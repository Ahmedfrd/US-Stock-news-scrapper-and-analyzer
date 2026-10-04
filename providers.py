"""
providers.py — one interface over several *free* LLM APIs.

Supported (all have genuinely free tiers, no credit card):
  * gemini      Google AI Studio — Flash models. Default. Key: GEMINI_API_KEY
                (+ optional GEMINI_API_KEY_2/_3, rotated on rate-limit).
  * groq        Groq — fast open-weight models. Key: GROQ_API_KEY.
  * openrouter  OpenRouter — ":free" community models. Key: OPENROUTER_API_KEY.

Free-model IDs ROT. On 2026-09-06 both hardcoded fallbacks died in production
(groq llama-3.3-70b-versatile, openrouter inclusionai/ling-3.0-flash:free), and
on 2026-10-04 the replacement OpenRouter pick returned 403 ("only available on
agentic harnesses"). So each provider's live catalogue is read once per run and
turned into an ORDERED list of candidate models; a call that fails because the
model is gone/restricted moves on to the next candidate instead of losing the
provider. (Idea borrowed from the market-digest monorepo's _resolve_model.)

`complete_chain()` tries a preferred provider first and falls back to the others,
so a single provider's rate limit never sinks an AI call.

Privacy note: free tiers generally train on your prompts. This tool only sends
public headlines + public financial metrics.
"""

from __future__ import annotations

import os
import re
import time
import json
import threading
import requests


class ProviderError(Exception):
    pass


# --------------------------------------------------------------------------- #
#  Per-provider pacing — each provider is paced against its OWN free-tier RPM
# --------------------------------------------------------------------------- #
_RPM = {"gemini": 10, "groq": 25, "openrouter": 15}
_last_call: dict[str, float] = {}
_lock = threading.Lock()


def _throttle(provider: str) -> None:
    rpm = _RPM.get(provider)
    if not rpm:
        return
    gap = 60.0 / rpm
    while True:
        with _lock:
            now = time.monotonic()
            wait = _last_call.get(provider, 0.0) + gap - now
            if wait <= 0:
                _last_call[provider] = now
                return
        time.sleep(min(wait, 5.0))


def _retry(fn, tries=3, base=1.5):
    last = None
    for i in range(tries):
        try:
            return fn()
        except _ModelGone:
            raise                      # retrying a dead model is pointless
        except Exception as e:  # noqa: BLE001
            last = e
            if i >= tries - 1:
                break
            msg = str(e).lower()
            wait = 15 * (i + 1) if ("429" in msg or "rate limit" in msg) else base * (2 ** i)
            time.sleep(wait)
    raise ProviderError(str(last))


class _ModelGone(ProviderError):
    """The model itself is unavailable (removed, restricted, not free any more)."""


_GONE_PAT = re.compile(r"does not exist|not found|no endpoints|decommission|deprecated|"
                       r"not a valid model|unavailable for free|agentic harness|"
                       r"model_not_found|is not available|no longer available", re.I)


def _is_gone(status: int, text: str) -> bool:
    return status in (403, 404) or (status == 400 and bool(_GONE_PAT.search(text or "")))


# --------------------------------------------------------------------------- #
#  Model catalogues
# --------------------------------------------------------------------------- #
DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",
    "groq": "openai/gpt-oss-120b",
    "openrouter": "openrouter/free",
}

_PREFER = {
    "gemini": ["gemini-2.5-flash", "gemini-2.5-flash-lite"],
    # Strongest general text models first; small/fast ones last.
    "groq": ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "qwen/qwen3-32b",
             "meta-llama/llama-4-maverick-17b-128e-instruct", "openai/gpt-oss-20b",
             "meta-llama/llama-4-scout-17b-16e-instruct", "llama-3.1-8b-instant"],
    # openrouter/free is OpenRouter's own router over whatever free model is up.
    "openrouter": ["openrouter/free", "qwen/qwen3.8-27b:free", "google/gemma-4-31b-it:free",
                   "nvidia/nemotron-3-super-120b-a12b:free", "google/gemma-4-26b-a4b-it:free",
                   "deepseek/deepseek-chat-v3.1:free", "meta-llama/llama-3.3-70b-instruct:free"],
}
# Catalogue entries that are not general chat models (or are known-restricted).
_SKIP = re.compile(r"whisper|tts|guard|safety|embed|vision|lyria|audio|image|"
                   r"inkling|stealth|code|omni|orpheus|playai|compound", re.I)

_catalogue: dict[str, list[str]] = {}
_dead: set[tuple[str, str]] = set()


def _live_ids(provider: str) -> list[str] | None:
    try:
        if provider == "openrouter":
            r = requests.get("https://openrouter.ai/api/v1/models", timeout=20)
            r.raise_for_status()
            out = []
            for m in r.json().get("data", []):
                pr = m.get("pricing") or {}
                if (str(pr.get("prompt")) in ("0", "0.0") and str(pr.get("completion")) in ("0", "0.0")
                        and int(m.get("context_length") or 0) >= 32000):
                    out.append(m.get("id", ""))
            if "openrouter/free" not in out:
                out.append("openrouter/free")
            return out
        if provider == "groq":
            key = os.environ.get("GROQ_API_KEY")
            if not key:
                return None
            r = requests.get("https://api.groq.com/openai/v1/models",
                             headers={"Authorization": f"Bearer {key}"}, timeout=20)
            r.raise_for_status()
            return [m.get("id", "") for m in r.json().get("data", []) if m.get("active", True)]
    except Exception as e:  # noqa: BLE001 — discovery must never break a run
        print(f"[providers] could not list {provider} models ({e}); using defaults.", flush=True)
    return None


def candidates(provider: str, preferred: str | None = None) -> list[str]:
    """Ordered model list for a provider: preferred → known-good → anything else
    usable in the live catalogue. Dead models found this run are dropped."""
    provider = provider.lower()
    if provider not in _catalogue:
        live = _live_ids(provider)
        prefs = _PREFER.get(provider, [])
        if live is None:
            order = list(prefs)
        else:
            order = [m for m in prefs if m in live]
            order += [m for m in live if m not in order and not _SKIP.search(m)]
        _catalogue[provider] = order or list(prefs)
    order = list(_catalogue[provider])
    if preferred and preferred not in order:
        order.insert(0, preferred)
    elif preferred:
        order.remove(preferred)
        order.insert(0, preferred)
    return [m for m in order if (provider, m) not in _dead]


# --------------------------------------------------------------------------- #
#  Gemini (Google AI Studio) — native REST, with key rotation
# --------------------------------------------------------------------------- #
def _gemini_keys() -> list[str]:
    keys: list[str] = []
    for var in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3",
                "GEMINI_API_KEY_4", "GOOGLE_API_KEY"):
        for k in (os.environ.get(var) or "").split(","):
            k = k.strip()
            if k and k not in keys:
                keys.append(k)
    return keys


def _gemini(system: str, user: str, model: str, json_mode: bool = True) -> str:
    keys = _gemini_keys()
    if not keys:
        raise ProviderError("GEMINI_API_KEY not set")
    # Thinking disabled + a large output cap: on big structured-JSON prompts the
    # thinking budget used to eat the output budget and truncate the JSON.
    gen_cfg = {"temperature": 0.3, "maxOutputTokens": 24576,
               "thinkingConfig": {"thinkingBudget": 0}}
    if json_mode:
        gen_cfg["responseMimeType"] = "application/json"
    body = {"system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": user}]}],
            "generationConfig": gen_cfg}
    last = None
    for idx, key in enumerate(keys, 1):
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")

        def call():
            r = requests.post(url, json=body, timeout=120)
            if r.status_code == 429:
                raise ProviderError("Gemini rate limited (429)")
            if r.status_code == 404:
                raise _ModelGone(f"Gemini model {model} not found")
            if r.status_code >= 400:
                raise ProviderError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
            cand = r.json()["candidates"][0]
            text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
            if not text:
                raise ProviderError(f"Gemini returned empty text (finishReason={cand.get('finishReason')})")
            return text

        try:
            return _retry(call, tries=2)
        except _ModelGone:
            raise
        except Exception as e:  # noqa: BLE001 — this key exhausted; try the next one
            last = e
            if idx < len(keys):
                print(f"[providers] Gemini key #{idx} failed ({e}); trying key #{idx + 1}.", flush=True)
    raise ProviderError(str(last))


# --------------------------------------------------------------------------- #
#  OpenAI-compatible (Groq, OpenRouter)
# --------------------------------------------------------------------------- #
_OPENAI_COMPATIBLE = {
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
}
# Free-tier request-size ceilings (characters of the user message). The JSON
# instructions live at the TOP and are always kept; only the tail is trimmed.
_MAX_USER_CHARS = {"groq": 24000, "openrouter": 60000}


def _openai_style(provider: str, system: str, user: str, model: str,
                  json_mode: bool = True) -> str:
    endpoint, env = _OPENAI_COMPATIBLE[provider]
    key = os.environ.get(env)
    if not key:
        raise ProviderError(f"{env} not set")
    cap = _MAX_USER_CHARS.get(provider)
    if cap and len(user) > cap:
        user = user[:cap] + "\n\n[context truncated to fit this provider's request-size limit]"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {"model": model, "temperature": 0.3,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}
    # Groq honours json_object; many OpenRouter free models reject it (HTTP 400),
    # so OpenRouter relies on the prompt + parse_json()'s repair pass instead.
    if json_mode and provider == "groq":
        body["response_format"] = {"type": "json_object"}

    def call():
        r = requests.post(endpoint, headers=headers, json=body, timeout=120)
        if r.status_code == 429:
            raise ProviderError(f"{provider} rate limited (429)")
        if r.status_code == 400 and "response_format" in body and "json" in r.text.lower():
            body.pop("response_format", None)          # model can't do JSON mode
            raise ProviderError(f"{provider} rejected JSON mode; retrying without it")
        if _is_gone(r.status_code, r.text):
            raise _ModelGone(f"{provider} model {model}: HTTP {r.status_code} {r.text[:200]}")
        if r.status_code >= 400:
            raise ProviderError(f"{provider} HTTP {r.status_code}: {r.text[:300]}")
        content = r.json()["choices"][0]["message"].get("content") or ""
        if not content.strip():
            raise ProviderError(f"{provider} returned empty content")
        return content

    return _retry(call)


# --------------------------------------------------------------------------- #
#  Public entry points
# --------------------------------------------------------------------------- #
def complete(provider: str, system: str, user: str, model: str | None = None,
             json_mode: bool = True) -> str:
    """One provider; walks its candidate models when a model is gone."""
    provider = (provider or "gemini").lower()
    if provider not in ("gemini",) and provider not in _OPENAI_COMPATIBLE:
        raise ProviderError(f"Unknown provider: {provider}")
    last = None
    rate_limited = 0
    for m in candidates(provider, model)[:4]:
        _throttle(provider)
        try:
            if provider == "gemini":
                return _gemini(system, user, m, json_mode=json_mode)
            return _openai_style(provider, system, user, m, json_mode=json_mode)
        except _ModelGone as e:
            _dead.add((provider, m))
            last = e
            print(f"[providers] {provider}: model '{m}' unavailable — trying the next one.", flush=True)
        except ProviderError as e:
            # Free-tier limits are often per MODEL (Groq; Gemini flash vs flash-lite),
            # so one more model is worth a try before handing over to another provider.
            msg = str(e).lower()
            if ("429" in msg or "rate limit" in msg) and rate_limited == 0:
                rate_limited += 1
                last = e
                print(f"[providers] {provider}: '{m}' rate-limited — trying one more model.", flush=True)
                continue
            raise
    raise ProviderError(str(last) if last else f"{provider}: no usable model")


def model_in_use(provider: str) -> str:
    c = candidates(provider)
    return c[0] if c else DEFAULT_MODELS.get(provider, "?")


def available(provider: str) -> bool:
    provider = (provider or "gemini").lower()
    if provider == "gemini":
        return bool(_gemini_keys())
    if provider in _OPENAI_COMPATIBLE:
        return bool(os.environ.get(_OPENAI_COMPATIBLE[provider][1]))
    return False


ALL_PROVIDERS = ("gemini", "groq", "openrouter")


def complete_chain(order, system: str, user: str, json_mode: bool = True) -> tuple[str, str]:
    """Try each provider in `order`, then every other available provider.
    Returns (raw_text, provider_used). Raises ProviderError if all fail."""
    seq = []
    for p in list(order) + list(ALL_PROVIDERS):
        p = (p or "").lower()
        if p and p not in seq:
            seq.append(p)
    last = "no providers configured"
    for prov in seq:
        if not available(prov):
            continue
        try:
            return complete(prov, system, user, json_mode=json_mode), prov
        except Exception as e:  # noqa: BLE001
            last = f"{prov}: {e}"
            print(f"[providers] {prov} failed ({str(e)[:200]}); trying next provider.", flush=True)
    raise ProviderError(f"all providers failed ({last})")


# Fields the prompts describe as "bullet lines" — models sometimes return them as
# JSON arrays instead of newline-separated strings. Join arrays back into text.
_TEXT_FIELDS = {"summary", "news_impact", "fundamental_read", "divergence", "crowd_note",
                "rationale", "reason", "why", "note", "move_explainer", "nav_read",
                "vs_market", "vs_peers", "holdings_news_impact", "news", "impact_on_fund",
                "result", "outlook", "management_review", "read_across",
                "what_would_change_it", "key_risk", "changed_since_last_week",
                "catalyst", "mechanism", "priced_in", "invalidation", "before_acting",
                "why_now", "progression"}


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
    """Robustly pull a JSON object out of a model response (fences, comments,
    trailing commas, unescaped quotes, truncated tails — json-repair handles
    the near-misses free models emit)."""
    text = (text or "").strip()
    # Reasoning models sometimes prepend a <think>…</think> block.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1:
        raise ValueError("no JSON object in model response")
    blob = text[start:end + 1] if end != -1 else text[start:]
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        cleaned = "\n".join(l for l in blob.splitlines() if not l.strip().startswith("//"))
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            from json_repair import repair_json
            obj = repair_json(text[start:], return_objects=True)
            if not isinstance(obj, dict):
                raise ValueError("json-repair produced a non-object")
            return obj
