"""Gemini client: structured output, retry, caching, and audit logging.

All LLM traffic goes through this module so that every call is logged with its
prompt version and input hash (brief section 22), and so repeated calls on the
same input hit the cache instead of the free-tier rate limit.
"""

from __future__ import annotations

import hashlib
import json
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
_FLASH_PREFERENCES = ("flash-latest", "2.5-flash", "2.0-flash", "flash")


class LLMError(RuntimeError):
    pass


class RateLimited(LLMError):
    pass


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

    def list_models(self) -> list[str]:
        return [
            m.name.removeprefix("models/")
            for m in self.client.models.list()
            if "generateContent" in (m.supported_actions or [])
        ]

    def resolve_model(self) -> str:
        """Confirm the configured model exists, else fall back to the best Flash."""
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

    @retry(
        retry=retry_if_exception_type(RateLimited),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _generate(self, prompt_text: str, system: str, schema: type[BaseModel]) -> str:
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
            if any(s in str(exc).lower() for s in ("429", "quota", "resource_exhausted")):
                raise RateLimited(str(exc)) from exc
            raise LLMError(str(exc)) from exc
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
        page = f" p{chunk.page}" if chunk.page else ""
        lines.append(f"[{doc.chunk_key(section, chunk)}{page}] {chunk.text}")
    return "\n".join(lines)
