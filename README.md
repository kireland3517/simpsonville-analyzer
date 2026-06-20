# Simpsonville Pre-Sale ROI Report

AI-powered pre-sale renovation analysis for 130 Kingfisher Dr, Simpsonville SC 29680. Analyzes property photos with Claude vision, aggregates findings across 130+ images, and generates grounded listing-readiness recommendations anchored to real Greenville SC contractor costs and recent subdivision comps.

> Deployment note: this app assumes a private Railway deployment for trusted users. Mutating endpoints are not protected for public internet exposure.

---

## How It Works

```
 Local media or existing Supabase `photo_analyses`
     │
     ▼
Claude Vision (`CLAUDE_VISION_MODEL`)
  → per-photo: room type, condition, issues, upgrades,
    inspection flags, deal risk, dated features
     │
     ▼ (stored in Supabase: photo_analyses)
     │
build_analysis_summary()
  → aggregates 130+ analyses into weighted frequency scores
  → critical/high issues pinned regardless of frequency
     │
     ▼
Claude Text (`CLAUDE_TEXT_MODEL`) — assessment/report calls
  Call 1: Assessment (ARV, deal killers, timeline, SC notes)
  Call 2: Upgrades  (sorted by ROI)
  Call 3: Repairs   (sorted by priority)
     │
     ▼ (stored in Supabase: roi_report)
     │
FastAPI + static/index.html
  → listing tiers: Must Do / Should Do / Nice To Do / Not Doing
  → on-demand deep dive per item (cached in upgrade_details)
```

---

## Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for Claude vision, report generation, and item detail |
| `SUPABASE_URL` | Yes | Supabase project URL (e.g. `https://xxxxx.supabase.co`) |
| `SUPABASE_SERVICE_KEY` | Yes | Supabase service role key |
| `ATTOM_API_KEY` | No | Optional for app startup; required for future live ATTOM market refresh |
| `OPENAI_API_KEY` | No | Reserved for future OpenAI integration; current app paths do not use it |
| `SMARTY_AUTH_ID` / `SMARTY_AUTH_TOKEN` / `SMARTY_LICENSE` | No | Reserved for future address validation; not required for the current single-property workflow |
| `CLAUDE_MODEL` | No | Optional override for all Claude calls |
| `CLAUDE_VISION_MODEL` | No | Vision model override |
| `CLAUDE_TEXT_MODEL` | No | Text/report model override |
| `CLAUDE_DETAIL_MODEL` | No | Deep-dive model override |

---

## Supabase Tables

| Table | Key | Purpose |
|---|---|---|
| `photo_analyses` | `id` (filename) | Raw Claude vision output per photo — room type, condition, issues, upgrades, inspection flags, deal risk, dated features |
| `roi_report` | `id` (e.g. `standard_general`) | Generated ROI reports for each (detail_level, buyer_profile) combination |
| `upgrade_details` | `id` (item name) + `item_type` | On-demand deep how-to detail cached after first Claude call |

Create tables once in Supabase SQL Editor:

```sql
CREATE TABLE photo_analyses (
    id           TEXT PRIMARY KEY,
    filename     TEXT,
    analysis     JSONB,
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE roi_report (
    id           TEXT PRIMARY KEY,
    report       JSONB,
    generated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE upgrade_details (
    id           TEXT,
    item_type    TEXT,
    detail       JSONB,
    created_at   TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id, item_type)
);

```

---

## The Three Scopes of Work

Each scope is additive — Standard includes everything in Quick Wins, Deep Dive includes everything in Balanced. Items and costs are anchored at the lower level and never re-priced at a higher level.

### Quick Wins (executive)
**"Fix these 3–4 things and list it."**

The highest-leverage repairs and upgrades that stop buyers from walking away at the first showing. Deal-killers only: water stains, broken glass, damaged garage doors, safety hazards, obvious deferred maintenance. No cosmetic projects unless they kill the deal.

- Max 3 upgrades, 3 repairs
- ARV target: $295,000
- Tone: plain, non-technical, encouraging

### Balanced Approach (standard)
**"Meet what buyers expect in the $295K–$305K range."**

Everything in Quick Wins plus what same-era neighbors delivered when they sold. Anchored to actual River Ridge comps:
- 4 Kingfisher Dr — 3/2, 1,891 sqft, built 1996, sold Apr 2026 at $289,000
- 305 Kingfisher Dr — 3/2, 1,382 sqft, built 2000, sold Apr 2024 at $265,000

Market bar: smooth ceilings, updated hardware, neutral paint. Not over-improving — matching what successful same-era listings delivered.

- Max 5 upgrades, 5 repairs
- ARV target: $300,000
- Tone: balanced, informative, practical

### Leave Nothing Behind (deep_dive)
**"Compete with newer homes for top dollar."**

Everything in Balanced plus what closes the 1999-vs-2007 gap. Anchored to:
- 307 Blue Heron Cir — 3/2, 1,782 sqft, built 2007, sold Dec 2025 at $301,000

Addresses full flooring replacement, jacuzzi-to-shower conversion, kitchen counters, and all finishes buyers see in 2000s-built comps. Competing with newer construction, not just same-age homes.

- Max 8 upgrades, 8 repairs
- ARV target: $305,000
- Tone: forensic, precise — advising a client before listing

---

## Scoring & Weighting Logic

`build_analysis_summary()` in `run_roi.py` aggregates 130+ photo analyses into ranked issue and upgrade lists.

### Issue Weighting

Each issue text is normalized to a 5-word keyword fingerprint (`_norm_key()`):
1. Lowercase, strip punctuation
2. Drop tokens containing digits (measurements)
3. Drop filler stop words (`visible`, `noted`, `appears`, articles, prepositions)
4. Drop tokens shorter than 3 characters
5. Keep first 5 remaining words

Issues are then scored by deal risk of the photo they came from:

| deal_risk | Weight |
|---|---|
| critical | 10.0 |
| high | 5.0 |
| medium | 2.0 |
| low | 1.0 |
| none | 0.5 |

The same issue appearing across 5 critical-risk photos accumulates 50.0 points. Top 30 by weighted score feed the repairs prompt. Issues from critical/high photos are pinned to `critical_and_high_issues` and always included regardless of rank.

### Inspection Flag Aggregation

Inspection flags from critical/high photos are extracted separately and injected into the repairs prompt verbatim — giving Claude the exact language a licensed inspector would use (e.g. "recommend full door replacement") rather than letting it interpret from issue text alone.

### Upgrade Ranking

Upgrades are ranked by raw count (how many photos mentioned the same upgrade opportunity), top 30. Jetted/jacuzzi tub references are always pinned regardless of rank because deep_dive requires explicit tub-to-shower conversion.

---

## Deal Killer Criteria

`_enforce_repair_priorities()` in `roi.py` ensures Claude never downgrades a known deal-risk item based on cost or ease of fix. Severity is about buyer impact.

### Mark as CRITICAL if any are true:
- Item will fail FHA/USDA/conventional appraisal
- Item is a known SC disclosure requirement
- Item poses safety risk (electrical, structural, water intrusion)
- Item will trigger immediate price reduction demand from any buyer
- Item involves: exposed wiring, cracked window glass, damaged garage door panels, active water stains, or moisture damage

### Mark as HIGH if:
- Item will appear on home inspection report
- Buyer will request repair credit
- Item is visually obvious to any buyer

### Token-based Detection

Repairs are auto-escalated to critical when their name/description overlaps with:

```python
_CRITICAL_REPAIR_TOKENS = {
    "water", "stain", "moisture", "leak", "intrusion", "mold",
    "garage", "door", "panel", "window", "glass", "crack", "broken",
    "deck", "post", "structural", "split", "electrical", "wiring", "safety",
}
```

---

## Known Repair Facts

`_KNOWN_REPAIR_FACTS` in `roi.py` is a ground-truth block injected into every repairs prompt. It overrides ambiguous inspector flag language and prevents Claude from choosing a cheaper interpretation.

**Current facts (derived from photo analysis of 130 Kingfisher Dr):**

1. **Garage Door — Full Replacement Required**
   Photos show structural crack penetrating through panel material with spider-web fracture pattern, exposed void/interior, and panel separation at the crack. Inspector flag: "recommend full door replacement." Scope: replace full 2-car steel insulated door. Cost: $1,600–$2,400 installed. Panel repair is not an acceptable scope.

2. **Window Glass — Broken Pane Replacement**
   Cracked/broken window glass confirmed in photos. Safety hazard. Scope: replace broken pane(s). Cost: $150–$500 per window.

Update this block if new photos reveal additional confirmed findings.

---

## Greenville SC Cost Anchors

`_GREENVILLE_COST_ANCHORS` in `roi.py` is a 60+ item table of real-world installed costs sourced from Greenville/Simpsonville contractor quotes, HomeAdvisor regional data, and local Lowe's/Home Depot pricing. Labor is 15–20% below national average. Injected into every Claude call.

Selected anchors:

| Item | Cost Range |
|---|---|
| Garage door — full 2-car replacement (steel, insulated) | $1,600–$2,400 |
| Window glass — single-pane replacement (per window) | $150–$350 |
| Ceiling water stain — drywall patch + paint | $300–$700 |
| Popcorn ceiling removal + skim coat — whole house | $2,500–$4,500 |
| Exterior siding repair — wood/hardboard patch | $300–$700 |
| Interior paint — whole house (2,019 sqft) | $3,500–$5,500 |
| Flooring — LVP whole house (2,019 sqft) | $5,000–$9,500 |
| Jetted tub removal + walk-in shower conversion | $4,500–$9,000 |
| Kitchen countertop — granite/quartz (30–40 sqft) | $1,800–$4,000 |
| HVAC replacement (2,019 sqft home) | $5,000–$9,000 |
| Roof replacement (30-yr architectural shingle) | $8,000–$14,000 |

### Cache Invalidation

Every generated report is stamped with `prompt_version` — an 8-character SHA1 hash of `_GREENVILLE_COST_ANCHORS`. When cost anchors are edited, this hash changes automatically. `/report/status` compares each cached report's `prompt_version` to the current value to detect stale reports.

---

## Buyer Profiles

Six profiles shape item selection, priority weighting, and description tone across all three Claude calls.

| Profile | Focus |
|---|---|
| `general` | Balanced — typical Simpsonville buyer, age ~38, household income ~$85K |
| `first_time_buyer` | FHA/USDA appraisal pass/fail items first — repairs over upgrades, move-in ready |
| `young_family` | Safety (handrails, deck structure, electrical), family-friendly features, yard adequacy |
| `downsizer` | Low-maintenance upgrades, single-level living, accessibility, large yard flagged as burden |
| `investor` | ROI math and rental yield — Simpsonville 3/2 rents $1,600–$1,900/mo, durable materials only |
| `relocating_professional` | Move-in ready, modern finishes, competing with new construction ($320K–$380K corridor) |

---

## Report Output Schema

```json
{
  "detail_level": "executive | standard | deep_dive",
  "buyer_profile": "general | first_time_buyer | ...",
  "level_description": "User-facing scope summary",
  "buyer_profile_notes": ["Profile-specific observations"],
  "prompt_version": "8-char SHA1 hash of cost anchors",
  "executive_summary": {
    "current_value": 276810,
    "estimated_arv": 295000,
    "total_investment_low": 0,
    "total_investment_high": 0,
    "net_gain_low": 0,
    "net_gain_high": 0,
    "recommendation": "Plain English 2-3 sentence recommendation",
    "market_position": "How property sits vs comps",
    "disclaimer": "AI analysis disclaimer"
  },
  "project_timeline": {
    "total_weeks_hired": 0,
    "total_weeks_diy": 0,
    "recommended_sequence": ["project 1", "..."],
    "parallel_projects": ["projects that can overlap"],
    "notes": "string"
  },
  "deal_killers": ["Items that could kill the sale"],
  "sc_considerations": ["SC-specific disclosure items"],
  "upgrades": [
    {
      "name": "string",
      "description": "string",
      "materials_cost": 0,
      "labor_cost": 0,
      "estimated_cost": 0,
      "estimated_value_add": 0,
      "roi_percent": 0,
      "priority": "high | medium | low",
      "diy_friendly": true,
      "diy_notes": "string",
      "skill_level": "beginner | intermediate | advanced | professional_only",
      "time_estimate_contractor": "string",
      "time_estimate_diy": "string"
    }
  ],
  "repairs": [
    {
      "name": "string",
      "description": "string",
      "estimated_cost": 0,
      "priority": "critical | high | medium | low",
      "diy_friendly": false,
      "diy_notes": "string",
      "time_estimate_contractor": "string",
      "time_estimate_diy": "string",
      "sc_disclosure_required": false,
      "safety_concern": false
    }
  ]
}
```

---

## Photo Analysis Schema

Each photo analyzed by `analyzer.py` produces:

```json
{
  "room_type": "master bathroom | kitchen | living room | exterior front | garage | ...",
  "condition": "excellent | good | fair | poor",
  "finish_quality": "builder_grade | mid_range | high_end | unknown",
  "dated_features": ["Jetted tub, popcorn ceilings, brass fixtures, oak cabinets, ..."],
  "issues": ["Specific visible problems with location and size — 'brown water stain on ceiling ~12in diameter near HVAC vent'"],
  "deal_risk": "none | low | medium | high | critical",
  "upgrades": ["Specific improvements — 'replace garden tub with walk-in tile shower + frameless glass door'"],
  "buyer_psychology_notes": ["Emotional buyer reaction — 'Jacuzzi tub reads as 1990s dated, buyers under 45 will mentally subtract value'"],
  "inspection_flags": ["Items a licensed SC inspector would document in their written report"],
  "photo_quality": "good | description of limitations"
}
```

---

## API Endpoints

```
GET  /                        Serve static/index.html
POST /analyze                 Removed import endpoint; returns 410 with local-media instructions
POST /analyze/bulk            Removed import endpoint; returns 410 with local-media instructions
GET  /analyze/results         Return all cached analysis results

POST /report                  Generate ROI report (body: {detail_level, buyer_profile})
GET  /report                  Return saved report by ?id= (default: standard_general)
GET  /report/status           Cache status + prompt_version for every report slot
POST /report/invalidate       Delete cached reports (?profile=all or specific profile)
POST /report/regenerate-all   Rebuild all 3 levels for one profile (?profile=general)
GET  /report/export/csv       Download upgrades + repairs as CSV

GET  /upgrade-detail          Deep how-to for one upgrade (?name=, ?description=, ?issues=)
GET  /repair-detail           Deep how-to for one repair  (?name=, ?description=, ?issues=)
GET  /dated-features          Aggregated dated features across all photo analyses
GET  /inspection-flags        Top 20 inspection flags across all photo analyses
```

---

## CLI Usage

### Analyze Photos
```bash
python run_analysis.py   # scan filesystem for images, analyze each, save to Supabase
```

### Generate Reports
```bash
python run_roi.py                                  # standard_general (default)
python run_roi.py --all --buyer general            # all 3 levels for one buyer
python run_roi.py --detail executive --buyer first_time_buyer
python run_roi.py --detail deep_dive --buyer relocating_professional
```

---

## Updating Cached Reports

Cached reports must be regenerated whenever prompts or cost anchors change. Check status, invalidate, and regenerate:

```powershell
# Check which reports are stale
Invoke-RestMethod "https://your-domain/report/status"

# Wipe stale cache
Invoke-RestMethod -Method POST "https://your-domain/report/invalidate?profile=all"

# Regenerate all 3 levels (executive → standard → deep_dive)
# ~9 Claude calls, ~60-90 seconds
Invoke-RestMethod -Method POST "https://your-domain/report/regenerate-all?profile=general"
```

Photo analyses (`photo_analyses` table) are never affected — only the 9-call report generation chain reruns. The 136 vision calls are permanent.

---

## External Services

| Service | Used For | Cost Model |
|---|---|---|
| Anthropic Claude | Vision analysis + report generation + deep dive detail | Per token |
| Supabase | PostgreSQL storage for analyses, reports, detail cache, walkthrough, and decision matrix data | Free tier sufficient for single-property use |
| ATTOM Data | Property AVM and sales history | Local cached JSON now; live refresh can use `ATTOM_API_KEY` |
| Railway | Hosting | Usage-based |

---

## Running Locally

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY
python -m uvicorn main:app --port 8000 --reload
```

Open `http://localhost:8000`.
