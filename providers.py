"""
providers.py — one interface over several *free* LLM APIs.

Supported (all have genuinely free tiers, no credit card):
  * gemini      Google AI Studio — Flash models. Default. ~1,500 req/day free,
                1M-token context. Key: GEMINI_API_KEY (get one at ai.google.dev).
  * groq        Groq — fast open-weight models (Llama/Qwen). Key: GROQ_API_KEY.
  * openrouter  OpenRouter — many ":free" community models. Key: OPENROUTER_API_KEY.

Everything is plain HTTPS via `requests`, so there are no vendor SDKs to break.
`complete()` returns raw text; the analyzer asks for JSON and parses it.

Privacy note: free tiers generally train on your prompts. This tool only sends
public headlines + public financial metrics, so that's fine — but don't wire in
anything private.
"""

from __future__ import annotations

import os
import time
import json
import requests


class ProviderError(Exception):
    pass


def _retry(fn, tries=3, base=1.5):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            # Back off on rate limits / transient errors.
            time.sleep(base * (2 ** i))
    raise ProviderError(str(last))


# --------------------------------------------------------------------------- #
#  Gemini (Google AI Studio) — native REST
# --------------------------------------------------------------------------- #
def _gemini(system: str, user: str, model: str) -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise ProviderError("GEMINI_API_KEY not set")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={key}")
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"},
    }

    def call():
        r = requests.post(url, json=body, timeout=90)
        if r.status_code == 429:
            raise ProviderError("Gemini rate limited (429)")
        r.raise_for_status()
        data = r.json()
        cand = data["candidates"][0]
        return "".join(p.get("text", "") for p in cand["content"]["parts"])

    return _retry(call)


# --------------------------------------------------------------------------- #
#  OpenAI-compatible (Groq, OpenRouter, and most others)
# --------------------------------------------------------------------------- #
_OPENAI_COMPATIBLE = {
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
}


def _openai_style(provider: str, system: str, user: str, model: str) -> str:
    endpoint, env = _OPENAI_COMPATIBLE[provider]
    key = os.environ.get(env)
    if not key:
        raise ProviderError(f"{env} not set")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "temperature": 0.3,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
    }

    def call():
        r = requests.post(endpoint, headers=headers, json=body, timeout=90)
        if r.status_code == 429:
            raise ProviderError(f"{provider} rate limited (429)")
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    return _retry(call)


# --------------------------------------------------------------------------- #
#  Public entry point
# --------------------------------------------------------------------------- #
DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",              # free; try gemini-2.5-flash-lite for more RPM
    "groq": "llama-3.3-70b-versatile",         # free
    "openrouter": "meta-llama/llama-3.3-70b-instruct:free",
}


def complete(provider: str, system: str, user: str, model: str | None = None) -> str:
    provider = (provider or "gemini").lower()
    model = model or DEFAULT_MODELS.get(provider)
    if provider == "gemini":
        return _gemini(system, user, model)
    if provider in _OPENAI_COMPATIBLE:
        return _openai_style(provider, system, user, model)
    raise ProviderError(f"Unknown provider: {provider}")


def available(provider: str) -> bool:
    """True if the key for this provider is present in the environment."""
    provider = (provider or "gemini").lower()
    if provider == "gemini":
        return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    if provider in _OPENAI_COMPATIBLE:
        return bool(os.environ.get(_OPENAI_COMPATIBLE[provider][1]))
    return False


def parse_json(text: str) -> dict:
    """Robustly pull a JSON object out of a model response."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model response")
    return json.loads(text[start:end + 1])
