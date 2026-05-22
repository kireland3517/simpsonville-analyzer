"""
analyzer.py
───────────
Uses Claude Vision to analyze house photos and return structured condition findings.
Requires ANTHROPIC_API_KEY to be set in the environment.

No API calls are made for local helpers (extract_video_frames uses ffmpeg via subprocess).
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

import anthropic

# Optional HEIC support
try:
    import pillow_heif as _pillow_heif
    _HEIC_AVAILABLE = True
except ImportError:
    _pillow_heif = None  # type: ignore[assignment]
    _HEIC_AVAILABLE = False

MODEL = "claude-opus-4-5"

SYSTEM_PROMPT = (
    "You are a real estate condition analyst. Analyze house photos and identify "
    "condition issues, finish quality, and upgrade opportunities."
)

USER_PROMPT = (
    "Analyze this photo of a house. Return a JSON object with exactly these fields:\n"
    "- room_type: string (e.g. kitchen, bathroom, living room, exterior, unknown)\n"
    "- condition: string, one of: excellent, good, fair, poor\n"
    "- issues: list of strings describing specific problems visible\n"
    "- upgrades: list of strings describing upgrades that would add value\n"
    "- finish_quality: string, one of: builder_grade, mid_range, high_end, unknown\n"
    "Return only valid JSON, no explanation."
)

_MEDIA_TYPES: dict[str, str] = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".webp": "image/webp",
    ".heic": "image/jpeg",  # converted to JPEG in memory before encoding
}

_ERROR_RESULT: dict = {
    "room_type":      None,
    "condition":      None,
    "issues":         None,
    "upgrades":       None,
    "finish_quality": None,
}


def _media_type(path: Path) -> Optional[str]:
    return _MEDIA_TYPES.get(path.suffix.lower())


def _extract_json(text: str) -> dict:
    """
    Pull the first {...} block out of the response text and parse it.
    Claude occasionally wraps JSON in markdown fences; this strips them.
    """
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in response: {text!r}")
    return json.loads(match.group())


# ─── Main public function ─────────────────────────────────────────────────────

def analyze_image(image_path: Path) -> dict:
    """
    Send a house photo to Claude Vision and return structured condition findings.

    Returns a dict with keys: room_type, condition, issues, upgrades, finish_quality.
    On any error (missing file, unsupported type, API failure, bad JSON) returns
    those same keys all set to None plus an "error" key with the message.
    """
    media_type = _media_type(image_path)
    if media_type is None:
        return {**_ERROR_RESULT, "error": f"Unsupported image type: {image_path.suffix!r}"}

    if image_path.suffix.lower() == ".heic":
        if not _HEIC_AVAILABLE:
            return {**_ERROR_RESULT, "error": "HEIC support requires pillow-heif: pip install pillow-heif"}
        try:
            import io
            from PIL import Image
            heif = _pillow_heif.read_heif(str(image_path))
            img = Image.frombytes(heif.mode, heif.size, heif.data, "raw")
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            image_data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
        except (FileNotFoundError, OSError) as exc:
            return {**_ERROR_RESULT, "error": str(exc)}
    else:
        try:
            image_data = base64.standard_b64encode(image_path.read_bytes()).decode("utf-8")
        except (FileNotFoundError, OSError) as exc:
            return {**_ERROR_RESULT, "error": str(exc)}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {**_ERROR_RESULT, "error": "ANTHROPIC_API_KEY environment variable is not set"}

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": USER_PROMPT,
                        },
                    ],
                }
            ],
        )
    except anthropic.APIError as exc:
        return {**_ERROR_RESULT, "error": str(exc)}

    response_text = message.content[0].text
    try:
        return _extract_json(response_text)
    except (ValueError, json.JSONDecodeError) as exc:
        return {**_ERROR_RESULT, "error": str(exc)}


# ─── Video frame extraction ───────────────────────────────────────────────────

def extract_video_frames(
    video_path: Path,
    output_dir: Path,
    every_n_seconds: int = 5,
) -> list[Path]:
    """
    Use ffmpeg to extract one frame every `every_n_seconds` seconds from a video.
    Saves frames as JPEGs to output_dir and returns the list of saved paths.
    Returns an empty list if ffmpeg is not installed or the command fails.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    output_pattern = output_dir / f"{video_path.stem}_frame_%04d.jpg"

    cmd = [
        "ffmpeg",
        "-i", str(video_path),
        "-vf", f"fps=1/{every_n_seconds}",
        "-q:v", "2",          # JPEG quality (2 = near-lossless, 31 = worst)
        "-y",                  # overwrite without prompting
        str(output_pattern),
    ]

    try:
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        # ffmpeg is not installed
        return []
    except subprocess.CalledProcessError:
        return []

    return sorted(output_dir.glob(f"{video_path.stem}_frame_*.jpg"))
