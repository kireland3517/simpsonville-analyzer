# Environment Variable Inventory

Complete audit of environment variables used in **simpsonville-analyzer** (cloned without the original `.env`). Generated from a full-repo search for `os.getenv`, `os.environ`, `dotenv`, `load_dotenv`, `process.env`, `import.meta.env`, and provider prefixes (`SUPABASE_`, `ATTOM`, `CLAUDE`, `ANTHROPIC`, `GEMINI`, `GOOGLE`, `OPENAI`, `SMARTY`, `TWILIO`, `STRIPE`, `RESEND`).

---

## Config files found (or missing)

| Artifact | Status | Notes |
|---|---|---|
| `.env.example` | **Missing from repo** | Referenced in `README.md` but not committed; this audit adds one |
| `.env` / `.env.local` | Gitignored | Loaded by `python-dotenv` in several entry points |
| `google_credentials.json` | Gitignored file fallback | Alternative to `GOOGLE_CREDENTIALS_JSON` |
| `google_token.json` | Gitignored file fallback | Alternative to `GOOGLE_TOKEN_JSON` |
| `attom_assessment.json` | Gitignored local data | ATTOM cache — no API key or env var |
| `attom_sales.json` | Gitignored local data | ATTOM cache — no API key or env var |
| `Procfile` | Present | `web: uvicorn main:app --host 0.0.0.0 --port $PORT` |
| Railway config (`railway.json`, `railway.toml`) | **Not present** | README mentions Railway hosting; no project config in repo |
| Docker (`Dockerfile`, `docker-compose.yml`) | **Not present** | |
| GitHub Actions (`.github/workflows/`) | **Not present** | No CI secret references |
| Frontend `process.env` / `import.meta.env` | **Not used** | Static `static/index.html` only; no Node build |

---

## Important: active LLM client

Production code (`analyzer.py`, `roi.py`, `run_analysis.py`, `run_inventory.py`, `main.py`) imports **`claude_client`** (Anthropic), not `gemini_client`.

| What docs say | What code does |
|---|---|
| `README.md` lists `GEMINI_API_KEY` as required | Runtime checks `ANTHROPIC_API_KEY` via `claude_client.get_api_key()` |
| `run_roi.py` exits if `GEMINI_API_KEY` is unset | `generate_roi_report()` calls Claude; you need `ANTHROPIC_API_KEY` at call time |
| `app.py` (Streamlit) sets `GEMINI_API_KEY` from UI | `analyze_image()` still reads `ANTHROPIC_API_KEY` |

`gemini_client.py` exists but is **not imported** by any production module.

---

## Full variable table

| Variable | File(s) where used | Required or optional | Purpose | What breaks if missing |
|---|---|---|---|---|
| `ANTHROPIC_API_KEY` | `claude_client.py`, `analyzer.py`, `roi.py`, `run_analysis.py`, `run_inventory.py`, `main.py` (via imports) | **Required** for all AI features | Anthropic API key for vision, ROI reports, and item deep-dive | Photo analysis returns error; report generation fails; `run_analysis.py` / `run_inventory.py` exit at startup |
| `CLAUDE_MODEL` | `claude_client.py` | Optional | Overrides vision, text, and detail models when set | Defaults used (`claude-sonnet-4-6`) |
| `CLAUDE_VISION_MODEL` | `claude_client.py` | Optional | Model for photo vision analysis | Default: `claude-sonnet-4-6` |
| `CLAUDE_TEXT_MODEL` | `claude_client.py` | Optional | Model for ROI report text generation | Default: `claude-sonnet-4-6` |
| `CLAUDE_DETAIL_MODEL` | `claude_client.py` | Optional | Model for on-demand upgrade/repair detail | Default: `claude-sonnet-4-6` |
| `SUPABASE_URL` | `main.py`, `photos.py`, `run_analysis.py`, `run_inventory.py`, `run_roi.py`, `scripts/bulk_update_walkthrough_evidence.py`, `check_report.py` | **Required** for persistence / live DB scripts | Supabase project URL | DB reads/writes fail; walkthrough bulk script exits; report endpoints return 503 when cache is cold |
| `SUPABASE_SERVICE_KEY` | Same as `SUPABASE_URL` | **Required** (with URL) for Supabase | Service-role key (bypasses RLS) | Same as `SUPABASE_URL` |
| `GOOGLE_CREDENTIALS_JSON` | `photos.py` | **Required for Google Photos OAuth** (unless `google_credentials.json` on disk) | OAuth2 client config as a JSON string | `/auth/login` fails when building OAuth flow; `run_inventory.py` (Google mode) cannot authenticate |
| `GOOGLE_TOKEN_JSON` | `photos.py` | Optional | Pre-seeded OAuth token JSON | User must complete OAuth or provide `google_token.json`; `/photos/*` return 401 |
| `REDIRECT_URI` | `photos.py` | Optional | OAuth callback URL | Defaults to `http://localhost:8000/auth/callback`; production OAuth fails if mismatch with Google Cloud console |
| `GEMINI_API_KEY` | `gemini_client.py`, `app.py` (UI sets it), `run_roi.py` (startup check only) | Optional for main app; required only for `gemini_client` / Streamlit UI expectation | Google Gemini API key | Unused by main FastAPI path; `run_roi.py` refuses to start; Streamlit sets it but analyzer still needs `ANTHROPIC_API_KEY` |
| `GOOGLE_API_KEY` | `gemini_client.py`, `run_roi.py` (startup check) | Optional | Alias for `GEMINI_API_KEY` | Same as `GEMINI_API_KEY` for Gemini-only paths |
| `GEMINI_MODEL` | `gemini_client.py` | Optional | Global Gemini model override | Gemini defaults used |
| `GEMINI_VISION_MODEL` | `gemini_client.py` | Optional | Gemini vision model override | Default: `gemini-2.5-flash` |
| `GEMINI_TEXT_MODEL` | `gemini_client.py` | Optional | Gemini text model override | Default: `gemini-2.5-pro` |
| `GEMINI_DETAIL_MODEL` | `gemini_client.py` | Optional | Gemini detail model override | Default: `gemini-2.5-flash` |
| `PORT` | `Procfile` (platform-injected) | Required on Railway/Heroku-style hosts | HTTP listen port for `uvicorn` | Deployed web process fails to bind if unset |

### Searched but not found in code

| Prefix / service | Result |
|---|---|
| `ATTOM_*` | No env vars — reads `attom_assessment.json` / `attom_sales.json` from disk |
| `OPENAI_*` | Not used |
| `SMARTY_*` | Not used |
| `TWILIO_*` | Not used |
| `STRIPE_*` | Not used |
| `RESEND_*` | Not used |
| `process.env` / `import.meta.env` | Not used (no JS bundler) |

---

## Variables by capability

### Launch the app (serve UI)

**Minimum:** none.

`uvicorn main:app` starts without env vars. Static UI at `/` works. Walkthrough, reports, photos, and analysis endpoints degrade gracefully or error at request time.

```bash
python -m uvicorn main:app --port 8000 --reload
```

On Railway/Heroku, the platform sets `PORT` via `Procfile`.

### Connect to Supabase

| Variable | Required |
|---|---|
| `SUPABASE_URL` | Yes |
| `SUPABASE_SERVICE_KEY` | Yes |

Without these: in-memory caches only; OAuth tokens not persisted; walkthrough/report/analysis data not saved or loaded from DB.

### Google Photos (browse / analyze via web app)

| Variable | Required |
|---|---|
| `GOOGLE_CREDENTIALS_JSON` **or** `google_credentials.json` | Yes |
| `GOOGLE_TOKEN_JSON` **or** `google_token.json` **or** OAuth via `/auth/login` | Yes (one of) |
| `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` | Recommended (token + analysis persistence) |
| `REDIRECT_URI` | Yes in production (must match Google Cloud OAuth client) |

This app does **not** upload photos to Supabase storage — it reads from Google Photos API.

### Run photo analysis

| Variable | Required |
|---|---|
| `ANTHROPIC_API_KEY` | Yes |
| `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` | Yes for `run_analysis.py` CLI |
| `CLAUDE_*_MODEL` | Optional |

Local files: place images under `media/` for `python run_analysis.py`.

### Generate ROI reports

| Variable | Required |
|---|---|
| `ANTHROPIC_API_KEY` | Yes (actual LLM calls) |
| `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` | Yes for CLI (`run_roi.py`) and cold-start web reports |
| `GEMINI_API_KEY` | Checked by `run_roi.py` startup only (stale — set `ANTHROPIC_API_KEY` instead) |

ATTOM data comes from local JSON files, not env vars.

### Access ATTOM property data

**No environment variables.**

Requires gitignored files in repo root:

- `attom_assessment.json`
- `attom_sales.json`

If missing, `get_property_summary()` / `get_last_sale()` return empty defaults; reports still generate but with placeholder market value.

### Call Claude (Anthropic)

| Variable | Required |
|---|---|
| `ANTHROPIC_API_KEY` | Yes |
| `CLAUDE_MODEL` | Optional (overrides all tasks) |
| `CLAUDE_VISION_MODEL` | Optional |
| `CLAUDE_TEXT_MODEL` | Optional |
| `CLAUDE_DETAIL_MODEL` | Optional |

### Call Gemini

Only via `gemini_client.py` (unused by main app today):

| Variable | Required |
|---|---|
| `GEMINI_API_KEY` or `GOOGLE_API_KEY` | Yes |
| `GEMINI_*_MODEL` | Optional |

---

## Walkthrough bulk-update script (live database)

Script: `scripts/bulk_update_walkthrough_evidence.py`

### Minimum set for live DB (dry-run or `--apply`)

| Variable | Required |
|---|---|
| `SUPABASE_URL` | Yes |
| `SUPABASE_SERVICE_KEY` | Yes |

No LLM keys, Google OAuth, or ATTOM files are needed. The script only updates `owner_note` and `include_in_report` on existing walkthrough rows.

```bash
# Dry-run against live DB
python scripts/bulk_update_walkthrough_evidence.py

# Write changes
python scripts/bulk_update_walkthrough_evidence.py --apply
```

Loads env from (in order): cwd `.env`, project root `.env`, project root `.env.local`.

### `--simulate` mode

No env vars required — uses in-memory template seeds, not Supabase.

---

## Recommended starter `.env` (full local development)

See `.env.example` in the repo root. Minimal practical set:

```env
ANTHROPIC_API_KEY=sk-ant-...
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_SERVICE_KEY=eyJ...
GOOGLE_CREDENTIALS_JSON={"web":{...}}
GOOGLE_TOKEN_JSON={"token":"...","refresh_token":"..."}
REDIRECT_URI=http://localhost:8000/auth/callback
```

Also obtain (not env vars):

- `attom_assessment.json` and `attom_sales.json` from the original project owner or ATTOM export

---

## `load_dotenv()` entry points

| Module | Loads |
|---|---|
| `main.py` | `.env` (cwd) |
| `run_analysis.py` | `.env` (cwd) |
| `run_inventory.py` | `.env` (cwd) |
| `run_roi.py` | `.env` (cwd) |
| `check_report.py` | `.env` (cwd) |
| `scripts/bulk_update_walkthrough_evidence.py` | `.env`, `ROOT/.env`, `ROOT/.env.local` |

---

## Known documentation drift

1. `README.md` lists Gemini as the active LLM; code uses Anthropic (`ANTHROPIC_API_KEY`).
2. `README.md` references `cp .env.example .env` but `.env.example` was not in the repo (added by this audit).
3. `run_roi.py` docstring and startup guard reference `GEMINI_API_KEY`; runtime uses `claude_client` → `ANTHROPIC_API_KEY`.
4. Error strings in `analyzer.py` / `roi.py` sometimes say `GEMINI_API_KEY` when the check is `ANTHROPIC_API_KEY`.
5. `app.py` (Streamlit) prompts for Gemini key but `analyzer.py` requires Anthropic.
