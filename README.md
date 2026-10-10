# Laytime & demurrage from a shipping inbox

A pipeline that reads a vessel's voyage emails and answers, for the active voyage:
**where is the vessel, how much laytime was used at each port, and how much demurrage is owed —
with the source email for every number.**

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # then add your OPENROUTER_API_KEY
```