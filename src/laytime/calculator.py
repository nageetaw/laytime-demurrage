"""Laytime and demurrage arithmetic, following DOMAIN.md. Pure functions: no I/O, no LLM.

Time is counted in exact minutes (Decimal) and money is rounded to cents only at the end.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from pydantic import AwareDatetime

from laytime.fleet import PortCall
from laytime.models import (
    Check,
    Contract,
    Deduction,
    DeductionCategory,
    Demurrage,
    EventType,
    Fact,
    LaytimeResult,
    PortEvent,
    PortLaytime,
    Stoppage,
)

ONE = Decimal(1)
MINUTES_PER_DAY = Decimal(1440)


@dataclass
class _Period:
    """A deduction candidate, clipped to the laytime window."""

    category: DeductionCategory
    reason: str
    start: Fact[AwareDatetime]
    end: Fact[AwareDatetime]
    window_start: datetime
    window_end: datetime
    counts: Decimal  # share that still counts as laytime
    deducted_minutes: Decimal = Decimal(0)


@dataclass
class _Clock:
    """One laytime clock shared by all cargo ports (reversible laytime)."""

    allowance_minutes: Decimal
    exceptions_on_demurrage: bool
    used_minutes: Decimal = Decimal(0)
    on_demurrage_from: datetime | None = None

    def run(self, start: datetime, end: datetime, periods: list[_Period]) -> Decimal:
        """Count laytime from start to end and return the minutes used at this port."""
        before = self.used_minutes
        cuts = sorted({start, end, *(p.window_start for p in periods), *(p.window_end for p in periods)})
        for a, b in zip(cuts, cuts[1:]):
            active = [p for p in periods if p.window_start <= a and b <= p.window_end]
            # Overlapping deductions are not added up: the one that counts least applies.
            self._segment(a, b, min(active, key=lambda p: p.counts, default=None))
        return self.used_minutes - before

    def _segment(self, a: datetime, b: datetime, period: _Period | None) -> None:
        minutes = _minutes(a, b)
        share = period.counts if period and self._deductions_apply() else ONE
        remaining = self.allowance_minutes - self.used_minutes
        if self.on_demurrage_from is None and minutes * share > remaining:
            # The allowance runs out inside this segment.
            head = remaining / share
            self.on_demurrage_from = a + timedelta(minutes=float(head))
            self._count(period, head, share)
            minutes -= head
            share = period.counts if period and self._deductions_apply() else ONE
        self._count(period, minutes, share)

    def _deductions_apply(self) -> bool:
        return self.on_demurrage_from is None or self.exceptions_on_demurrage

    def _count(self, period: _Period | None, minutes: Decimal, share: Decimal) -> None:
        self.used_minutes += minutes * share
        if period:
            period.deducted_minutes += minutes * (ONE - share)


def calculate(
    contract: Contract,
    itinerary: list[PortCall],
    events: list[PortEvent],
    stoppages: list[Stoppage],
    now: datetime,
) -> LaytimeResult:
    """Laytime per cargo port and demurrage for the voyage.

    `now` is the latest time the inbox covers; a port whose laytime is still running
    is counted up to it and the result is provisional.
    """
    checks = _unsupported_terms(contract)
    end_type = EventType.HOSES_DISCONNECTED
    if contract.laytime_ends_on:
        end_type = contract.laytime_ends_on.value
    else:
        checks.append(Check(severity="info", message="Contract does not say when laytime ends; using hoses disconnected"))
    exceptions_on_demurrage = True
    if contract.exceptions_apply_on_demurrage:
        exceptions_on_demurrage = contract.exceptions_apply_on_demurrage.value
    else:
        checks.append(Check(severity="info", message="Contract does not say if deductions apply on demurrage; assuming they do"))

    clock = _Clock(
        allowance_minutes=Decimal(str(contract.laytime_allowed_hours.value)) * 60,
        exceptions_on_demurrage=exceptions_on_demurrage,
    )
    rate = Decimal(str(contract.demurrage_rate_per_day.value))
    final = True
    ports: list[PortLaytime] = []
    all_periods: list[tuple[str, _Period]] = []

    for call in (c for c in itinerary if c.is_cargo):
        port_events = {e.type: e for e in events if e.port == call.port}
        nor = port_events.get(EventType.NOR_TENDERED)
        if nor is None:
            final = False
            checks.append(Check(severity="info", message=f"{call.port}: no NOR tendered yet, no laytime counted"))
            continue
        start = nor.time.value + timedelta(hours=contract.turn_time_hours.value)
        end = port_events.get(end_type)
        counted_until = end.time.value if end else max(now, start)
        if end is None:
            final = False
            checks.append(Check(severity="info", message=f"{call.port}: laytime still running, counted up to {counted_until.isoformat()}"))

        periods = _periods(call.port, port_events, stoppages, contract, start, counted_until)
        used = clock.run(start, counted_until, periods)
        all_periods += [(call.port, p) for p in periods]
        ports.append(
            PortLaytime(
                port=call.port,
                function=call.function,
                nor_tendered=nor.time,
                turn_time_hours=contract.turn_time_hours.value,
                start=start,
                end=end.time if end else None,
                counted_until=counted_until,
                gross_hours=_hours(_minutes(start, counted_until)),
                deductions=[_deduction(p) for p in periods],
                used_hours=_hours(used),
            )
        )

    over = max(clock.used_minutes - clock.allowance_minutes, Decimal(0))
    amount = _usd(over, rate)
    checks += _deduction_checks(all_periods, contract, rate if over > 0 else Decimal(0))
    demurrage = Demurrage(
        status="final" if final else "provisional",
        allowed_hours=contract.laytime_allowed_hours.value,
        used_hours=_hours(clock.used_minutes),
        hours_on_demurrage=_hours(over),
        on_demurrage_from=clock.on_demurrage_from,
        rate_per_day=float(rate),
        currency=contract.currency,
        amount=float(amount),
        formula=f"{_hours(over)} h / 24 x {rate:,.2f} {contract.currency}/day = {amount:,.2f} {contract.currency}",
    )
    return LaytimeResult(ports=ports, demurrage=demurrage, checks=checks)


def _periods(
    port: str,
    port_events: dict[EventType, PortEvent],
    stoppages: list[Stoppage],
    contract: Contract,
    start: datetime,
    end: datetime,
) -> list[_Period]:
    candidates: list[tuple[DeductionCategory, str, Fact[AwareDatetime], Fact[AwareDatetime]]] = []
    aweigh, all_fast = port_events.get(EventType.ANCHOR_AWEIGH), port_events.get(EventType.ALL_FAST)
    if aweigh and all_fast and aweigh.time.value < all_fast.time.value:
        candidates.append((DeductionCategory.SHIFTING, "Shifting from anchorage to berth", aweigh.time, all_fast.time))
    candidates += [(s.category, s.reason, s.start, s.end) for s in stoppages if s.port == port]

    periods = []
    for category, reason, s_from, s_to in candidates:
        window_start, window_end = max(s_from.value, start), min(s_to.value, end)
        if window_start >= window_end:
            continue  # entirely outside the laytime window
        rule = contract.counts_as_laytime.get(category)
        periods.append(
            _Period(
                category=category,
                reason=reason,
                start=s_from,
                end=s_to,
                window_start=window_start,
                window_end=window_end,
                counts=Decimal(str(rule.value)) if rule else ONE,
            )
        )
    return periods


def _deduction(p: _Period) -> Deduction:
    return Deduction(
        reason=p.reason,
        category=p.category,
        start=p.start,
        end=p.end,
        hours=_hours(_minutes(p.window_start, p.window_end)),
        counts_as_laytime=float(p.counts),
        deducted_hours=_hours(p.deducted_minutes),
    )


def _deduction_checks(periods: list[tuple[str, _Period]], contract: Contract, rate: Decimal) -> list[Check]:
    """Stoppages whose cause decides money: ones no clause covers, and ones taken off the claim.

    `rate` is zero when the voyage is not on demurrage, so no USD impact is shown.
    """
    checks = []
    for port, p in periods:
        minutes = _minutes(p.window_start, p.window_end)
        sources = [p.start.source]
        if p.category not in contract.counts_as_laytime:
            checks.append(
                Check(
                    severity="medium",
                    message=f"{port}: no contract clause covers '{p.reason}' ({p.category}); counted in full",
                    sources=sources,
                    impact_usd=float(_usd(minutes, rate)) or None,
                )
            )
        elif p.deducted_minutes > 0 and p.category != DeductionCategory.SHIFTING:
            checks.append(
                Check(
                    severity="info",
                    message=f"{port}: {_hours(p.deducted_minutes)} h deducted for '{p.reason}' "
                    f"({p.category}); confirm the cause",
                    sources=sources,
                    impact_usd=float(_usd(p.deducted_minutes, rate)) or None,
                )
            )
    return checks


def _unsupported_terms(contract: Contract) -> list[Check]:
    checks = []
    if not contract.reversible.value:
        checks.append(Check(severity="high", message="Non-reversible laytime is not supported; computed as reversible",
                            sources=[contract.reversible.source]))
    if "SHINC" not in contract.basis.value.upper():
        checks.append(Check(severity="high", message=f"Laytime basis '{contract.basis.value}' is not supported; "
                            "every day was counted (SHINC)", sources=[contract.basis.source]))
    if not contract.pro_rata.value:
        checks.append(Check(severity="high", message="Demurrage is not pro rata; computed pro rata",
                            sources=[contract.pro_rata.source]))
    return checks


def _minutes(a: datetime, b: datetime) -> Decimal:
    return Decimal(int((b - a).total_seconds())) / 60


def _hours(minutes: Decimal) -> float:
    return float(round(minutes / 60, 2))


def _usd(minutes: Decimal, rate: Decimal) -> Decimal:
    return (minutes / MINUTES_PER_DAY * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
