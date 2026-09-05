"""Model fallback and error classification.

A Flash model can stay overloaded for minutes while a sibling answers instantly.
Losing a run to that is avoidable, so the client walks a candidate list — and
the audit log records which model actually served the request.
"""

import pytest

from app.llm.client import (
    GeminiClient,
    LLMError,
    QuotaExhausted,
    TransientLLMError,
    _hash_input,
)

BUSY = (
    "503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently "
    "experiencing high demand.', 'status': 'UNAVAILABLE'}}"
)
OUT_OF_QUOTA = (
    "429 RESOURCE_EXHAUSTED. Quota exceeded for metric: generate_content_free_tier_requests, "
    "quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"
)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.llm.client.genai.Client", lambda **kwargs: object())
    instance = GeminiClient(api_key="test-key", model="gemini-3.7-flash")
    instance._model_resolved = True
    return instance


def _only_this_model_works(client, working: str, error: Exception) -> list[str]:
    """Stub generation so every model raises except `working`."""
    calls: list[str] = []

    def fake(prompt_text, system, schema):
        calls.append(client.model)
        if client.model == working:
            return '{"ok": true}'
        raise error

    client._generate_once = fake  # type: ignore[method-assign]
    return calls


def test_a_busy_model_falls_back_to_one_that_answers(client):
    # Reproduces a real failure: the configured model returned 503 for minutes
    # while a sibling answered instantly.
    calls = _only_this_model_works(client, "gemini-3.6-flash", TransientLLMError(BUSY))

    assert client._generate("prompt", "system", dict) == '{"ok": true}'
    assert calls[0] == "gemini-3.7-flash", "the configured model is tried first"
    assert client.model == "gemini-3.6-flash", "audit must record the model that served"


def test_quota_on_one_model_moves_to_the_next(client):
    calls = _only_this_model_works(
        client, "gemini-flash-latest", QuotaExhausted(OUT_OF_QUOTA)
    )
    assert client._generate("p", "s", dict) == '{"ok": true}'
    assert len(calls) == 3, "should exhaust the earlier candidates first"


def test_every_model_failing_reports_what_was_tried(client):
    def fake(prompt_text, system, schema):
        raise TransientLLMError(BUSY)

    client._generate_once = fake  # type: ignore[method-assign]
    with pytest.raises(LLMError, match="Every candidate model"):
        client._generate("p", "s", dict)


def test_the_configured_model_is_always_tried_first(client):
    client.model = "gemini-3.6-flash"
    assert client._model_candidates()[0] == "gemini-3.6-flash"
    assert len(set(client._model_candidates())) == len(client._model_candidates())


def test_input_hash_distinguishes_model_and_schema():
    # The cache is keyed on this; colliding keys would serve one model's output
    # as another's.
    base = _hash_input("prompt", "model-a", "SchemaA")
    assert base != _hash_input("prompt", "model-b", "SchemaA")
    assert base != _hash_input("prompt", "model-a", "SchemaB")
    assert base == _hash_input("prompt", "model-a", "SchemaA")
