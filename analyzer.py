"""
analyzer.py
───────────
Uses Gemini Vision to analyze house photos and return structured condition findings.
Requires GEMINI_API_KEY to be set in the environment.

No API calls are made for local helpers (extract_video_frames uses ffmpeg via subprocess).
"""
from __future__ import annotations

import io
import subprocess
from pathlib import Path
from typing import Optional

from PIL import Image

from gemini_client import generate_vision, get_api_key

# Optional HEIC support
try:
    import pillow_heif as _pillow_heif
    _HEIC_AVAILABLE = True
except ImportError:
    _pillow_heif = None  # type: ignore[assignment]
    _HEIC_AVAILABLE = False

SYSTEM_PROMPT = (
    "You are a forensic pre-listing home inspector and buyer's agent with 20 years of "
    "experience in the Greenville County SC market. Your job is to find everything a "
    "buyer at the $295,000-$305,000 price point would flag, negotiate on, or walk away "
    "from. You have an obsessive eye for detail. You notice things sellers stop seeing "
    "because they live there every day."
)

USER_PROMPT = """\
Analyze this photo of a house interior or exterior. Return a JSON object with exactly \
these fields and no others:

- room_type: string — be specific: "master bathroom", "kitchen", "living room", \
"exterior front", "garage", "laundry room", "half bath", "bonus room", etc.

- condition: string — one of: excellent, good, fair, poor

- finish_quality: string — one of: builder_grade, mid_range, high_end, unknown

- dated_features: list of strings — features that were standard in 1999 but buyers \
in 2026 consider outdated. Look specifically for:
  * Jetted/jacuzzi tubs (buyers prefer walk-in showers)
  * Popcorn or stippled ceilings
  * Brass or gold fixtures and hardware
  * Oak or honey-colored cabinets
  * Laminate countertops
  * Linoleum or vinyl sheet flooring
  * Builder-grade light fixtures (boob lights, basic ceiling fans)
  * Hollow core interior doors
  * Basic white plastic outlet covers and switch plates
  * Single-pane windows
  * Builder-grade hollow core bifold closet doors
  * Garden tubs without a separate shower
  * Cultured marble vanity tops
  * Tile with dated colors (mauve, seafoam, almond, peach)
  * Carpet in any room (buyers at this price expect LVP or hardwood)
  * Wallpaper or wallpaper borders
  * Mirrored closet doors
  * Drop ceilings or suspended tile

- issues: list of strings — specific visible problems a home inspector would document \
or a buyer would request credits for. Be precise: not "water damage" but "brown water \
stain on ceiling approximately 12 inches diameter near HVAC vent, suggesting past or \
active leak". Include: stains, cracks, peeling paint, damaged trim, gaps in caulk, \
missing or broken hardware, signs of moisture, settlement cracks, deferred maintenance.

- deal_risk: string — one of: none, low, medium, high, critical
  * critical = could kill the sale or requires immediate disclosure under SC law
  * high = buyer will request a repair credit or price reduction
  * medium = buyer will notice and factor into their offer
  * low = minor cosmetic only, most buyers overlook
  * none = no issues visible

- upgrades: list of strings — specific improvements to bring this space to the \
$295K-$305K buyer expectation. Not generic: not "update bathroom" but "replace \
garden tub with freestanding soaking tub or convert niche to walk-in tile shower \
with frameless glass door".

- buyer_psychology_notes: list of strings — how a buyer would emotionally react to \
this room during a showing. Examples:
  * "Jacuzzi tub reads as maintenance burden and 1990s dated — buyers under 45 will \
mentally subtract value"
  * "Popcorn ceiling is the first thing buyers photograph to show their agent as a \
negotiating point"
  * "Carpet in master bedroom triggers concern for buyers with allergies or pets"

- inspection_flags: list of strings — items a licensed SC home inspector would call \
out in their written report, including code concerns, safety issues, or deferred \
maintenance that requires further evaluation.

- photo_quality: string — note if photo is too dark, blurry, poorly framed, or \
otherwise limits your ability to assess the space accurately. Write "good" if clear.

Return only valid JSON. No explanation, no markdown, no preamble.\
"""

_MEDIA_TYPES: dict[str, str] = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".webp": "image/webp",
    ".heic": "image/jpeg",  # converted to JPEG in memory before encoding
}

_ERROR_RESULT: dict = {
    "room_type":              None,
    "condition":              None,
    "finish_quality":         None,
    "dated_features":         None,
    "issues":                 None,
    "deal_risk":              None,
    "upgrades":               None,
    "buyer_psychology_notes": None,
    "inspection_flags":       None,
    "photo_quality":          None,
}


def _media_type(path: Path) -> Optional[str]:
    return _MEDIA_TYPES.get(path.suffix.lower())


def analyze_image(image_path: Path) -> dict:
    """
    Send a house photo to Gemini Vision and return structured condition findings.

    Returns a dict with keys: room_type, condition, issues, upgrades, finish_quality.
    On any error (missing file, unsupported type, API failure, bad JSON) returns
    those same keys all set to None plus an "error" key with the message.
    """
    media_type = _media_type(image_path)
    if media_type is None:
        return {**_ERROR_RESULT, "error": f"Unsupported image type: {image_path.suffix!r}"}

    try:
        if image_path.suffix.lower() == ".heic":
            if not _HEIC_AVAILABLE:
                return {**_ERROR_RESULT, "error": "HEIC support requires pillow-heif: pip install pillow-heif"}
            heif = _pillow_heif.read_heif(str(image_path))
            img = Image.frombytes(heif.mode, heif.size, heif.data, "raw")
        else:
            img = Image.open(image_path)

        # Resize so the longest side is at most 2000px, then encode as JPEG
        img = img.convert("RGB")
        img.thumbnail((2000, 2000), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        image_bytes = buf.getvalue()
        mime_type = "image/jpeg"
    except (FileNotFoundError, OSError) as exc:
        return {**_ERROR_RESULT, "error": str(exc)}

    if not get_api_key():
        return {**_ERROR_RESULT, "error": "GEMINI_API_KEY environment variable is not set"}

    result, err = generate_vision(
        image_bytes,
        mime_type,
        USER_PROMPT,
        system=SYSTEM_PROMPT,
        max_tokens=2048,
    )
    if err:
        return {**_ERROR_RESULT, "error": err}
    return result


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
