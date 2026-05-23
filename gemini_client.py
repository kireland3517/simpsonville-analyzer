"""
gemini_client.py
----------------
Shared Google Gemini API helpers for text and vision calls.

Uses the google-genai SDK (NOT the deprecated google-generativeai package):
    pip install google-genai
    from google import genai
    from google.genai import types

Requires GEMINI_API_KEY (or GOOGLE_API_KEY) in the environment.

Model selection (cheapest good quality for each task):
  GEMINI_VISION_MODEL  — photo analysis          (default: gemini-2.5-flash)
  GEMINI_TEXT_MODEL    — ROI reports             (default: gemini-2.5-pro)
  GEMINI_DETAIL_MODEL  — on-demand item detail  (default: gemini-2.5-flash)
  GEMINI_MODEL         — optional override for ALL of the above if set
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from google import genai
from google.genai import types

DEFAULT_VISION_MODEL = "gemini-2.5-flash"
DEFAULT_TEXT_MODEL = "gemini-2.5-pro"
DEFAULT_DETAIL_MODEL = "gemini-2.5-flash"


def _resolve_model(specific: str | None, default: str) -> str:
    """GEMINI_MODEL overrides everything; else use task-specific var or default."""
    override = (os.environ.get("GEMINI_MODEL") or "").strip()
    if override:
        return override
    model = (specific or default).strip()
    return model or default


def get_vision_model() -> str:
    """Model for photo vision analysis (high volume — use flash)."""
    return _resolve_model(os.environ.get("GEMINI_VISION_MODEL"), DEFAULT_VISION_MODEL)


def get_text_model() -> str:
    """Model for ROI report generation (low volume — use pro)."""
    return _resolve_model(os.environ.get("GEMINI_TEXT_MODEL"), DEFAULT_TEXT_MODEL)


def get_detail_model() -> str:
    """Model for on-demand upgrade/repair deep detail (cached — use flash)."""
    return _resolve_model(os.environ.get("GEMINI_DETAIL_MODEL"), DEFAULT_DETAIL_MODEL)


def get_model() -> str:
    """Backward-compatible alias for get_text_model()."""
    return get_text_model()


# Static defaults for imports / documentation
VISION_MODEL = DEFAULT_VISION_MODEL
TEXT_MODEL = DEFAULT_TEXT_MODEL
DETAIL_MODEL = DEFAULT_DETAIL_MODEL


def get_api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def get_client() -> genai.Client:
    api_key = get_api_key()
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is not set")
    return genai.Client(api_key=api_key)


def extract_json(text: str) -> dict:
    """Pull the first {...} block from the response and parse it."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in response: {text!r}")
    return json.loads(match.group())


def _diagnose_response(response: Any) -> str:
    """Build a human-readable reason when response text is missing."""
    parts = ["Gemini returned an empty response"]
    pf = getattr(response, "prompt_feedback", None)
    if pf and getattr(pf, "block_reason", None):
        parts.append(f"prompt blocked: {pf.block_reason}")
    candidates = getattr(response, "candidates", None) or []
    parts.append(f"candidates={len(candidates)}")
    if candidates:
        fr = getattr(candidates[0], "finish_reason", None)
        parts.append(f"finish_reason={fr}")
        if fr and "MAX_TOKENS" in str(fr).upper():
            parts.append(
                "output token budget exhausted before JSON was written — "
                "gemini-2.5-pro may use tokens for internal reasoning; increase max_output_tokens"
            )
    return "; ".join(parts)


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text and text.strip():
        return text
    candidates = getattr(response, "candidates", None) or []
    chunks: list[str] = []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", None) or []:
            if getattr(part, "thought", False):
                continue
            part_text = getattr(part, "text", None)
            if part_text:
                chunks.append(part_text)
    if chunks:
        return "".join(chunks)
    raise ValueError(_diagnose_response(response))


def _gen_config(
    *,
    system: str,
    max_tokens: int,
) -> types.GenerateContentConfig:
    """Shared generate_content config for JSON responses."""
    return types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=max_tokens,
        temperature=0.2,
        response_mime_type="application/json",
    )


def _finish_reason(response: Any) -> str:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return "unknown"
    reason = candidates[0].finish_reason
    return str(reason) if reason is not None else "unknown"


def _log_response(label: str, text: str, finish: str) -> None:
    head = text[:200].replace("\n", " ")
    tail = text[-200:].replace("\n", " ")
    print(f"  [{label}] finish_reason={finish!r}  chars={len(text):,}")
    print(f"  [{label}] HEAD: {head}")
    print(f"  [{label}] TAIL: {tail}")
    if "MAX_TOKENS" in finish.upper() or finish.upper() in {"LENGTH", "MAX_OUTPUT_TOKENS"}:
        print(f"  [{label}] WARNING: response hit max_tokens limit — JSON is likely truncated")


def generate_text(
    prompt: str,
    *,
    system: str,
    max_tokens: int = 2048,
    label: str = "Gemini",
    model: str | None = None,
) -> tuple[dict | None, str | None]:
    """
    Make one Gemini text call and parse JSON. Retries once on parse failure.
    Returns (result_dict, None) on success or (None, error_str) on failure.
    """
    model = model or get_text_model()
    print(f"  [{label}] model={model}")
    try:
        client = get_client()
    except ValueError as exc:
        return None, str(exc)

    config = _gen_config(system=system, max_tokens=max_tokens)

    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )
    except Exception as exc:
        return None, f"{label} API error: {exc}"

    try:
        text = _response_text(response)
    except ValueError as exc:
        return None, f"{label} API error: {exc}"

    finish = _finish_reason(response)
    _log_response(label, text, finish)

    try:
        return extract_json(text), None
    except (ValueError, json.JSONDecodeError) as parse_err:
        print(f"  [{label}] JSON parse failed: {parse_err} — retrying")

    retry_prompt = (
        "Your previous response could not be parsed as JSON. "
        "Return ONLY the JSON object — no explanation, no markdown fences, "
        "no truncation. Start with '{' and end with '}'."
    )
    contents = [
        types.Content(role="user", parts=[types.Part.from_text(text=prompt)]),
        types.Content(role="model", parts=[types.Part.from_text(text=text)]),
        types.Content(role="user", parts=[types.Part.from_text(text=retry_prompt)]),
    ]

    try:
        retry_response = client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
        retry_text = _response_text(retry_response)
        retry_finish = _finish_reason(retry_response)
        _log_response(f"{label} RETRY", retry_text, retry_finish)
        return extract_json(retry_text), None
    except (ValueError, json.JSONDecodeError) as exc:
        return None, f"{label} JSON parse failed after retry: {exc}"
    except Exception as exc:
        return None, f"{label} retry API error: {exc}"


def generate_vision(
    image_bytes: bytes,
    mime_type: str,
    prompt: str,
    *,
    system: str,
    max_tokens: int = 2048,
    model: str | None = None,
) -> tuple[dict | None, str | None]:
    """Analyze an image and return parsed JSON."""
    model = model or get_vision_model()
    print(f"  [Vision] model={model}")
    try:
        client = get_client()
    except ValueError as exc:
        return None, str(exc)

    config = _gen_config(system=system, max_tokens=max_tokens)

    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                types.Part.from_text(text=prompt),
            ],
        )
    ]

    try:
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
        text = _response_text(response)
        return extract_json(text), None
    except (ValueError, json.JSONDecodeError) as exc:
        return None, str(exc)
    except Exception as exc:
        return None, str(exc)
