"""The one place that talks to a language model.

Providers:
  gemini     Google Gemini API. A free key from https://aistudio.google.com/apikey works.
  anthropic  Claude (optional: pip install anthropic).

Picked by LLM_PROVIDER, otherwise Gemini if GEMINI_API_KEY / GOOGLE_API_KEY is set,
otherwise Claude if ANTHROPIC_API_KEY is set.
"""
import os
import time

import requests

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
RETRY_STATUSES = (429, 500, 502, 503, 504)

_sleep = time.sleep   # tests replace this
_last_call = 0.0


class LLMError(Exception):
    pass


# --- configuration ------------------------------------------------------

def gemini_key():
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def provider():
    """'gemini', 'anthropic', or None if nothing is set up."""
    explicit = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if explicit:
        if explicit not in ("gemini", "anthropic"):
            raise LLMError(f"LLM_PROVIDER must be 'gemini' or 'anthropic', not '{explicit}'.")
        return explicit
    if gemini_key():
        return "gemini"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


def config_problem():
    """A one-line reason the model can't be used, or None if it's ready."""
    try:
        name = provider()
    except LLMError as e:
        return str(e)
    if name is None:
        return "No AI key set: add GEMINI_API_KEY (free at aistudio.google.com/apikey) to .env."
    if name == "gemini" and not gemini_key():
        return "LLM_PROVIDER=gemini but GEMINI_API_KEY is not set."
    if name == "anthropic" and not os.getenv("ANTHROPIC_API_KEY"):
        return "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set."
    return None


def get_model():
    if provider() == "anthropic":
        return os.getenv("ANTHROPIC_MODEL") or os.getenv("MODEL") or DEFAULT_ANTHROPIC_MODEL
    return os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL


# --- generate -----------------------------------------------------------

def generate(prompt, max_tokens=500, json_mode=False):
    """Send one prompt and return the reply text. Raises LLMError on any failure."""
    problem = config_problem()
    if problem:
        raise LLMError(problem)
    if provider() == "anthropic":
        return _anthropic_generate(prompt, max_tokens)
    return _gemini_generate(prompt, max_tokens, json_mode)


def _throttle():
    """Free keys have a requests-per-minute limit, so space calls out."""
    global _last_call
    gap = float(os.getenv("GEMINI_MIN_INTERVAL", "4"))
    wait = _last_call + gap - time.monotonic()
    if wait > 0:
        _sleep(wait)
    _last_call = time.monotonic()


def _retry_delay(response, attempt):
    try:
        return min(60.0, float(response.headers.get("Retry-After")))
    except (TypeError, ValueError):
        return min(60.0, 5.0 * 2 ** attempt)


def _gemini_error(response):
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        message = response.text
    return f"Gemini error {response.status_code}: {str(message).strip()[:200]}"


def _gemini_text(response):
    try:
        data = response.json()
    except ValueError as e:
        raise LLMError("Gemini returned a reply that isn't JSON.") from e
    block = (data.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise LLMError(f"Gemini blocked the prompt ({block}).")
    candidates = data.get("candidates") or []
    if not candidates:
        raise LLMError("Gemini returned no answer.")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        raise LLMError(f"Gemini returned no text (finishReason={candidates[0].get('finishReason')}).")
    return text


def _gemini_generate(prompt, max_tokens, json_mode):
    retries = int(os.getenv("GEMINI_MAX_RETRIES", "4"))
    url = GEMINI_URL.format(model=get_model())
    # Newer Gemini models spend part of this budget "thinking", so keep it generous.
    config = {"maxOutputTokens": max(max_tokens, 2048)}
    if json_mode:
        config["responseMimeType"] = "application/json"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": config}
    headers = {"x-goog-api-key": gemini_key(), "Content-Type": "application/json"}

    for attempt in range(retries + 1):
        _throttle()
        try:
            response = requests.post(url, json=body, headers=headers, timeout=60)
        except requests.RequestException as e:
            if attempt == retries:
                raise LLMError(f"Gemini request failed: {type(e).__name__}") from e
            _sleep(min(60.0, 5.0 * 2 ** attempt))
            continue
        if response.status_code in RETRY_STATUSES and attempt < retries:
            _sleep(_retry_delay(response, attempt))
            continue
        if response.status_code >= 400:
            raise LLMError(_gemini_error(response))
        return _gemini_text(response)


def _anthropic_generate(prompt, max_tokens):
    try:
        import anthropic
    except ImportError as e:
        raise LLMError("The Claude provider needs: pip install anthropic") from e
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=get_model(),
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as e:
        raise LLMError(f"Claude error: {type(e).__name__}: {e}") from e
