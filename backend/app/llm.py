"""Language model access through OpenRouter's chat completions API.

Free models come and go and share one daily quota, so this module tries the configured models in
order, spends as few requests as possible, and reports failures precisely enough for the
interface to tell the student what actually happened.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from .config import Settings

logger = logging.getLogger(__name__)

MISTAKE_TYPES = [
    "distribution_partial",
    "distribution_sign",
    "transposition_sign",
    "one_sided_operation",
    "inverse_operation",
    "inverted_division",
    "arithmetic_slip",
    "conceptual",
]

RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["message", "reasoning_mistake", "new_problem"],
    "properties": {
        "message": {"type": "string"},
        "reasoning_mistake": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["type", "explanation"],
                    "properties": {
                        "type": {"type": "string", "enum": MISTAKE_TYPES},
                        "explanation": {"type": "string"},
                    },
                },
            ]
        },
        "new_problem": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["equation"],
                    "properties": {"equation": {"type": "string"}},
                },
            ]
        },
    },
}


class LLMError(Exception):
    """The model could not answer. ``code`` tells the interface what to say."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code  # daily_limit | rate_limited | auth | unavailable | not_configured


@dataclass(frozen=True)
class LLMResult:
    data: dict
    model: str


class LLMClient(Protocol):
    @property
    def label(self) -> str: ...

    def complete(self, messages: list[dict[str, str]]) -> LLMResult: ...


def parse_model_output(content: str) -> dict:
    """Read a model's JSON reply, tolerating the wrapping weaker models tend to add."""
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL)
    candidate = fenced.group(1) if fenced else cleaned
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(candidate[start : end + 1])
            if isinstance(parsed, dict) and isinstance(parsed.get("message"), str):
                return parsed
        except json.JSONDecodeError:
            pass
    # A plain-text answer is still a usable tutor reply; it just carries no structured extras.
    return {"message": cleaned, "reasoning_mistake": None, "new_problem": None}


def _merge_system_into_user(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Some models refuse the system role, so its text moves into the first user message."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    rest = [dict(m) for m in messages if m["role"] != "system"]
    if rest and system:
        rest[0]["content"] = f"{system}\n\n{rest[0]['content']}"
    return rest


class OpenRouterClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.client = httpx.Client(
            base_url=settings.openrouter_base_url,
            timeout=settings.llm_timeout_seconds,
            transport=transport,
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "HTTP-Referer": settings.public_url,
                "X-Title": "AI Math Tutor",
            },
        )

    @property
    def label(self) -> str:
        return "openrouter"

    def complete(self, messages: list[dict[str, str]]) -> LLMResult:
        if not self.settings.openrouter_api_key:
            raise LLMError("not_configured", "OPENROUTER_API_KEY is not set")
        last_error = LLMError("unavailable", "No model is configured")
        started = time.monotonic()
        # Free models are often briefly rate-limited upstream. One more pass after a short
        # pause usually succeeds, but only while the student has not been waiting too long.
        for attempt in range(2):
            if attempt:
                if time.monotonic() - started > self.settings.llm_retry_window_seconds:
                    break
                time.sleep(self.settings.llm_retry_pause_seconds)
            for model in self.settings.model_list:
                try:
                    return self._ask_model(model, messages)
                except LLMError as error:
                    # Daily quota and credentials are account-wide: other models would fail too.
                    if error.code in ("daily_limit", "auth"):
                        raise
                    logger.warning("Model %s failed: %s", model, error)
                    last_error = error
        raise last_error

    def _ask_model(self, model: str, messages: list[dict[str, str]]) -> LLMResult:
        schema_format = {
            "type": "json_schema",
            "json_schema": {"name": "tutor_turn", "strict": True, "schema": RESPONSE_SCHEMA},
        }
        # A 400 means this model rejects a request feature, and is not charged to the quota.
        attempts = (
            (messages, schema_format, True),
            (messages, None, False),
            (_merge_system_into_user(messages), None, False),
        )
        error = LLMError("unavailable", "No attempt was made")
        for attempt_messages, response_format, brief_reasoning in attempts:
            body: dict = {
                "model": model,
                "messages": attempt_messages,
                "max_tokens": self.settings.llm_max_tokens,
            }
            if response_format:
                body["response_format"] = response_format
            if brief_reasoning:
                # Reasoning models can spend the whole token budget thinking and return an
                # empty answer; a tutor's turn needs little deliberation.
                body["reasoning"] = {"effort": "low"}
            try:
                response = self.client.post("/chat/completions", json=body)
            except httpx.HTTPError as exc:
                raise LLMError("unavailable", f"{type(exc).__name__}") from exc
            if response.status_code == 400:
                error = LLMError("unavailable", f"400: {response.text[:300]}")
                continue
            return self._read_response(model, response)
        raise error

    def _read_response(self, model: str, response: httpx.Response) -> LLMResult:
        if response.status_code in (401, 403):
            raise LLMError("auth", f"{response.status_code}: {response.text[:300]}")
        if response.status_code == 402:
            raise LLMError("daily_limit", "402: account has no available credit")
        if response.status_code == 429:
            text = response.text.lower()
            if "per-day" in text or "per day" in text or "per_day" in text:
                raise LLMError("daily_limit", response.text[:300])
            raise LLMError("rate_limited", response.text[:300])
        if response.status_code >= 400:
            raise LLMError("unavailable", f"{response.status_code}: {response.text[:300]}")
        try:
            payload = response.json()
            if "error" in payload:
                raise LLMError("unavailable", str(payload["error"])[:300])
            choice = payload["choices"][0]
            content = choice["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("unavailable", "Malformed response") from exc
        data = parse_model_output(content)
        if not data["message"].strip():
            reason = choice.get("finish_reason")
            raise LLMError("unavailable", f"Empty response (finish_reason={reason})")
        return LLMResult(data=data, model=payload.get("model", model))


class StubClient:
    """Offline stand-in that follows the engine's directive; never used unless configured."""

    @property
    def label(self) -> str:
        return "stub"

    def complete(self, messages: list[dict[str, str]]) -> LLMResult:
        request = json.loads(messages[-1]["content"])
        facts = request["engine_facts"]
        russian = facts["reply_language"] == "Russian"
        text = {
            "diagnose_mistake": (
                "[stub] Посмотри на шаг ещё раз. Что именно умножается на множитель перед скобкой?",
                "[stub] Look at that step again. What exactly does the factor outside the bracket "
                "multiply?",
            ),
            "mistake_corrected": (
                "[stub] Верно, этот шаг исправлен. Что делаем дальше?",
                "[stub] Correct, that step is fixed. What do we do next?",
            ),
            "acknowledge_progress": (
                "[stub] Верный шаг. Почему так можно? Что дальше?",
                "[stub] A valid step. Why is it allowed? What comes next?",
            ),
            "ask_substitution_check": (
                "[stub] Теперь проверка: подставь свой ответ в исходное уравнение. Что получается?",
                "[stub] Now verify it: substitute your answer into the original equation. "
                "What do both sides equal?",
            ),
            "guide_substitution_check": (
                "[stub] Вычисли левую и правую части отдельно. Какие числа получились?",
                "[stub] Work out the left and right sides separately. Which numbers do you get?",
            ),
            "complete_problem": (
                "[stub] Проверка сошлась, задача решена. Вот следующая.",
                "[stub] The check works out, so the problem is solved. Here is the next one.",
            ),
        }.get(
            facts["directive"],
            (
                "[stub] Запиши, пожалуйста, свой следующий шаг в виде уравнения.",
                "[stub] Please write your next step as an equation.",
            ),
        )
        return LLMResult(
            data={
                "message": text[0] if russian else text[1],
                "reasoning_mistake": None,
                "new_problem": None,
            },
            model="stub",
        )


def build_client(settings: Settings) -> LLMClient:
    return StubClient() if settings.llm_provider == "stub" else OpenRouterClient(settings)
