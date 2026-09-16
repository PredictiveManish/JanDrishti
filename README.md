# JanDrishti — Indian Government Transparency Platform

A full-stack transparency platform covering every department of the Central
Government and all 28 States + 8 Union Territories: leadership (ministers,
secretaries), budgets (Union + all states), actual expenditure vs budget,
scheme-wise utilisation, CAG audit findings, taxpayer vigilance red flags,
and a live PIB press-release feed.

## Stack

- **Backend**: Python + FastAPI (`backend/main.py`)
  - 13 JSON API endpoints under `/api/*`
  - `POST /api/updates/refresh` runs the PIB pipeline on demand
  - Serves the frontend at `/` — one server, one command
- **Data**: `backend/data/*.json` (12 datasets, as of 11 Sep 2026, sourced
  from pmindia.gov.in, indiabudget.gov.in, cag.gov.in, cga.nic.in, sansad.in,
  pib.gov.in, DoPT, RBI, PRS, ECI/ADR)
- **Pipeline**: `backend/pipelines/fetch_pib.py` — pulls PIB's official RSS
  feed, auto-tags ministries (English + Hindi), writes `pib_updates.json`
- **Frontend**: `frontend/index.html` — single dynamic page, fetches all data
  from `/api/all` at load; dark/light theme toggle (persisted); live
  "Fetch latest releases" button

## Run it

```bash
# 1. Install dependencies (once)
pip install -r requirements.txt

# 2. (Optional) pull the latest PIB press releases
cd backend
python3 pipelines/fetch_pib.py

# 3. Start the server
uvicorn main:app --reload

# 4. Open http://localhost:8000
```

## Keep it live

The frontend re-fetches from the API on every page load, so the pipeline is
the only thing that needs scheduling:

```bash
# Linux/Mac cron — hourly PIB refresh:
0 * * * * cd /path/to/jandrishti/backend && python3 pipelines/fetch_pib.py >> pib.log 2>&1
```

Or click **"Fetch latest releases now"** in the Latest Updates section —
the backend runs the pipeline on demand via `POST /api/updates/refresh`.

## API quick reference

| Endpoint | What it returns |
|---|---|
| `GET /api/health` | Service status |
| `GET /api/meta` | Dataset counts & as-of date |
| `GET /api/positions` | 19 constitutional positions |
| `GET /api/ministers` | PM + 30 Cabinet + 5 MoS(IC) + 35 MoS |
| `GET /api/ministries` | 52 ministries w/ departments, ministers, secretaries |
| `GET /api/states` | 28 states w/ full cabinets |
| `GET /api/uts` | 8 union territories |
| `GET /api/union-budget` | Ministry allocations, scheme BE→RE trails |
| `GET /api/state-budgets` | All 28 state budgets (FY2025-26) |
| `GET /api/expenditure` | Actuals, CGA monthly, scheme utilisation, CAG |
| `GET /api/vigilance` | 15 taxpayer red flags |
| `GET /api/sources` | 14-source data registry |
| `GET /api/updates` | Latest PIB press releases |
| `POST /api/updates/refresh` | Run the PIB pipeline now |
| `GET /api/all` | Everything in one call (used by the frontend) |

Interactive API docs are auto-generated at `http://localhost:8000/docs`.

## Project structure

```
jandrishti/
├── README.md
├── requirements.txt
├── backend/
│   ├── main.py               # FastAPI app
│   ├── data/                 # 12 JSON datasets (the platform's database)
│   └── pipelines/
│       └── fetch_pib.py      # Live PIB RSS pipeline
└── frontend/
    └── index.html            # Dynamic SPA (fetches from the API)
```

## Data provenance

Every record in `backend/data/` carries `sources` and `verification` fields.
Leadership data is anchored to the official PMO Council of Ministers list
(25.07.2026), DoPT secretary lists (01.01.2026–01.02.2026), and MHA AGMUT
lists (16.02.2026). Budget figures come from Union Budget 2025-26 documents,
RBI State Finances (Jan 2026 edition), and PRS analyses. Expenditure actuals
are from CGA monthly accounts and CAG Appropriation Accounts (Report No. 6
of 2026).

## Next pipelines (same pattern as fetch_pib.py)

1. **CGA monthly accounts** — makes "money spent so far" current every month
2. **CAG report tracker** — new audit findings as they're tabled (weekly scrape)
3. **Budget-day processing** — Feb 1 auto-ingest of the new Union Budget
4. **Parliament questions** — daily during sessions (sansad.in)

## Caveats

- PIB's RSS feed currently serves Hindi-language releases; the platform
  displays them as-is with automatic ministry tagging.
- Office-holder data changes with reshuffles — re-verify before production
  use (see the "needs-verification" flags in the data files).
- The pre-populated datasets are a snapshot dated 11 September 2026.
