"""Score a run's result.json against the eval/gold.json that is created by claude model  opus 5.5 and verified by this repository authors.
Note: The author's Domain knowlege is also limited so thier can be some mistakes in gold set too.
"""

import json
from dataclasses import dataclass
from datetime import datetime

from laytime.models import Email
from laytime.normalise import quote_found


@dataclass
class Line:
    section: str
    item: str
    expected: object
    actual: object

    @property
    def ok(self) -> bool:
        return _same(self.expected, self.actual)


def score(result: dict, gold: dict) -> list[Line]:
    lines = []
    rows = {r["file"]: r for r in result["emails"]}
    for file, want in gold["emails"].items():
        row = rows.get(file, {})
        lines.append(Line("emails", f"{file} vessels", want["vessels"], row.get("vessels")))
        lines.append(Line("emails", f"{file} relevance", want["relevance"], row.get("relevance")))
        if want["doc_type"] is not None:
            lines.append(Line("emails", f"{file} doc_type", want["doc_type"], row.get("doc_type")))

    contract = result.get("contract") or {}
    for name, want in gold["contract"].items():
        if name == "counts_as_laytime":
            counts = contract.get("counts_as_laytime", {})
            for category, share in want.items():
                lines.append(Line("contract", f"rule {category}", share, _value(counts.get(category))))
        else:
            lines.append(Line("contract", name, want, _value(contract.get(name))))

    calls = {c["port"]: c for c in result["port_calls"]}
    for port, want in gold["port_calls"].items():
        for event in ("arrived", "sailed"):
            lines.append(Line("port calls", f"{port} {event}", want[event], _value(calls.get(port, {}).get(event))))

    ports = {p["port"]: p for p in result["laytime"]}
    for port, want in gold["laytime"].items():
        got = ports.get(port, {})
        for name in ("nor_tendered", "start", "end", "used_hours"):
            lines.append(Line("laytime", f"{port} {name}", want[name], _value(got.get(name))))
        deducted = {}
        for d in got.get("deductions", []):
            if d["deducted_hours"]:
                deducted[d["category"]] = deducted.get(d["category"], 0) + d["deducted_hours"]
        lines.append(Line("laytime", f"{port} deducted hours", want["deducted_hours"], deducted))

    demurrage = result.get("demurrage") or {}
    for name, want in gold["demurrage"].items():
        lines.append(Line("demurrage", name, want, demurrage.get(name)))

    for required in gold["required_checks"]:
        found = any(set(required["sources"]) <= set(c["sources"]) for c in result["checks"])
        lines.append(Line("checks", required["about"], True, found))
    return lines


def audit_citations(result: dict, emails: dict[str, Email]) -> list[Line]:
    """Every source and quote in result.json must appear in that email's text."""
    lines = []
    for fact in _facts(result):
        email = emails.get(fact["source"])
        found = email is not None and quote_found(fact["quote"], email)
        lines.append(Line("citations", f'{fact["source"]}: "{fact["quote"][:50]}"', True, found))
    return lines


def fingerprint(result: dict) -> str:
    """Every number and time in the answer, without file names, for comparing two runs."""
    def strip(obj):
        if isinstance(obj, dict):
            return {k: strip(v) for k, v in obj.items()
                    if k not in ("source", "quote", "also_reported_in", "replaces", "sources", "file")}
        if isinstance(obj, list):
            return [strip(v) for v in obj]
        return obj

    keep = {k: result[k] for k in ("summary", "contract", "laytime", "demurrage", "port_calls")}
    return json.dumps(strip(keep), sort_keys=True)


def _facts(obj):
    if isinstance(obj, dict):
        if "source" in obj and "quote" in obj:
            yield obj
        for value in obj.values():
            yield from _facts(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _facts(value)


def _value(fact):
    return fact["value"] if isinstance(fact, dict) and "value" in fact else fact


def _same(expected, actual) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(expected - actual) < 0.01
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(_same(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, str) and isinstance(actual, str):
        try:
            return datetime.fromisoformat(expected) == datetime.fromisoformat(actual)
        except ValueError:
            return expected.casefold() == actual.casefold()
    return expected == actual
