"""Command line: `laytime run`."""

import argparse
from pathlib import Path

from laytime import pipeline, report
from laytime.llm import LLMClient
from laytime.settings import get_settings, setup_logging


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="laytime", description="Laytime and demurrage from a shipping inbox.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run the pipeline on an inbox and write result.json and brief.md")
    run.add_argument("--emails", type=Path, default=Path("emails"), help="folder of .eml files")
    run.add_argument("--fleet", type=Path, default=Path("fleet.json"), help="fleet and voyage file")
    run.add_argument("--out", type=Path, default=Path("output"), help="output folder")
    args = parser.parse_args(argv)

    settings = get_settings()
    setup_logging(settings.log_level)
    llm = LLMClient(settings)
    results = pipeline.run(args.emails, args.fleet, settings, llm)
    for r in results:
        folder = report.write(report.build(r, settings, llm.usage()), args.out)
        print(f"\nWrote {folder}/result.json and {folder}/brief.md\n")
        print((folder / "brief.md").read_text())
