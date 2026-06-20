# Environment Variable Inventory

Current audit of environment variables used by `simpsonville-analyzer` after
removing external photo import support.

## Config Files

| Artifact | Status | Notes |
|---|---|---|
| `.env.example` | Present | Template for local/dev variables; do not commit real secrets |
| `.env` | Local only | Created with placeholders for local development |
| `attom_assessment.json` | Optional local cache | Legacy ATTOM assessment cache |
| `attom_sales.json` | Optional local cache | Legacy ATTOM sales cache |
| `Procfile` | Present | `web: uvicorn main:app --host 0.0.0.0 --port $PORT` |

## Active Variables

| Variable | Required | Used For |
|---|---|---|
| `PORT` | Platform-dependent | HTTP listen port |
| `PROPERTY_ID` | Optional | Default property id; defaults remain `130_kingfisher` in code where applicable |
| `SUPABASE_URL` | Yes for persistence | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Yes for persistence | Supabase service role key |
| `ANTHROPIC_API_KEY` | Yes for AI features | Claude vision, ROI report text, and item detail |
| `CLAUDE_MODEL` | Optional | Override all Claude model selections |
| `CLAUDE_VISION_MODEL` | Optional | Override photo/inventory vision model |
| `CLAUDE_TEXT_MODEL` | Optional | Override ROI/report model |
| `CLAUDE_DETAIL_MODEL` | Optional | Override on-demand detail model |
| `ATTOM_API_KEY` | Optional for startup | Required only when live ATTOM market refresh is implemented/used |
| `OPENAI_API_KEY` | Reserved | Not used by current active app paths |
| `SMARTY_AUTH_ID` | Reserved | Not required for current single-property workflow |
| `SMARTY_AUTH_TOKEN` | Reserved | Not required for current single-property workflow |
| `SMARTY_LICENSE` | Reserved | Not required for current single-property workflow |

## Notes

- External photo import has been removed from active app code.
- Existing `photo_analyses` data remains the photo evidence source in Supabase.
- You should not need to rerun photo analysis unless you add or change local media.
- Use `python run_analysis.py` for local media analysis if new photo analysis is needed.
- Use `python run_inventory.py` for local inventory analysis if needed.
- `OPENAI_API_KEY` and `SMARTY_*` are placeholders for future work only.

## Starter `.env`

```env
# Core app
PORT=8000
PROPERTY_ID=130_kingfisher

# Supabase
SUPABASE_URL=
SUPABASE_SERVICE_KEY=

# Current AI provider
ANTHROPIC_API_KEY=
CLAUDE_MODEL=
CLAUDE_VISION_MODEL=
CLAUDE_TEXT_MODEL=
CLAUDE_DETAIL_MODEL=

# Market data
ATTOM_API_KEY=

# Optional future AI provider
OPENAI_API_KEY=

# Optional future address validation
SMARTY_AUTH_ID=
SMARTY_AUTH_TOKEN=
SMARTY_LICENSE=
```
