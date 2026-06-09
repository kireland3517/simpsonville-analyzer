"""
claude_client.py
----------------
Drop-in replacement for gemini_client.py using the Anthropic SDK.

Exposes the same two public functions (generate_text, generate_vision) and
the same model-selection helpers so analyzer.py and roi.py need only change
their import line.

Requires ANTHROPIC_API_KEY in the environment.

Model selection:
  CLAUDE_VISION_MODEL  — photo analysis          (default: claude-sonnet-4-6)
  CLAUDE_TEXT_MODEL    — ROI reports             (default: claude-sonnet-4-6)
  CLAUDE_DETAIL_MODEL  — on-demand item detail   (default: claude-sonnet-4-6)
  CLAUDE_MODEL         — optional override for ALL of the above if set
"""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

import anthropic

DEFAULT_VISION_MODEL = "claude-sonnet-4-6"
DEFAULT_TEXT_MODEL   = "claude-sonnet-4-6"
DEFAULT_DETAIL_MODEL = "claude-sonnet-4-6"

# Aliases kept for any code that reads these as constants
VISION_MODEL = DEFAULT_VISION_MODEL
TEXT_MODEL   = DEFAULT_TEXT_MODEL
DETAIL_MODEL = DEFAULT_DETAIL_MODEL


def _resolve_model(specific: str | None, default: str) -> str:
    override = (os.environ.get("CLAUDE_MODEL") or "").strip()
    if override:
        return override
    model = (specific or default).strip()
    return model or default


def get_vision_model() -> str:
    return _resolve_model(os.environ.get("CLAUDE_VISION_MODEL"), DEFAULT_VISION_MODEL)


def get_text_model() -> str:
    return _resolve_model(os.environ.get("CLAUDE_TEXT_MODEL"), DEFAULT_TEXT_MODEL)


def get_detail_model() -> str:
    return _resolve_model(os.environ.get("CLAUDE_DETAIL_MODEL"), DEFAULT_DETAIL_MODEL)


def get_model() -> str:
    return get_text_model()


def get_api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY")


def get_client() -> anthropic.Anthropic:
    api_key = get_api_key()
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is not set")
    return anthropic.Anthropic(api_key=api_key)


def extract_json(text: str) -> dict:
    """Pull the first {...} block from the response and parse it."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in response: {text!r}")
    return json.loads(match.group())


def _log_response(label: str, text: str, stop_reason: str) -> None:
    head = text[:200].replace("\n", " ")
    tail = text[-200:].replace("\n", " ")
    print(f"  [{label}] stop_reason={stop_reason!r}  chars={len(text):,}")
    print(f"  [{label}] HEAD: {head}")
    print(f"  [{label}] TAIL: {tail}")
    if stop_reason == "max_tokens":
        print(f"  [{label}] WARNING: response hit max_tokens limit — JSON may be truncated")


def generate_text(
    prompt: str,
    *,
    system: str,
    max_tokens: int = 2048,
    label: str = "Claude",
    model: str | None = None,
) -> tuple[dict | None, str | None]:
    """
    Make one Claude text call and parse JSON. Retries once on parse failure.
    Returns (result_dict, None) on success or (None, error_str) on failure.
    """
    model = model or get_text_model()
    print(f"  [{label}] model={model}")
    try:
        client = get_client()
    except ValueError as exc:
        return None, str(exc)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0.2 if "claude-3" in model or "claude-sonnet" in model or "claude-haiku" in model or "claude-opus" in model else 1,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        return None, f"{label} API error: {exc}"

    text = response.content[0].text
    stop_reason = response.stop_reason or "unknown"
    _log_response(label, text, stop_reason)

    try:
        return extract_json(text), None
    except (ValueError, json.JSONDecodeError) as parse_err:
        print(f"  [{label}] JSON parse failed: {parse_err} — retrying")

    retry_prompt = (
        "Your previous response could not be parsed as JSON. "
        "Return ONLY the JSON object — no explanation, no markdown fences, "
        "no truncation. Start with '{' and end with '}'."
    )
    try:
        retry_response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=1,
            system=system,
            messages=[
                {"role": "user",      "content": prompt},
                {"role": "assistant", "content": text},
                {"role": "user",      "content": retry_prompt},
            ],
        )
        retry_text = retry_response.content[0].text
        retry_stop = retry_response.stop_reason or "unknown"
        _log_response(f"{label} RETRY", retry_text, retry_stop)
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

    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        text = response.content[0].text
        return extract_json(text), None
    except (ValueError, json.JSONDecodeError) as exc:
        return None, str(exc)
    except Exception as exc:
        return None, str(exc)
