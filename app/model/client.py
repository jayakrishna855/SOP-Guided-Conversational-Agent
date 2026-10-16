from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings
from app.model.extraction import extraction_json_schema
from app.workflow.state import Phase


class ModelError(RuntimeError):
    """The model couldn't be called or returned nothing usable."""


@dataclass(frozen=True)
class ExtractRequest:
    message: str
    phase: Phase
    history: list[tuple[str, str]]  # (role, text), oldest first, current message excluded
    followup_topics: list[str]  # the only values allowed for followup_topic


@dataclass(frozen=True)
class ReplyStep:
    action: str
    # Everything the reply may state. Before verification this holds no claim
    # data at all, only field names and the like.
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReplyRequest:
    message: str
    phase: Phase
    steps: list[ReplyStep]
    fallback: str  # the deterministic template for these steps
    history: list[tuple[str, str]]


class ModelAdapter(Protocol):
    def extract(self, request: ExtractRequest) -> str:
        """Raw JSON text for the extraction schema (parsed and validated by the caller)."""

    def respond(self, request: ReplyRequest) -> str:
        """The caller-facing reply."""


class KeyCheckError(Exception):
    """A caller-supplied key can't be used. `message` is safe to show: it never
    includes OpenAI's error text, which can echo part of the key."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


PROMPTS_DIR = Path(__file__).parent / "prompts"
_REPLY_PROMPTS = {
    Phase.VERIFY_ID: "verify.md",
    Phase.RESOLVE_INTENT: "resolve.md",
    Phase.PROCESS_CASE: "process.md",
    Phase.POST_PROCESS: "post.md",
    Phase.END: "post.md",
}
EXTRACT_HISTORY = 4  # recent entries; enough to read "yes" or "the second one"
REPLY_HISTORY = 6
TIMEOUT_SECONDS = 30.0


@cache
def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


def _messages(history: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"role": "user" if role == "caller" else "assistant", "content": text} for role, text in history]


class OpenAIModel:
    """OpenAI Responses API adapter. Extraction uses strict structured outputs
    (JSON schema); replies are plain text. Nothing is stored server-side by
    OpenAI (`store=False`), and the key is only ever handed to the SDK client.

    One instance per key: the server's OPENAI_API_KEY, or a key a reviewer
    entered for their session."""


    def __init__(self, settings: Settings, api_key: str):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=1)
        self._model = settings.openai_model
        self._extract_model = settings.extract_model
        self._reasoning = {"effort": settings.openai_reasoning_effort} if settings.openai_reasoning_effort else None
        # Read once at startup so a missing prompt fails fast.
        self._extract_prompt = load_prompt("extract.md")
        self._reply_base = load_prompt("reply_base.md")
        self._reply_prompts = {phase: load_prompt(name) for phase, name in _REPLY_PROMPTS.items()}

    def check_key(self) -> None:
        """One tiny call, so a bad key, a model the key can't use or an account
        without credits fails here with a clear message instead of mid-chat."""
        import openai

        try:
            self._client.responses.create(model=self._model, input="Reply with OK.", max_output_tokens=16,
                                          store=False)
        except openai.AuthenticationError:
            raise KeyCheckError(401, "That OpenAI API key wasn't accepted. Check it and try again.") from None
        except (openai.PermissionDeniedError, openai.NotFoundError):
            raise KeyCheckError(403, f"That key can't use the model {self._model}.") from None
        except openai.RateLimitError:
            raise KeyCheckError(429, "OpenAI says this key is out of credits or rate-limited. "
                                     "Check its billing, or try again in a minute.") from None
        except openai.OpenAIError:
            raise KeyCheckError(502, "Couldn't reach OpenAI to check the key. Please try again.") from None

    def _create(self, **kwargs) -> str:
        if self._reasoning:
            kwargs["reasoning"] = self._reasoning
        response = self._client.responses.create(store=False, **kwargs)
        text = (response.output_text or "").strip()
        if not text:
            raise ModelError("empty model output")
        return text

    def extract(self, request: ExtractRequest) -> str:
        return self._create(
            model=self._extract_model,
            instructions=self._extract_prompt.replace("{phase}", request.phase.value),
            input=[*_messages(request.history[-EXTRACT_HISTORY:]), {"role": "user", "content": request.message}],
            text={"format": {
                "type": "json_schema",
                "name": "extraction",
                "schema": extraction_json_schema(request.followup_topics),
                "strict": True,
            }},
        )

    def respond(self, request: ReplyRequest) -> str:
        directive = {
            "steps": [{"action": s.action, "facts": s.facts} for s in request.steps],
            "reference_reply": request.fallback,
        }
        return self._create(
            model=self._model,
            instructions=f"{self._reply_base}\n\n{self._reply_prompts[request.phase]}",
            input=[
                *_messages(request.history[-REPLY_HISTORY:]),
                {"role": "user", "content": request.message},
                {"role": "developer", "content": json.dumps(directive, ensure_ascii=False, default=str)},
            ],
        )
