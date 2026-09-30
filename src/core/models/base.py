"""Backend-agnostic chat client.

Anthropic uses the Messages API; OpenRouter, OpenAI and Google use the OpenAI-compatible Chat Completions API.
Both are normalized to `ChatResult`. Retries use exponential backoff (tenacity); token usage is captured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from typing import Any

import os
import sys
from tenacity import retry, retry_if_exception, wait_exponential_jitter

from ..config import ModelSpec, get_model


@dataclass
class ChatResult:
    text: str
    alias: str
    usage: dict[str, int] = field(default_factory=dict)
    raw: Any = None


# ------------------------------------------------------------------------------------------------------------- #
# RETRY POLICY — ONE layer, error-aware, bounded in WALL CLOCK.
#
# WHAT THIS REPLACES, and the incident that forced it (2026-07-28). The SDK client was built with
# `max_retries=6` AND this method carried `@retry(stop_after_attempt(4))`, so the two MULTIPLIED: one logical
# `chat()` against a 300s-timeout endpoint could legitimately block for 7 x 300s x 4 = 2.3 HOURS before raising.
# When a self-hosted endpoint scaled to zero (containers=0) its front door kept answering while inference did
# not, and eight forge cells sat inside that stack for over an hour producing no output and no error — because
# neither layer logs between attempts and the exception only surfaces at the very end. Every downstream handler
# (`_warn_errored`, the mutator's per-model except, the per-rollout error capture) was correct and simply never
# ran. A silent unbounded stall is the one failure they cannot see.
#
# THE RULES NOW:
#   * the SDK does NOT retry (`max_retries=0`); this decorator is the only retry, so nothing multiplies.
#   * TRANSIENT errors retry with exponential backoff + jitter: 429, 408, 5xx, connection/timeout/reset.
#   * PERMANENT errors do NOT retry at all: 400, 401, 403, 404, 422. Those never recover, and retrying them
#     used to burn the whole stack before failing — a bad key cost 35 minutes instead of one second.
#   * the whole call is bounded in WALL CLOCK by `stop_after_delay`, not just in attempts, so the worst case is
#     a number you can reason about rather than the product of two multipliers.
#   * every retry LOGS: attempt, error class, status, and the delay. A stall is now visible within seconds.
# ------------------------------------------------------------------------------------------------------------- #
_PERMANENT_STATUS = {400, 401, 403, 404, 405, 413, 422}
RETRY_MAX_ATTEMPTS = int(os.environ.get("MODEL_RETRY_ATTEMPTS", "4"))
RETRY_MAX_SECONDS = float(os.environ.get("MODEL_RETRY_MAX_SECONDS", "600"))


def _stop_retrying(retry_state) -> bool:
    """Attempt cap, plus a wall-clock ceiling that CANNOT BE SMALLER THAN THE ATTEMPTS IT PROMISES.

    THE BUG THIS REPLACES (2026-08-09). The stop was `stop_after_attempt(4) | stop_after_delay(600)` with the
    reasoning targets on `timeout=300`. For the calls that most needed retrying — the ones TIMING OUT — that
    is two attempts, not four: 300s burns, ~2s backoff, 300s burns, and the delay stop fires at ~602s. The
    attempt cap was decorative exactly where it mattered. Measured on H06: sonnet-5 lost 6/6 control reps to
    `APITimeoutError` in one sweep at `timeout=300`, and 12/12 across two sweeps at 120s before that, which
    reads as a dead model and is an artefact of this arithmetic. gemini-3.1-pro's registry comment records
    the same thing from the other side ("6/6 reps voided ... reported as `stop=max_turns`").

    WHY NOT JUST DELETE THE DELAY STOP. It exists for a real incident (2026-07-28, above): when a self-hosted
    endpoint scaled to zero its front door kept answering while inference did not, and eight forge cells sat
    inside the retry stack for over an hour, silently. A wall-clock bound is what makes that visible, so it
    stays — it is only the CONSTANT that was wrong, and only for slow endpoints.

    WHY NOT JUST RAISE THE CONSTANT. A single global number is either too small for a 300s reasoning endpoint
    or needlessly loose for a 120s one, and the failure is silent in the first direction. Deriving it from the
    spec's own timeout makes the guarantee "you get RETRY_MAX_ATTEMPTS attempts" true for every model without
    anyone maintaining a second number in step with the first.
    """
    if retry_state.attempt_number >= RETRY_MAX_ATTEMPTS:
        return True
    elapsed = retry_state.seconds_since_start
    if elapsed is None:
        return False
    spec = getattr(retry_state.args[0], "spec", None) if retry_state.args else None
    per_attempt = float(getattr(spec, "timeout", 0.0) or 0.0)
    # room for every attempt to run to its own timeout, plus the exponential backoff between them
    ceiling = max(RETRY_MAX_SECONDS, per_attempt * RETRY_MAX_ATTEMPTS + 120.0)
    return elapsed >= ceiling


def _status_of(exc: BaseException):
    """The HTTP status an SDK exception carries, if any. Both SDKs expose it, on different attributes."""
    for attr in ("status_code", "http_status", "code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, "response", None)
    v = getattr(resp, "status_code", None)
    return v if isinstance(v, int) else None


def _is_permanent(exc: BaseException) -> bool:
    """True for errors that will NEVER succeed on retry. Retrying these is pure latency."""
    st = _status_of(exc)
    if st in _PERMANENT_STATUS:
        return True
    name = type(exc).__name__
    return name in {"AuthenticationError", "PermissionDeniedError", "NotFoundError",
                    "BadRequestError", "UnprocessableEntityError"}


def _should_retry(exc: BaseException) -> bool:
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        return False
    return not _is_permanent(exc)


def _log_retry(state) -> None:
    exc = state.outcome.exception() if state.outcome else None
    nxt = getattr(state.next_action, "sleep", None)
    print(f"[model] attempt {state.attempt_number} failed: {type(exc).__name__}"
          f"{f' (HTTP {_status_of(exc)})' if _status_of(exc) else ''}: {str(exc)[:140]}"
          + (f" — retrying in {nxt:.1f}s" if nxt else " — giving up"), file=sys.stderr, flush=True)


class PermanentModelError(RuntimeError):
    """A model call that failed for a reason retrying cannot fix. Raised so callers can tell it apart from
    exhausted retries — the two mean different things and only one is worth re-running later."""


class ModelClient:
    """Lazily constructs the right SDK client for a ModelSpec and exposes chat()."""

    def __init__(self, spec: ModelSpec):
        self.spec = spec
        self._client = None  # built on first use so import never needs API keys

    def _build(self):
        if self._client is not None:
            return self._client
        # The SDK does NOT retry (`max_retries=0`); the tenacity wrapper on chat() and the agent loop's own
        # per-turn retry are the only retry layers, so nothing multiplies.
        if self.spec.provider == "anthropic":
            import anthropic
            self._client = anthropic.Anthropic(
                api_key=self.spec.resolve_api_key(), max_retries=0, timeout=self.spec.timeout
            )
        else:  # openrouter | openai | google — OpenAI-compatible
            import openai
            self._client = openai.OpenAI(base_url=self.spec.base_url, max_retries=0, timeout=self.spec.timeout,
                                         api_key=self.spec.resolve_api_key())
        return self._client

    @retry(stop=_stop_retrying,
           wait=wait_exponential_jitter(initial=2, max=30),
           retry=retry_if_exception(_should_retry),
           before_sleep=_log_retry, reraise=True)
    def chat(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 1.0,
        **kwargs: Any,
    ) -> ChatResult:
        client = self._build()
        model_id = self.spec.resolve_model_id()

        if self.spec.provider == "anthropic":
            if not self.spec.omit_temperature:
                kwargs["temperature"] = temperature
            resp = client.messages.create(
                model=model_id,
                system=system or "",
                messages=messages,
                max_tokens=max_tokens,
                **kwargs,
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            usage = {
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            }
            return ChatResult(text=text, alias=self.spec.alias, usage=usage, raw=resp)

        # OpenAI-compatible (openrouter / openai / google)
        oai_messages = ([{"role": "system", "content": system}] if system else []) + messages
        if self.spec.provider == "openai":
            # GPT-5.x reasoning family: max_completion_tokens (not max_tokens); only default temperature.
            kwargs["max_completion_tokens"] = max_tokens
            if not self.spec.omit_temperature and temperature != 1.0:
                kwargs["temperature"] = temperature
        else:
            kwargs["max_tokens"] = max_tokens
            kwargs["temperature"] = temperature
        if self.spec.default_extra_body:
            merged = dict(self.spec.default_extra_body)
            merged.update(kwargs.get("extra_body") or {})  # explicit caller extra_body overrides the spec default
            kwargs["extra_body"] = merged
        resp = client.chat.completions.create(
            model=model_id,
            messages=oai_messages,
            **kwargs,
        )
        # THE SAME TWO EMPTY-RESPONSE SHAPES THE AGENT LOOP GUARDS (loop.py, 2026-08-15): a 200 with no
        # `choices` at all, and a 200 whose `choices[0].message` is None. Gemini's OpenAI-compatible endpoint
        # produces the second one intermittently. Unguarded, both raise an AttributeError from inside the retry
        # stack, which is opaque at the call site and is not classified as a provider problem.
        #
        # THIS RAISES RATHER THAN DEGRADING TO "". This is the NON-agentic path — judges, the forge critic, the
        # mutator — and a judge handed an empty completion would score on it silently, which is strictly worse
        # than an error: it turns a provider hiccup into a fabricated verdict. Raising a plain RuntimeError also
        # keeps it RETRYABLE (PermanentModelError would not be), which is right, because an empty message from
        # this endpoint is transient.
        _choices = getattr(resp, "choices", None)
        _msg = getattr(_choices[0], "message", None) if _choices else None
        if _msg is None:
            _err = getattr(resp, "error", None) or ("no choices" if not _choices else "choice carried no message")
            raise RuntimeError(f"provider returned an empty completion ({model_id}): {str(_err)[:200]}")
        text = _msg.content or ""
        usage = {}
        if resp.usage:
            usage = {
                "input_tokens": resp.usage.prompt_tokens,
                "output_tokens": resp.usage.completion_tokens,
            }
        return ChatResult(text=text, alias=self.spec.alias, usage=usage, raw=resp)


@cache
def get_client(alias: str) -> ModelClient:
    return ModelClient(get_model(alias))
