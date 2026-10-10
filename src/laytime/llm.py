"""OpenRouter client: structured JSON answers, a disk cache, and a record of tokens and cost."""

import hashlib
import json
import logging
from pathlib import Path
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from laytime.settings import Settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text()


class CallRecord(BaseModel):
    label: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    cached: bool


class LLMClient:
    def __init__(self, settings: Settings):
        if settings.openrouter_api_key is None:
            raise ValueError("OPENROUTER_API_KEY is not set; add it to .env")
        self._client = OpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key.get_secret_value(),
        )
        self._temperature = settings.temperature
        self._max_retries = settings.max_retries
        self._cache_dir = settings.cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self.calls: list[CallRecord] = []

    def ask(self, *, model: str, system: str, user: str, schema: type[T], label: str) -> T:
        """Ask for an answer that matches `schema`.

        An answer that fails validation is sent back with the error, up to MAX_RETRIES times.
        Answers are cached by model, temperature, prompt, input and schema, so a re-run with
        nothing changed costs nothing and returns the same result.
        """
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "strict": True, "schema": schema.model_json_schema()},
        }
        key = _cache_key(model, self._temperature, system, user, response_format)
        cache_file = self._cache_dir / f"{key}.json"
        if cache_file.exists():
            entry = json.loads(cache_file.read_text())
            self._record(CallRecord(label=label, model=model, cached=True, **entry["usage"]))
            return schema.model_validate_json(entry["answer"])

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
        for attempt in range(self._max_retries + 1):
            response = self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=self._temperature,
                response_format=response_format,
            )
            usage = _add_usage(usage, response.usage)
            answer = response.choices[0].message.content or ""
            try:
                result = schema.model_validate_json(answer)
            except ValidationError as error:
                log.warning("answer did not match schema", extra={"stage": "llm", "label": label, "attempt": attempt + 1})
                messages += [
                    {"role": "assistant", "content": answer},
                    {"role": "user", "content": f"That answer is invalid:\n{error}\nReply again with JSON that matches the schema."},
                ]
                continue
            cache_file.write_text(json.dumps({"answer": answer, "usage": usage}))
            self._record(CallRecord(label=label, model=model, cached=False, **usage))
            return result
        raise RuntimeError(f"{label}: no valid answer from {model} after {self._max_retries + 1} attempts")

    def usage(self) -> dict:
        return {
            "llm_calls": len(self.calls),
            "cached_calls": sum(c.cached for c in self.calls),
            "prompt_tokens": sum(c.prompt_tokens for c in self.calls),
            "completion_tokens": sum(c.completion_tokens for c in self.calls),
            # What a run costs without the cache, so the figure is the same on every re-run.
            "cost_usd": round(sum(c.cost_usd for c in self.calls), 4),
        }

    def _record(self, call: CallRecord) -> None:
        self.calls.append(call)
        log.info(
            "llm answer",
            extra={"stage": "llm", **call.model_dump()},
        )


def _cache_key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:32]


def _add_usage(total: dict, usage) -> dict:
    if usage is None:
        return total
    # OpenRouter reports the charged cost alongside the token counts.
    cost = (usage.model_extra or {}).get("cost") or 0.0
    return {
        "prompt_tokens": total["prompt_tokens"] + usage.prompt_tokens,
        "completion_tokens": total["completion_tokens"] + usage.completion_tokens,
        "cost_usd": total["cost_usd"] + float(cost),
    }
