"""Gemini client: structured output, retry, caching, and audit logging.

All LLM traffic goes through this module so that every call is logged with its
prompt version and input hash (brief section 22), and so repeated calls on the
same input hit the cache instead of the free-tier rate limit.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel
from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.llm.prompts import Prompt
from app.models import LLMRequest

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Preference order when the configured model is unavailable. The brief named
# "Gemini 3.7 Flash", which is not a real model id, so the model is resolved
# against what the API actually offers rather than trusted from config.
_FLASH_PREFERENCES = ("flash-latest", "3.7-flash", "3.6-flash", "flash")

# Tried in order when the configured model is overloaded or out of quota.
# Google's Flash models share capacity unevenly: one can return 503 for minutes
# while a sibling answers immediately.
_FALLBACK_MODELS = ("gemini-3.7-flash", "gemini-3.6-flash", "gemini-flash-latest")


class LLMError(RuntimeError):
    pass


class TransientLLMError(LLMError):
    """Retryable: rate limits and the API's own temporary failures."""


class QuotaExhausted(LLMError):
    """The daily quota is gone. Retrying will not help until it resets."""


# Quota exhaustion and "model overloaded" both resolve by waiting, so both are
# retried. A 503 during extraction is common on popular models and must not
# surface to the user as a failed upload.
_TRANSIENT_MARKERS = (
    "429",
    "quota",
    "resource_exhausted",
    "500",
    "502",
    "503",
    "504",
    "unavailable",
    "overloaded",
    "high demand",
    "internal error",
)

# A per-day quota does not come back within a retry window. Backing off five
# times against it wastes minutes and hides the real problem from the caller.
_DAILY_QUOTA_MARKERS = ("perday", "per day", "requestsperday", "freetier")


def _hash_input(prompt_text: str, model: str, schema_name: str) -> str:
    return hashlib.sha256(f"{model}|{schema_name}|{prompt_text}".encode()).hexdigest()


class GeminiClient:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        settings = get_settings()
        self.api_key = api_key or settings.gemini_api_key
        if not self.api_key or self.api_key.startswith("your-"):
            raise LLMError(
                "GEMINI_API_KEY is not set. Add your Google AI Studio key to .env "
                "before running extraction."
            )
        self.client = genai.Client(api_key=self.api_key)
        self.model = model or settings.gemini_model
        self._model_resolved = False

    def list_models(self) -> list[str]:
        return [
            m.name.removeprefix("models/")
            for m in self.client.models.list()
            if "generateContent" in (m.supported_actions or [])
        ]

    def resolve_model(self) -> str:
        """Confirm the configured model exists, else fall back to the best Flash."""
        self._model_resolved = True
        available = self.list_models()
        if self.model in available:
            return self.model
        for preference in _FLASH_PREFERENCES:
            for name in available:
                if preference in name and "thinking" not in name:
                    logger.warning(
                        "Configured model %r unavailable; using %r", self.model, name
                    )
                    self.model = name
                    return name
        raise LLMError(f"No usable Gemini model found. Available: {available[:10]}")

    def _generate(self, prompt_text: str, system: str, schema: type[BaseModel]) -> str:
        """Generate, retrying transient failures and switching model if needed.

        A popular Flash model can stay overloaded for minutes at a time. Failing
        the whole run because one model is busy — when a sibling model is idle —
        makes the pipeline unusable at exactly the times it is most wanted. The
        model that actually served the request is recorded in the audit log, so
        a fallback is visible rather than silent.
        """
        if not self._model_resolved:
            self.resolve_model()

        original = self.model
        tried: list[str] = []
        for candidate in self._model_candidates():
            self.model = candidate
            tried.append(candidate)
            try:
                return self._generate_once(prompt_text, system, schema)
            except QuotaExhausted:
                logger.warning("Daily quota exhausted for %r; trying another model", candidate)
                continue
            except TransientLLMError:
                logger.warning("%r still unavailable after retries; trying another model", candidate)
                continue

        self.model = original
        raise LLMError(
            f"Every candidate model was unavailable or out of quota (tried: {', '.join(tried)}). "
            "Google's Flash models are shared and can be busy for minutes at a time; "
            "wait and retry, or set GEMINI_MODEL to a model with capacity."
        )

    def _model_candidates(self) -> list[str]:
        """The configured model first, then siblings to fall back to."""
        candidates = [self.model]
        for name in _FALLBACK_MODELS:
            if name not in candidates:
                candidates.append(name)
        return candidates

    @retry(
        retry=retry_if_exception_type(TransientLLMError),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _generate_once(self, prompt_text: str, system: str, schema: type[BaseModel]) -> str:
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt_text,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0.1,
                ),
            )
        except Exception as exc:  # google-genai raises transport-specific errors
            message = str(exc)
            lowered = message.lower().replace("_", "")
            if any(marker in lowered for marker in _DAILY_QUOTA_MARKERS) and "429" in lowered:
                raise QuotaExhausted(
                    "The Gemini free-tier daily request quota for "
                    f"{self.model} is exhausted. It resets on Google's daily "
                    "schedule; until then, run the pipeline with a stubbed "
                    "client or a model with remaining quota."
                ) from exc
            if any(marker in message.lower() for marker in _TRANSIENT_MARKERS):
                logger.warning("Transient Gemini error, will retry: %s", message[:200])
                raise TransientLLMError(message) from exc
            raise LLMError(message) from exc
        if not response.text:
            raise LLMError("Gemini returned an empty response")
        return response.text

    def generate_structured(
        self,
        prompt: Prompt,
        schema: type[T],
        db: Session | None = None,
        project_id: str | None = None,
        graph_node: str = "unknown",
        use_cache: bool = True,
        **template_kwargs,
    ) -> T:
        """Run a prompt and parse the result into `schema`, logging the exchange."""
        prompt_text = prompt.render(**template_kwargs)
        input_hash = _hash_input(prompt_text, self.model, schema.__name__)

        if use_cache and db is not None:
            cached = (
                db.query(LLMRequest)
                .filter(LLMRequest.input_hash == input_hash)
                .order_by(LLMRequest.created_at.desc())
                .first()
            )
            if cached:
                logger.info("LLM cache hit for %s (%s)", graph_node, input_hash[:8])
                return schema.model_validate_json(cached.output)

        started = time.monotonic()
        raw = self._generate(prompt_text, prompt.system, schema)
        latency_ms = int((time.monotonic() - started) * 1000)

        if db is not None:
            db.add(
                LLMRequest(
                    project_id=project_id,
                    graph_node=graph_node,
                    model=self.model,
                    prompt_version=f"{prompt.name}:{prompt.version}",
                    input_hash=input_hash,
                    output=raw,
                    latency_ms=latency_ms,
                )
            )
            db.commit()

        try:
            return schema.model_validate_json(raw)
        except Exception as exc:
            raise LLMError(f"Gemini output failed {schema.__name__} validation: {exc}") from exc


def build_chunked_text(doc) -> str:
    """Render a ParsedDocument as keyed chunks the model can cite verbatim."""
    lines = []
    current_section = None
    for section, chunk in doc.iter_chunks():
        if section is not current_section:
            lines.append(f"\n## {section.title}")
            current_section = section
        # The page must sit outside the bracket: anything inside gets copied
        # verbatim as the citation key, which then matches no chunk at all.
        page = f"(page {chunk.page}) " if chunk.page else ""
        lines.append(f"[{doc.chunk_key(section, chunk)}] {page}{chunk.text}")
    return "\n".join(lines)
