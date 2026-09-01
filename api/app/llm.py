"""Thin wrapper around the chat-completions call, plus its failure taxonomy.

**Why the OpenAI SDK against Groq.** Groq serves an OpenAI-*compatible* API, so
the official `openai` client talks to it with nothing changed but `base_url`.
That makes the provider a configuration detail rather than a code dependency:
Groq today (free tier, very fast), OpenAI or Together or a local Ollama
tomorrow, one env var either way. A vendor SDK would have bought nothing here and
pinned the choice into every call site.

**Why the failures are typed.** Three things go wrong in practice and they need
three different answers, so the caller should not be reading exception strings:

| What happened                  | Class            | HTTP | Why that code |
|--------------------------------|------------------|------|---------------|
| no API key configured          | `LlmNotConfigured` | 503 | the feature isn't set up yet, and the fix is the user's to make |
| Groq's free-tier rate limit    | `LlmRateLimited`   | 429 | genuinely retryable, and the client should say "in a moment" |
| timeout, 5xx, bad request, ... | `LlmUnavailable`   | 502 | an upstream we depend on failed |

The HTTP status lives on the exception class because there is exactly one
sensible mapping per failure, and `main.py` registers a single handler for the
base class rather than a try/except per call site.

**What never reaches the client.** The API key. `str(exc)` from the SDK can
include request context, so the messages below are written here, and the
provider's own text is only ever included when it is a rate-limit note. Nothing
formats an exception straight into a response body.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import openai
from fastapi import status

from .config import get_settings

# Shown verbatim in the 503 body and rendered by the web UI's setup empty state.
# One place, so the API and the dashboard can't drift on the instructions.
SETUP_HINT = (
    "AI chat is not configured: this server has no GROQ_API_KEY. "
    "Get a free key at https://console.groq.com, add GROQ_API_KEY=... to the "
    "repo's .env, then restart the API (`docker compose up -d api`)."
)


class LlmError(RuntimeError):
    """Base class: the LLM call could not be completed.

    Carries the HTTP status so main.py needs one handler, not three.
    """

    status_code: int = status.HTTP_502_BAD_GATEWAY


class LlmNotConfigured(LlmError):
    """No API key. Not an error in the deployment — just an unconfigured feature."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    def __init__(self) -> None:
        super().__init__(SETUP_HINT)


class LlmRateLimited(LlmError):
    """The provider said 429. Retryable, unlike everything else here."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS

    def __init__(self) -> None:
        super().__init__(
            "The LLM provider's free tier is rate limited and this request was "
            "throttled. Retry shortly."
        )


class LlmUnavailable(LlmError):
    """Timeout, connection failure, or any other upstream error."""

    def __init__(self, reason: str) -> None:
        # `reason` is always a value this module wrote (an exception *class*
        # name, a status code) — never the provider's message body, which can
        # echo request details.
        super().__init__(f"The LLM provider did not answer ({reason}).")


@dataclass(frozen=True, slots=True)
class Completion:
    """The assistant's text plus what it cost.

    Tokens come back as `None`, not 0, when the provider omits the `usage` block
    (some OpenAI-compatible servers do, and streaming responses would). "Unknown"
    and "free" are different facts: the trace table and the daily budget both sum
    these, and counting an unknown as zero would quietly under-report spend.
    """

    text: str
    tokens_prompt: int | None
    tokens_completion: int | None

    @property
    def tokens_total(self) -> int:
        return (self.tokens_prompt or 0) + (self.tokens_completion or 0)


def is_configured() -> bool:
    return get_settings().llm_configured


@lru_cache
def _client() -> openai.OpenAI:
    """One client per process; it holds a connection pool worth reusing.

    Built lazily rather than at import time so the module imports fine — and the
    rest of the API keeps working — on a machine with no key at all.
    """
    settings = get_settings()
    return openai.OpenAI(
        api_key=settings.groq_api_key,
        base_url=settings.llm_base_url,
        # Bounded so a hung provider can't hold a request (and the worker thread
        # behind it) open indefinitely. max_retries=0 because the SDK's default
        # retry would turn one 30s timeout into three.
        timeout=settings.llm_timeout_seconds,
        max_retries=0,
    )


def model_name() -> str:
    return get_settings().llm_model


def complete(system: str, user: str, temperature: float = 0.0) -> Completion:
    """One chat completion. Returns text + token usage, or raises LlmError.

    temperature=0 by default: both calls in this feature (write SQL, summarise
    the rows) want the most likely answer, not a creative one.
    """
    if not is_configured():
        raise LlmNotConfigured()

    try:
        response = _client().chat.completions.create(
            model=model_name(),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
        )
    except openai.RateLimitError:
        raise LlmRateLimited() from None
    except openai.AuthenticationError:
        # A present-but-wrong key is a setup problem, not an outage, so it gets
        # the same 503 route as a missing one — with its own wording, because
        # "add a key" is unhelpful advice when there is one.
        raise LlmUnavailable("the configured GROQ_API_KEY was rejected") from None
    except openai.APIStatusError as exc:
        raise LlmUnavailable(f"HTTP {exc.status_code}") from None
    except openai.APIError as exc:
        # Covers APITimeoutError and APIConnectionError. Class name only — never
        # str(exc), which can carry the request that was sent.
        raise LlmUnavailable(type(exc).__name__) from None

    choices = response.choices
    content = choices[0].message.content if choices else None
    if not content or not content.strip():
        raise LlmUnavailable("empty response")

    usage = response.usage
    return Completion(
        text=content.strip(),
        tokens_prompt=usage.prompt_tokens if usage else None,
        tokens_completion=usage.completion_tokens if usage else None,
    )
