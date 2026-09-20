"""Every Gemini call goes through here, so an unattended run fails on purpose.

Two different failures each cost this project a full day:

  * a transient 503 killed the topic it happened to land on, because one
    exception from one call was treated as "this topic is bad";
  * depleted prepay credits burned all five attempts in forty seconds, because
    each attempt asked an account that had nothing left to give.

Neither is a property of the topic, so neither is handled per topic any more.
A transient error is retried with backoff on the same topic; an account-level
error aborts the whole run immediately and says so, instead of spending the
backlog to discover the same thing five times.
"""
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline_errors import QuotaExhaustedError  # noqa: E402

# Account-level: more attempts cannot help, whatever the topic or the model.
_QUOTA_MARKERS = (
    "credits are depleted",
    "prepayment credits",
    "exceeded your current quota",
    "billing",
    "quota_exceeded",
    "insufficient_quota",
    "daily limit",
)
# Momentary: the same request a few seconds later usually works.
_RETRY_MARKERS = (
    "unavailable",
    "overloaded",
    "deadline",
    "timeout",
    "timed out",
    "internal error",
    "500",
    "502",
    "503",
    "504",
    "connection",
    "temporarily",
    "try again",
    "rate limit",
    "resource_exhausted",
    "ratelimiterror",
    "apiconnectionerror",
    "internal_error",
)
# This model ID is wrong or not served to this key; another one might be.
_NEXT_MODEL_MARKERS = (
    "not_found",
    "404",
    "is not found",
    "not supported",
    "unsupported",
    "invalid model",
    "permission_denied",
    "403",
    "invalidrequesterror",
    "notfounderror",
)

MAX_ATTEMPTS = int(os.getenv("MODEL_RETRY_ATTEMPTS", "4"))
BASE_DELAY = float(os.getenv("MODEL_RETRY_BASE_DELAY", "5"))
MAX_DELAY = float(os.getenv("MODEL_RETRY_MAX_DELAY", "60"))


def classify(exc):
    """fatal_quota | retry | next_model | fatal — decided on the message text.

    The SDK raises one ClientError type for very different situations, so the
    message is all there is to go on. Quota is checked first: a depleted
    account reports 429 RESOURCE_EXHAUSTED, which also matches the retry
    markers, and retrying it is exactly the behaviour being fixed here.
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(m in text for m in _QUOTA_MARKERS):
        return "fatal_quota"
    if any(m in text for m in _NEXT_MODEL_MARKERS):
        return "next_model"
    if any(m in text for m in _RETRY_MARKERS):
        return "retry"
    return "fatal"


def suggested_delay(exc):
    """Honour the server's own retryDelay when it sends one."""
    match = re.search(r"retrydelay['\":\s]+(\d+(?:\.\d+)?)s", str(exc), re.I)
    return float(match.group(1)) if match else None


def models_from_env(primary_key, fallback_key):
    """The primary model plus any comma-separated fallbacks, in order."""
    models = [(os.getenv(primary_key) or "").strip()]
    models += [m.strip() for m in (os.getenv(fallback_key) or "").split(",")]
    seen, ordered = set(), []
    for m in models:
        if m and m not in seen:
            seen.add(m)
            ordered.append(m)
    return ordered


def text_models():
    return models_from_env("GROK_TEXT_MODEL", "GROK_TEXT_MODEL_FALLBACKS")


def image_models():
    return models_from_env("GROK_IMAGE_MODEL", "GROK_IMAGE_MODEL_FALLBACKS")


def vision_models():
    return ["grok-vision-beta"]


def call_model(do_call, models, what, attempts=None, base_delay=None, sleep=time.sleep):
    """Run do_call(model) against each model in turn, retrying what deserves it.

    Returns (result, model_used). Raises QuotaExhaustedError the moment the
    account itself is out — the caller is expected to stop the run, not move
    to the next topic.
    """
    attempts = attempts or MAX_ATTEMPTS
    base_delay = base_delay or BASE_DELAY
    if not models:
        raise ValueError(f"{what}: no model IDs configured")

    history = []
    for model in models:
        for attempt in range(1, attempts + 1):
            try:
                return do_call(model), model
            except Exception as e:  # noqa: BLE001 — re-raised below, never swallowed
                kind = classify(e)
                note = f"{model} attempt {attempt}: {type(e).__name__}: {str(e)[:160]}"
                history.append(note)

                if kind == "fatal_quota":
                    raise QuotaExhaustedError(
                        f"{what}: the xAI account is out of quota or credit, so every "
                        f"remaining topic today would fail the same way.\n  {note}\n"
                        "Top up at https://console.x.ai/ — the backlog is untouched "
                        "and the next scheduled run picks up where this one stopped."
                    ) from e

                if kind == "next_model":
                    print(f"[model] {what}: {model} not usable, trying the next model")
                    break

                if kind == "retry" and attempt < attempts:
                    delay = min(base_delay * (2 ** (attempt - 1)), MAX_DELAY)
                    delay = max(delay, suggested_delay(e) or 0)
                    delay += random.uniform(0, delay * 0.25)
                    print(f"[model] {what}: {type(e).__name__}, retrying in {delay:.0f}s "
                          f"({attempt}/{attempts})")
                    sleep(delay)
                    continue

                if kind == "retry":
                    break  # attempts spent on this model; try the next one

                raise  # genuinely fatal and specific to this call

    raise RuntimeError(f"{what}: every model failed.\n  " + "\n  ".join(history))
