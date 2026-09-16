# JanDrishti — Indian Government Transparency Platform

A full-stack transparency platform covering every department of the Central
Government and all 28 States + 8 Union Territories: leadership (ministers,
secretaries), budgets (Union + all states), actual expenditure vs budget,
scheme-wise utilisation, CAG audit findings, taxpayer vigilance red flags,
and a live PIB press-release feed.

## Stack

- **Backend**: Python + FastAPI (`backend/main.py`)
  - 15 JSON API endpoints under `/api/*`
  - `POST /api/updates/refresh` runs the PIB pipeline on demand
  - In-process scheduler (APScheduler) keeps the PIB feed fresh — no
    external cron needed
  - Serves the frontend at `/` — one server, one command
- **Data**: `backend/data/*.json` (snapshot as of 11 Sep 2026, sourced
  from pmindia.gov.in, indiabudget.gov.in, cag.gov.in, cga.nic.in, sansad.in,
  pib.gov.in, DoPT, RBI, PRS, ECI/ADR)
- **Pipeline**: `backend/pipelines/fetch_pib.py` — pulls PIB's official RSS
  feed (with an AllRelease.aspx fallback), auto-tags ministries
  (English + Hindi), writes `pib_updates.json`
- **Frontend**: `frontend/index.html` — single dynamic page, fetches all
  data from `/api/all` at load; dark/light theme toggle (persisted); live
  "Fetch latest releases" button with stale-feed error state

## Run it (local)

```bash
# 1. Install dependencies (once)
pip install -r requirements.txt

# 2. Start the server
cd backend
uvicorn main:app --reload

# 3. Open http://localhost:8000
```

The PIB feed refreshes itself on a timer while the server runs (see
`PIB_REFRESH_INTERVAL_MINUTES` below), and the "Fetch latest releases
now" button in the Latest Updates section triggers an immediate refresh.

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `DEBUG` | off | `1`/`true` enables dev mode: CORS allows all origins |
| `ALLOWED_ORIGINS` | *(empty)* | Comma-separated CORS allowlist, e.g. `https://jandrishti.in,https://www.jandrishti.in`. If unset and not in DEBUG, only same-origin requests are allowed (the frontend is served by this app) |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Bind port |
| `PIB_REFRESH_INTERVAL_MINUTES` | `60` | Fetch PIB releases every N minutes via the in-process scheduler. `0` disables |
| `PIB_INITIAL_REFRESH_DELAY_SECONDS` | `30` | Delay before the first scheduled fetch after startup |

## The PIB pipeline and bot-detection

`pib.gov.in` intermittently rejects scripted clients with **403 Forbidden**
(bot detection). The pipeline is built for this:

1. Tries the official **RSS feed** first, using a browser-like session
   (full header set, keep-alive).
2. Retries each source **3 times with exponential backoff** (2s, 4s, …).
3. Falls back to the official **AllRelease.aspx HTML listing** if the RSS
   is blocked.
4. If everything fails, it **keeps serving the last-known-good
   `pib_updates.json`**, records the error in `pib_status.json`, and
   the API responds with a structured `ok: false` + `last_good`
   timestamp — never a raw 502. The frontend shows a visible
   "stale feed" state with the last successful fetch time.

Pipeline health is exposed at `GET /api/updates/status`
(`last_success`, `last_attempt`, `last_error`).

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
| `GET /api/updates/status` | Pipeline health (last success/error) |
| `POST /api/updates/refresh` | Run the PIB pipeline now (structured result) |
| `GET /api/all` | Everything in one call (used by the frontend) |

Interactive API docs are auto-generated at `http://localhost:8000/docs`.

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

The suite (11 tests) covers every GET endpoint's status code and JSON
shape, the frontend being served at `/`, and the PIB refresh path with
**mocked** PIB fetches — success, 403 rejection, and crash cases — so CI
never depends on pib.gov.in. Pipeline parsers (RSS, HTML listing) are
unit-tested against local fixtures.

## Deploying

### Docker

```bash
docker build -t jandrishti .
docker run -p 8000:8000 \
  -e ALLOWED_ORIGINS="https://yourdomain.in" \
  -e PIB_REFRESH_INTERVAL_MINUTES=60 \
  jandrishti
```

The image is a two-stage build: dependencies install in a builder stage,
and the runtime stage is slim, runs as a non-root user (uid 10001), and
contains no build toolchain. Set `-e PORT=...` if your platform needs a
different port.

### Render / Railway / Heroku (Procfile)

The repo ships a `Procfile`, so PaaS platforms can deploy it directly:

1. Push this repo to GitHub.
2. Create a **Web Service** (Render) / app (Railway) from the repo —
   the platform detects the Procfile and runs
   `uvicorn main:app --host 0.0.0.0 --port $PORT`.
3. Set `ALLOWED_ORIGINS` to your site's domain in the platform's
   environment settings.

The in-process scheduler means a single web dyno/instance keeps the PIB
feed fresh — no separate worker or external cron required. (If you scale
to multiple instances, set `PIB_REFRESH_INTERVAL_MINUTES=0` on all but
one to avoid redundant fetches.)

## Project structure

```
jandrishti/
├── Dockerfile                 # two-stage, non-root runtime
├── Procfile                   # PaaS entrypoint
├── .dockerignore / .gitignore
├── pytest.ini
├── requirements.txt
├── backend/
│   ├── main.py                # FastAPI app + scheduler + config
│   ├── data/                  # 13 JSON datasets (the platform's database)
│   ├── pipelines/
│   │   └── fetch_pib.py       # Hardened PIB pipeline (RSS + fallback)
│   └── tests/                 # pytest suite (no network dependency)
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
- The Dockerfile has not been built in CI yet (no Docker available during
  development); it is kept structurally simple — if the build surfaces an
  issue, it will be in the two-stage copy paths, which mirror the Procfile
  layout.
