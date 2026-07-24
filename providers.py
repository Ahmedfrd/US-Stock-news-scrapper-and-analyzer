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
            if i >= tries - 1:
                break
            # Rate limits need a real pause (free tiers are per-minute);
            # other transient errors get a short exponential backoff.
            msg = str(e).lower()
            wait = 15 * (i + 1) if ("429" in msg or "rate limit" in msg) else base * (2 ** i)
            time.sleep(wait)
    raise ProviderError(str(last))


# --------------------------------------------------------------------------- #
#  Gemini (Google AI Studio) — native REST
# --------------------------------------------------------------------------- #
def _gemini_keys() -> list[str]:
    """All configured Gemini keys, in priority order, de-duplicated.

    Add extra keys (e.g. from a second Google account, for more free quota) via
    numbered vars GEMINI_API_KEY_2, GEMINI_API_KEY_3, … or as a comma-separated
    list in GEMINI_API_KEY. Each Google account has its own free-tier quota, so
    when one is rate-limited (429) the next key is tried automatically. Because
    the debate + main analysis both back up to Gemini, more keys here is the
    single biggest lever against the daily 429s.
    """
    keys: list[str] = []
    raw: list[str] = []
    for var in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3",
                "GEMINI_API_KEY_4", "GOOGLE_API_KEY"):
        val = os.environ.get(var)
        if val:
            raw.extend(val.split(","))          # allow comma-separated lists too
    for k in raw:
        k = k.strip()
        if k and k not in keys:
            keys.append(k)
    return keys


def _gemini(system: str, user: str, model: str, json_mode: bool = True) -> str:
    keys = _gemini_keys()
    if not keys:
        raise ProviderError("GEMINI_API_KEY not set")
    # Gemini 2.5 Flash spends part of its output-token budget on internal
    # "thinking" before it writes the answer; on a big structured-JSON prompt
    # that ate into the budget enough to cut the JSON off mid-object (the
    # "Expecting ',' delimiter" parse failures). Disabling thinking (not needed
    # for extraction/summarization) and raising the cap fixes the truncation.
    gen_cfg = {"temperature": 0.3, "maxOutputTokens": 8192,
               "thinkingConfig": {"thinkingBudget": 0}}
    if json_mode:
        gen_cfg["responseMimeType"] = "application/json"
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": user}]}],
        "generationConfig": gen_cfg,
    }

    last = None
    for idx, key in enumerate(keys, 1):
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")

        def call():
            r = requests.post(url, json=body, timeout=90)
            if r.status_code == 429:
                raise ProviderError("Gemini rate limited (429)")
            r.raise_for_status()
            data = r.json()
            cand = data["candidates"][0]
            return "".join(p.get("text", "") for p in cand["content"]["parts"])

        try:
            return _retry(call)
        except Exception as e:  # noqa: BLE001 — this key exhausted; try the next one
            last = e
            if len(keys) > 1:
                print(f"[providers] Gemini key #{idx} failed ({e}); trying next key.",
                      flush=True)
    raise ProviderError(str(last))


# --------------------------------------------------------------------------- #
#  OpenAI-compatible (Groq, OpenRouter, and most others)
# --------------------------------------------------------------------------- #
_OPENAI_COMPATIBLE = {
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
}


def _openai_style(provider: str, system: str, user: str, model: str,
                  json_mode: bool = True) -> str:
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
    }
    # Only force JSON output when the caller actually wants JSON — Groq rejects
    # json_object mode (HTTP 400) when the prompt doesn't ask for JSON, which
    # silently killed every prose call (e.g. the debate bull/bear cases).
    if json_mode:
        body["response_format"] = {"type": "json_object"}

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
    # meta-llama/llama-3.3-70b-instruct:free was removed from OpenRouter's free
    # catalog (every call 404'd). OpenRouter's free lineup turns over often —
    # re-check https://openrouter.ai/models?max_price=0 if this one disappears too.
    "openrouter": "inclusionai/ling-3.0-flash:free",
}


def complete(provider: str, system: str, user: str, model: str | None = None,
             json_mode: bool = True) -> str:
    provider = (provider or "gemini").lower()
    model = model or DEFAULT_MODELS.get(provider)
    if provider == "gemini":
        return _gemini(system, user, model, json_mode=json_mode)
    if provider in _OPENAI_COMPATIBLE:
        return _openai_style(provider, system, user, model, json_mode=json_mode)
    raise ProviderError(f"Unknown provider: {provider}")


def available(provider: str) -> bool:
    """True if the key for this provider is present in the environment."""
    provider = (provider or "gemini").lower()
    if provider == "gemini":
        return bool(_gemini_keys())
    if provider in _OPENAI_COMPATIBLE:
        return bool(os.environ.get(_OPENAI_COMPATIBLE[provider][1]))
    return False


# Fields the prompts describe as "bullet lines" — models sometimes return them
# as JSON arrays instead of newline-separated strings. The renderer and the
# plain-text builder expect strings, so join arrays back into bullet text.
_TEXT_FIELDS = {"market_overview", "summary", "news_impact", "fundamental_read",
                "divergence", "crowd_note", "bull", "bear", "rationale", "reason",
                "why", "note", "move_explainer", "nav_read", "vs_market", "vs_peers",
                "risks", "holdings_news_impact", "result", "outlook",
                "management_review", "verdict", "read_across",
                "what_would_change_it", "start_here"}


def normalize_text_fields(obj):
    """Coerce list-valued free-text fields to newline-joined strings, recursively."""
    if isinstance(obj, dict):
        return {k: ("\n".join(str(x) for x in v)
                    if (k in _TEXT_FIELDS and isinstance(v, list))
                    else normalize_text_fields(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_text_fields(x) for x in obj]
    return obj


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
    blob = text[start:end + 1]
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        # Common model slips: '// comment' lines and trailing commas.
        import re
        cleaned = "\n".join(l for l in blob.splitlines() if not l.strip().startswith("//"))
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
        return json.loads(cleaned)
