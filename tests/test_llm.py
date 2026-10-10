from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from laytime.llm import LLMClient
from laytime.settings import Settings


class Answer(BaseModel):
    value: int


class FakeCompletions:
    """Stands in for the OpenRouter API: returns the given replies in order and counts calls."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, model_extra={"cost": 0.001})
        message = SimpleNamespace(content=self.replies.pop(0))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def client(tmp_path, replies, max_retries=1):
    llm = LLMClient(Settings(openrouter_api_key="test", cache_dir=tmp_path, max_retries=max_retries))
    fake = FakeCompletions(replies)
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return llm, fake


def ask(llm):
    return llm.ask(model="m", system="s", user="u", schema=Answer, label="test")


def test_second_identical_call_comes_from_the_cache(tmp_path):
    llm, fake = client(tmp_path, ['{"value": 1}'])
    assert ask(llm) == ask(llm) == Answer(value=1)
    assert fake.calls == 1
    usage = llm.usage()
    assert (usage["llm_calls"], usage["cached_calls"]) == (2, 1)
    # Cost is reported as for an uncached run, so it does not change between re-runs.
    assert usage["cost_usd"] == 0.002


def test_invalid_answer_is_retried(tmp_path):
    llm, fake = client(tmp_path, ['{"value": "many"}', '{"value": 2}'])
    assert ask(llm) == Answer(value=2)
    assert fake.calls == 2


def test_gives_up_after_max_retries(tmp_path):
    llm, _ = client(tmp_path, ["not json", "still not json"], max_retries=1)
    with pytest.raises(RuntimeError, match="no valid answer"):
        ask(llm)


def test_missing_api_key_is_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        LLMClient(Settings(openrouter_api_key=None, cache_dir=tmp_path, _env_file=None))
