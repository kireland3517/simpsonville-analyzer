"""
House Media Review
──────────────────
Browses house photos and videos (sourced from Google Photos).
Lets you add notes, tag by room/area, mark favorites, and run AI analysis.
All annotation data is saved locally to media_review.csv — nothing is uploaded.

Run:  streamlit run app.py
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st
from PIL import Image

from analyzer import analyze_image, extract_video_frames
from attom import get_last_sale, get_property_summary
from roi import generate_roi_report

# Optional HEIC support — graceful fallback if not installed
try:
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:
    HEIC_SUPPORTED = False

# ─── Config ───────────────────────────────────────────────────────────────────

CSV_PATH = Path("media_review.csv")
VIDEO_FRAMES_DIR = Path(".video_frames")

IMAGE_EXTS: set[str] = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
VIDEO_EXTS: set[str] = {".mp4"}
ALL_EXTS: set[str] = IMAGE_EXTS | VIDEO_EXTS

THUMB_WIDTH = 400
CSV_COLS = ["filepath", "notes", "tags", "favorite", "analysis"]


# ─── Thumbnail cache ──────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def make_thumbnail(path_str: str, mtime: float) -> Optional[bytes]:
    p = Path(path_str)
    if p.suffix.lower() == ".heic" and not HEIC_SUPPORTED:
        return None
    try:
        with Image.open(p) as img:
            img = img.convert("RGB")
            img.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 2))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=75)
            return buf.getvalue()
    except Exception:
        return None


# ─── CSV persistence ──────────────────────────────────────────────────────────

def load_csv() -> pd.DataFrame:
    if CSV_PATH.exists():
        df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
        # Back-compat: add "analysis" column if it came from an older CSV
        if "analysis" not in df.columns:
            df["analysis"] = ""
        return df
    return pd.DataFrame(columns=CSV_COLS)


def save_csv() -> None:
    st.session_state.df.to_csv(CSV_PATH, index=False)


# ─── Session state ────────────────────────────────────────────────────────────

def init() -> None:
    if "initialized" not in st.session_state:
        st.session_state.df = load_csv()
        st.session_state.initialized = True


# ─── on_change callback ───────────────────────────────────────────────────────

def on_field_change(filepath: str, field: str, widget_key: str) -> None:
    df = st.session_state.df
    idx = df.index[df["filepath"] == filepath]
    if len(idx):
        df.at[idx[0], field] = str(st.session_state[widget_key])
    save_csv()


# ─── AI analysis helpers ──────────────────────────────────────────────────────

def _merge_frame_analyses(results: list[dict]) -> dict:
    """Combine multiple video-frame analyses into one dict for storage."""
    valid = [r for r in results if r.get("error") is None]
    if not valid:
        return results[0] if results else {"error": "no frames could be analyzed"}
    issues = list({i for r in valid for i in (r.get("issues") or [])})
    upgrades = list({u for r in valid for u in (r.get("upgrades") or [])})
    return {
        "room_type":      valid[0].get("room_type"),
        "condition":      valid[0].get("condition"),
        "finish_quality": valid[0].get("finish_quality"),
        "issues":         issues,
        "upgrades":       upgrades,
    }


def _store_analysis(filepath: str, result: dict) -> None:
    df = st.session_state.df
    idx = df.index[df["filepath"] == filepath]
    if len(idx):
        df.at[idx[0], "analysis"] = json.dumps(result)
    save_csv()


# ─── Export helpers ───────────────────────────────────────────────────────────

def export_pdf_report() -> None:
    st.warning("PDF export coming soon")


# ─── App ──────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="House Media Review", layout="wide", page_icon="🏠")
init()

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🏠 House Media Review")

    type_filter: str = st.radio(
        "File type", ["All", "Images", "Videos"], horizontal=True
    )

    all_tags = sorted({
        tag.strip()
        for row in st.session_state.df["tags"]
        for tag in row.split(",")
        if tag.strip()
    })
    tag_filter: list[str] = st.multiselect("Room / area", all_tags)

    fav_only: bool = st.checkbox("⭐ Favorites only")

    st.divider()

    n_cols: int = st.slider("Grid columns", min_value=2, max_value=5, value=3)

    if not HEIC_SUPPORTED:
        st.caption("⚠ HEIC support unavailable. Run `pip install pillow-heif`.")

    st.divider()

    # ── AI Analysis ───────────────────────────────────────────────────────────
    st.markdown("### 🔍 AI Analysis")

    api_key_input: str = st.text_input(
        "Anthropic API Key",
        type="password",
        key="api_key_input",
        placeholder="sk-ant-...",
    )
    if api_key_input:
        os.environ["ANTHROPIC_API_KEY"] = api_key_input

    already_analyzed = st.session_state.df["analysis"].str.strip().ne("").sum()
    st.caption(f"{already_analyzed} / {len(st.session_state.df)} files analyzed")

    run_analysis: bool = st.button(
        "🧠 Analyze All",
        use_container_width=True,
        disabled=not bool(api_key_input),
    )

# ── Analyze All (runs in main area so progress bar renders there) ─────────────

if run_analysis:
    df_all = st.session_state.df
    to_analyze = [
        Path(row["filepath"])
        for _, row in df_all.iterrows()
        if not row["analysis"].strip()
        and Path(row["filepath"]).exists()
    ]

    if not to_analyze:
        st.info("All files already have analysis results. Clear the 'analysis' column to re-run.")
    else:
        progress_bar = st.progress(0.0, text=f"Analyzing 0 / {len(to_analyze)}")
        analyzed_count = 0

        for fp in to_analyze:
            ext = fp.suffix.lower()
            label = fp.name

            if ext in VIDEO_EXTS:
                frames_dir = VIDEO_FRAMES_DIR / fp.stem
                frames = extract_video_frames(fp, frames_dir, every_n_seconds=5)
                if frames:
                    frame_results = [analyze_image(f) for f in frames]
                    result = _merge_frame_analyses(frame_results)
                else:
                    result = {"error": "ffmpeg not found or frame extraction failed"}
            else:
                result = analyze_image(fp)

            _store_analysis(str(fp), result)
            analyzed_count += 1
            progress_bar.progress(
                analyzed_count / len(to_analyze),
                text=f"Analyzing {analyzed_count} / {len(to_analyze)}: {label}",
            )

        progress_bar.empty()
        st.success(f"✅ Analyzed {analyzed_count} file{'s' if analyzed_count != 1 else ''}")

# ── Filter (applied to both tabs) ────────────────────────────────────────────

df: pd.DataFrame = st.session_state.df.copy()

if type_filter == "Images":
    df = df[df["filepath"].apply(lambda p: Path(p).suffix.lower() in IMAGE_EXTS)]
elif type_filter == "Videos":
    df = df[df["filepath"].apply(lambda p: Path(p).suffix.lower() in VIDEO_EXTS)]

if tag_filter:
    def _has_any_tag(tags_str: str) -> bool:
        file_tags = {t.strip() for t in tags_str.split(",") if t.strip()}
        return bool(file_tags & set(tag_filter))
    df = df[df["tags"].apply(_has_any_tag)]

if fav_only:
    df = df[df["favorite"].str.lower() == "true"]

df = df.reset_index(drop=True)

# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_media, tab_roi = st.tabs(["📷 Media", "📊 ROI Report"])

# ── Tab 1: Media grid ─────────────────────────────────────────────────────────

with tab_media:
    if df.empty:
        st.info("No files match the current filters.")
    else:
        st.markdown(f"**{len(df)}** file{'s' if len(df) != 1 else ''} shown")

        for row_start in range(0, len(df), n_cols):
            chunk = df.iloc[row_start : row_start + n_cols]
            cols = st.columns(n_cols)

            for col_idx, (_, rec) in enumerate(chunk.iterrows()):
                fp: str = rec["filepath"]
                path = Path(fp)
                ext = path.suffix.lower()

                with cols[col_idx]:
                    with st.container(border=True):

                        # ── Media preview ─────────────────────────────────────
                        if ext in IMAGE_EXTS:
                            mtime = path.stat().st_mtime if path.exists() else 0.0
                            thumb = make_thumbnail(fp, mtime)
                            if thumb:
                                st.image(thumb, use_container_width=True)
                            elif ext == ".heic":
                                st.warning("HEIC — install `pillow-heif` to preview")
                            else:
                                st.warning("Cannot preview image")

                        elif ext in VIDEO_EXTS:
                            if path.exists():
                                st.video(fp)
                            else:
                                st.error("File not found")

                        # ── File name ──────────────────────────────────────────
                        st.caption(path.name)

                        # ── Analysis badge ─────────────────────────────────────
                        if rec.get("analysis", "").strip():
                            st.caption("✅ analyzed")

                        # ── Favorite ───────────────────────────────────────────
                        fav_key = f"fav|{fp}"
                        st.checkbox(
                            "⭐ Favorite",
                            value=rec["favorite"].strip().lower() == "true",
                            key=fav_key,
                            on_change=on_field_change,
                            args=(fp, "favorite", fav_key),
                        )

                        # ── Tags ───────────────────────────────────────────────
                        tags_key = f"tags|{fp}"
                        st.text_input(
                            "Tags",
                            value=rec["tags"],
                            key=tags_key,
                            placeholder="kitchen, living room, ...",
                            on_change=on_field_change,
                            args=(fp, "tags", tags_key),
                            label_visibility="collapsed",
                        )
                        st.caption("↑ tags  (comma-separated room / area names)")

                        # ── Notes ──────────────────────────────────────────────
                        notes_key = f"notes|{fp}"
                        st.text_area(
                            "Notes",
                            value=rec["notes"],
                            key=notes_key,
                            placeholder="Add notes...",
                            height=80,
                            on_change=on_field_change,
                            args=(fp, "notes", notes_key),
                            label_visibility="collapsed",
                        )

# ── Tab 2: ROI Report ─────────────────────────────────────────────────────────

with tab_roi:
    analyzed_rows = st.session_state.df[
        st.session_state.df["analysis"].str.strip().ne("")
    ]
    st.caption(f"{len(analyzed_rows)} files with analysis available")

    if st.button("📈 Generate ROI Report", use_container_width=False):
        if analyzed_rows.empty:
            st.warning("No analyses available yet — run **Analyze All** first.")
        else:
            analyses: list[dict] = []
            for _, row in analyzed_rows.iterrows():
                try:
                    analyses.append(json.loads(row["analysis"]))
                except (json.JSONDecodeError, ValueError):
                    pass

            with st.spinner("Generating ROI report..."):
                report = generate_roi_report(
                    analyses,
                    get_property_summary(),
                    get_last_sale(),
                )
            st.session_state.roi_report = report

    # ── Display report if available ───────────────────────────────────────────
    if "roi_report" in st.session_state:
        report = st.session_state.roi_report

        if report.get("error"):
            st.error(f"Report error: {report['error']}")
        else:
            upgrades_list: list[dict] = report.get("upgrades") or []
            repairs_list: list[dict] = report.get("repairs") or []

            total_upgrade_cost = sum(
                float(u.get("estimated_cost") or 0) for u in upgrades_list
            )
            critical_repair_count = sum(
                1 for r in repairs_list if r.get("priority") == "critical"
            )

            # ── Metric row ────────────────────────────────────────────────────
            m1, m2, m3 = st.columns(3)
            m1.metric(
                "Estimated ARV",
                f"${report.get('estimated_arv', 0):,.0f}",
            )
            m2.metric(
                "Total Upgrade Cost",
                f"${total_upgrade_cost:,.0f}",
            )
            m3.metric(
                "Critical Repairs",
                critical_repair_count,
            )

            st.divider()

            # ── Upgrades table ────────────────────────────────────────────────
            if upgrades_list:
                st.subheader("Upgrades (sorted by ROI)")
                upgrades_df = pd.DataFrame(upgrades_list)
                display_cols = [
                    c for c in
                    ["name", "estimated_cost", "estimated_value_add", "roi_percent", "priority"]
                    if c in upgrades_df.columns
                ]
                st.dataframe(
                    upgrades_df[display_cols],
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("No upgrade recommendations in this report.")

            # ── Repairs table ─────────────────────────────────────────────────
            if repairs_list:
                st.subheader("Repairs")
                repairs_df = pd.DataFrame(repairs_list)
                display_cols = [
                    c for c in
                    ["name", "estimated_cost", "priority"]
                    if c in repairs_df.columns
                ]
                st.dataframe(
                    repairs_df[display_cols],
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("No repairs identified.")

            # ── Summary ───────────────────────────────────────────────────────
            if report.get("summary"):
                st.info(report["summary"])

            st.divider()

            # ── Export ────────────────────────────────────────────────────────
            if st.button("💾 Export Report"):
                upgrades_export = pd.DataFrame(upgrades_list)
                if not upgrades_export.empty:
                    upgrades_export.insert(0, "type", "upgrade")

                repairs_export = pd.DataFrame(repairs_list)
                if not repairs_export.empty:
                    repairs_export.insert(0, "type", "repair")

                combined = pd.concat(
                    [upgrades_export, repairs_export], ignore_index=True
                )
                roi_csv_path = Path("roi_report.csv")
                combined.to_csv(roi_csv_path, index=False)
                st.success(f"Saved {roi_csv_path}")

                export_pdf_report()
