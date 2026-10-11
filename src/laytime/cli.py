"""Command line: `laytime run` and `laytime eval`."""

import argparse
import json
import random
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

import openai

from laytime import pipeline, report
from laytime.evaluate import Line, audit_citations, fingerprint, score
from laytime.ingest import read_inbox
from laytime.llm import LLMClient
from laytime.settings import Settings, get_settings, setup_logging


def main(argv: list[str] | None = None) -> None:
    inputs = argparse.ArgumentParser(add_help=False)
    inputs.add_argument("--emails", type=Path, default=Path("emails"), help="folder of .eml files")
    inputs.add_argument("--fleet", type=Path, default=Path("fleet.json"), help="fleet and voyage file")

    parser = argparse.ArgumentParser(prog="laytime", description="Laytime and demurrage from a shipping inbox.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", parents=[inputs], help="run the pipeline and write result.json and brief.md")
    run.add_argument("--out", type=Path, default=Path("output"), help="output folder")
    evaluate = commands.add_parser("eval", parents=[inputs], help="run the pipeline and score it against the answer key")
    evaluate.add_argument("--gold", type=Path, default=Path("eval/gold.json"), help="answer key")
    evaluate.add_argument("--runs", type=Path, default=Path("eval/runs"), help="folder for scorecards")
    args = parser.parse_args(argv)

    settings = get_settings()
    setup_logging(settings.log_level)
    llm = LLMClient(settings)
    try:
        if args.command == "run":
            _run(args, settings, llm)
        else:
            _eval(args, settings, llm)
    except openai.APIStatusError as error:
        # e.g. a model this API key may not use, or no credit left: say so in one line.
        message = error.body.get("message") if isinstance(error.body, dict) else None
        raise SystemExit(f"OpenRouter refused the request (HTTP {error.status_code}): {message or error.message}")


def _run(args: argparse.Namespace, settings: Settings, llm: LLMClient) -> None:
    for r in pipeline.run(args.emails, args.fleet, settings, llm):
        result = report.build(r, settings, llm.usage())
        folder = report.write(result, args.out / result.summary.voyage)
        print(f"\nWrote {folder}/result.json and {folder}/brief.md\n")
        print((folder / "brief.md").read_text())


def _eval(args: argparse.Namespace, settings: Settings, llm: LLMClient) -> None:
    gold = json.loads(args.gold.read_text())
    built = _result(args.emails, args.fleet, gold["voyage"], settings, llm)
    result = json.loads(built.model_dump_json())
    usage = llm.usage()
    emails = {e.file: e for e in read_inbox(args.emails)}
    lines = score(result, gold) + audit_citations(result, emails)

    # Same inbox under random file names and a shuffled file order: every number must match.
    # The LLM never sees file names, so this run is served entirely from the cache.
    with tempfile.TemporaryDirectory() as tmp:
        files = sorted(args.emails.glob("*.eml"))
        random.Random(0).shuffle(files)
        for i, path in enumerate(files):
            shutil.copy(path, Path(tmp) / f"msg_{i:04d}.eml")
        renamed = json.loads(_result(Path(tmp), args.fleet, gold["voyage"], settings, llm).model_dump_json())
    lines.append(Line("independence", "same answer with renamed, reordered email files", True,
                      fingerprint(result) == fingerprint(renamed)))

    sections: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for line in lines:
        sections[line.section][0] += line.ok
        sections[line.section][1] += 1
    failures = [line for line in lines if not line.ok]

    print(f"\nScorecard  {gold['voyage']}  triage={settings.triage_model}  extract={settings.extract_model}")
    for section, (passed, total) in sections.items():
        print(f"  {section:13} {passed:3}/{total:<3} {'ok' if passed == total else 'FAIL'}")
    print(f"  {'cost':13} ${usage['cost_usd']:.4f} for a full run; ${usage['spent_usd']:.4f} spent now "
          f"({usage['llm_calls']} LLM calls, {usage['cached_calls']} from cache)")
    print(f"  {'time':13} {usage['llm_seconds']:.1f} s of LLM time for a full run; "
          f"slowest call {usage['slowest_call_seconds']:.1f} s")
    for line in failures:
        print(f"  FAIL  {line.section}: {line.item}: expected {line.expected!r}, got {line.actual!r}")

    # One folder per voyage and model pair, keeping each model's output next to its score.
    name = f"{gold['voyage']}__{settings.triage_model}__{settings.extract_model}"
    folder = report.write(built, args.runs / name.replace("/", "_").replace(":", "-"))
    (folder / "scorecard.json").write_text(json.dumps({
        "models": {"triage": settings.triage_model, "extract": settings.extract_model},
        "usage": usage,
        "sections": {s: {"passed": p, "total": t} for s, (p, t) in sections.items()},
        "failures": [{"section": f.section, "item": f.item, "expected": f.expected, "actual": f.actual}
                     for f in failures],
    }, indent=2, default=str) + "\n")
    print(f"\nWrote {folder}/scorecard.json, result.json and brief.md")
    if failures:
        raise SystemExit(1)


def _result(emails: Path, fleet: Path, voyage: str, settings: Settings, llm: LLMClient) -> report.Result:
    results = pipeline.run(emails, fleet, settings, llm)
    r = next(r for r in results if r.vessel.current_voyage.voyage_no == voyage)
    return report.build(r, settings, llm.usage())
