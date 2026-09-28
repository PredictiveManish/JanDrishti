#!/usr/bin/env python3
"""
JanDrishti Backend — FastAPI
============================
Serves all platform data as JSON endpoints and hosts the frontend.

Configuration (environment variables):
    DEBUG=1                      dev mode: permissive CORS ("*"), verbose
    ALLOWED_ORIGINS              comma-separated CORS allowlist, e.g.
                                 "https://jandrishti.in,https://www.jandrishti.in"
                                 (in DEBUG mode, defaults to "*"; otherwise
                                 same-origin only — the frontend is served by
                                 this app, so cross-origin is blocked)
    HOST=0.0.0.0                 bind address
    PORT=8000                    bind port
    PIB_REFRESH_INTERVAL_MINUTES=60
                                 in-process scheduler: fetch PIB releases
                                 every N minutes while the app runs
                                 (0 disables; first run happens shortly
                                 after startup)
    PIB_INITIAL_REFRESH_DELAY_SECONDS=30
                                 delay before the first scheduled fetch

Run:
    uvicorn main:app --reload        (from the backend/ directory)
    # then open http://localhost:8000

Endpoints:
    GET  /                      -> frontend (index.html)
    GET  /api/health            -> service status
    GET  /api/meta              -> dataset metadata & counts
    GET  /api/positions         -> constitutional positions
    GET  /api/ministers         -> council of ministers (cabinet, MoS-IC, MoS)
    GET  /api/ministries        -> 52 union ministries w/ secretaries
    GET  /api/states            -> 28 states w/ cabinets
    GET  /api/uts               -> 8 union territories
    GET  /api/union-budget      -> allocations, schemes, taxpayer facts
    GET  /api/state-budgets     -> all 28 state budgets
    GET  /api/expenditure       -> actuals, utilisation, CAG, states combined
    GET  /api/vigilance         -> taxpayer red flags
    GET  /api/sources           -> data source registry
    GET  /api/updates           -> latest PIB press releases
    GET  /api/updates/status    -> pipeline health (last success/error)
    POST /api/updates/refresh   -> run the PIB pipeline NOW; returns a
                                   structured result (ok=false + last_good
                                   on upstream failure, never a raw 502)
    GET  /api/search?q=query    -> unified search across all datasets
                                   (ministers, ministries, states, budget,
                                   PIB updates, vigilance); min 2 chars
"""
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# Make the pipelines package importable (backend/pipelines/fetch_pib.py)
_PIPELINE_DIR = Path(__file__).resolve().parent / "pipelines"
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))
import fetch_pib  # noqa: E402  (module-level import so tests can monkeypatch it)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
FRONTEND_DIR = BASE_DIR.parent / "frontend"

log = logging.getLogger("jandrishti.api")

# ── configuration (environment-driven) ────────────────────────────────────────
DEBUG = os.getenv("DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))


def _resolve_cors_origins() -> list[str]:
    """ALLOWED_ORIGINS wins; else "*" only in DEBUG; else none (same-origin)."""
    raw = os.getenv("ALLOWED_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    if DEBUG:
        return ["*"]  # dev convenience only
    # Production default: the frontend is served by this app, so no
    # cross-origin access is needed — CORS blocks everything else.
    return []


ALLOWED_ORIGINS = _resolve_cors_origins()


# ── scheduled PIB refresh (in-process, no external cron needed) ──────────────
def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _scheduled_pib_refresh() -> None:
    """Job body: safe to fail — pipeline handles degradation internally."""
    try:
        result = fetch_pib.fetch_with_retries(limit=40)
        if result.get("ok"):
            log.info("[scheduler] PIB refresh OK: %s releases via %s",
                     result["count"], result["source"])
        else:
            log.warning("[scheduler] PIB refresh failed: %s (keeping feed from %s)",
                        result.get("error"), result.get("last_good"))
    except Exception:  # absolute safety net — the job must never crash the app
        log.exception("[scheduler] PIB refresh job crashed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    interval = _int_env("PIB_REFRESH_INTERVAL_MINUTES", 60)
    initial_delay = _int_env("PIB_INITIAL_REFRESH_DELAY_SECONDS", 30)
    scheduler = None
    if interval > 0:
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.interval import IntervalTrigger

            scheduler = BackgroundScheduler(timezone="UTC")
            scheduler.add_job(
                _scheduled_pib_refresh,
                IntervalTrigger(
                    minutes=interval,
                    start_date=datetime.now(timezone.utc) + timedelta(seconds=initial_delay),
                ),
                id="pib_refresh",
                max_instances=1,
                coalesce=True,
            )
            scheduler.start()
            log.info("PIB scheduler started: every %d min (first run in ~%ds)",
                     interval, initial_delay)
        except ImportError:
            log.warning("APScheduler not installed — scheduled PIB refresh disabled "
                        "(pip install apscheduler)")
    else:
        log.info("PIB scheduler disabled (PIB_REFRESH_INTERVAL_MINUTES=0)")
    log.info("JanDrishti starting | mode=%s | cors_origins=%s | refresh_interval=%s min",
             "debug" if DEBUG else "production", ALLOWED_ORIGINS or "same-origin-only",
             interval if interval > 0 else "off")
    yield
    if scheduler:
        scheduler.shutdown(wait=False)
        log.info("PIB scheduler stopped")


app = FastAPI(
    title="JanDrishti API",
    description=("Indian government transparency platform — central ministries, "
                 "all 28 states, budgets, expenditure, CAG findings, and a live PIB feed."),
    version="1.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ── helpers ──────────────────────────────────────────────────────────────────
def load(name: str) -> Any:
    path = DATA_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"dataset '{name}' not found")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── core endpoints ──────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat(), "version": "1.1.0"}


@app.get("/api/meta")
def meta():
    return load("meta.json")


@app.get("/api/positions")
def positions():
    return load("positions.json")


@app.get("/api/ministers")
def ministers():
    return load("ministers.json")


@app.get("/api/ministries")
def ministries():
    return load("ministries.json")


@app.get("/api/states")
def states():
    return load("states.json")


@app.get("/api/uts")
def uts():
    return load("uts.json")


@app.get("/api/union-budget")
def union_budget():
    return load("union_budget.json")


@app.get("/api/state-budgets")
def state_budgets():
    return load("state_budgets.json")


@app.get("/api/expenditure")
def expenditure():
    return load("expenditure.json")


@app.get("/api/vigilance")
def vigilance():
    return load("vigilance.json")


@app.get("/api/sources")
def sources():
    return load("sources.json")


@app.get("/api/updates")
def updates():
    return load("pib_updates.json")


@app.get("/api/updates/status")
def updates_status():
    """Pipeline health: last success / last attempt / last error."""
    path = DATA_DIR / "pib_status.json"
    if not path.exists():
        return {
            "last_success": None,
            "last_attempt": None,
            "last_error": None,
            "source": None,
            "item_count": 0,
            "note": "pipeline has never run",
        }
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── live PIB refresh ────────────────────────────────────────────────────────
class RefreshResponse(BaseModel):
    ok: bool
    count: int
    fetched_at: str | None = None
    source: str | None = None
    message: str
    error: str | None = None
    last_good: str | None = None


@app.post("/api/updates/refresh")
def refresh_updates():
    """Run the PIB pipeline on demand.

    Returns 200 with ok=False (and the last-known-good details) when
    pib.gov.in rejects us — the client always gets a structured answer.
    """
    try:
        result = fetch_pib.fetch_with_retries(limit=40)
    except Exception as e:  # absolute safety net — never 500 on upstream trouble
        log.exception("PIB refresh crashed unexpectedly")
        items, last_good = fetch_pib._read_last_good()
        return RefreshResponse(
            ok=False, count=len(items), last_good=last_good,
            message="Pipeline error — serving last-known-good feed",
            error=str(e),
        )
    if result.get("ok"):
        return RefreshResponse(
            ok=True, count=result["count"], fetched_at=result["fetched_at"],
            source=result["source"],
            message=f"Fetched {result['count']} press releases from PIB",
        )
    return RefreshResponse(
        ok=False, count=result.get("count", 0),
        last_good=result.get("last_good"),
        error=result.get("error"),
        message=("PIB rejected the request (bot detection) — "
                 "serving last-known-good feed"),
    )


# ── unified search ─────────────────────────────────────────────────────────
@app.get("/api/search")
def global_search(q: str):
    """Search across all datasets in one call.

    Returns results grouped by category: ministers, ministries, states,
    budget line-items, PIB updates, and vigilance flags.

    Query requirements:
    - Minimum 2 characters.
    - Case-insensitive substring match on the most descriptive fields.
    """
    query = q.strip().lower()
    if len(query) < 2:
        raise HTTPException(
            status_code=400,
            detail="Search query must be at least 2 characters.",
        )

    results: dict[str, list[Any]] = {
        "ministers": [],
        "ministries": [],
        "states": [],
        "budget_items": [],
        "pib_updates": [],
        "vigilance": [],
    }

    # 1. Ministers — data is a dict with three category keys, each a list of
    #    [name, portfolio] pairs.
    ministers_data = load("ministers.json")
    category_labels = {
        "cabinet": "Cabinet",
        "mos_independent_charge": "MoS (IC)",
        "mos": "MoS",
    }
    for cat_key, cat_label in category_labels.items():
        for entry in ministers_data.get(cat_key, []):
            name, portfolio = entry[0], entry[1]
            if query in name.lower() or query in portfolio.lower():
                results["ministers"].append({
                    "name": name,
                    "portfolio": portfolio,
                    "category": cat_label,
                })

    # 2. Ministries — list of {ministry, departments[], minister_in_charge,
    #    secretary_or_head}
    for m in load("ministries.json"):
        searchable = " ".join([
            m.get("ministry", ""),
            m.get("minister_in_charge", ""),
            m.get("secretary_or_head", ""),
            " ".join(m.get("departments", [])),
        ]).lower()
        if query in searchable:
            results["ministries"].append({
                "ministry": m.get("ministry"),
                "minister_in_charge": m.get("minister_in_charge"),
                "secretary_or_head": m.get("secretary_or_head"),
                "departments": m.get("departments", []),
            })

    # 3. States — list of {state, capital, chief_minister, governor, …}
    for s in load("states.json"):
        searchable = " ".join([
            s.get("state", ""),
            s.get("chief_minister", ""),
            s.get("governor", ""),
            s.get("capital", ""),
            s.get("party_alliance", ""),
        ]).lower()
        if query in searchable:
            results["states"].append({
                "state": s.get("state"),
                "capital": s.get("capital"),
                "chief_minister": s.get("chief_minister"),
                "governor": s.get("governor"),
                "party_alliance": s.get("party_alliance"),
            })

    # 4. Budget — union ministry allocations and scheme budgets
    budget_data = load("union_budget.json")
    for mb in budget_data.get("ministry_budgets", []):
        if query in mb.get("ministry", "").lower():
            results["budget_items"].append({
                "type": "Ministry Allocation",
                "name": mb.get("ministry"),
                "be_2526_cr": mb.get("be_2526"),
                "be_2425_cr": mb.get("be_2425"),
                "pct_of_budget": mb.get("pct"),
            })
    for sb in budget_data.get("scheme_budgets", []):
        searchable = " ".join([
            sb.get("scheme", ""),
            sb.get("ministry", ""),
        ]).lower()
        if query in searchable:
            results["budget_items"].append({
                "type": "Scheme",
                "name": sb.get("scheme"),
                "ministry": sb.get("ministry"),
                "be_2526_cr": sb.get("be_2526"),
            })

    # 5. PIB updates — list of {title, ministry, date, url, prid}
    for u in load("pib_updates.json"):
        if query in u.get("title", "").lower() or query in u.get("ministry", "").lower():
            results["pib_updates"].append({
                "title": u.get("title"),
                "ministry": u.get("ministry"),
                "date": u.get("date"),
                "url": u.get("url"),
            })

    # 6. Vigilance flags — list of {title, detail, severity, category}
    for v in load("vigilance.json"):
        searchable = " ".join([
            v.get("title", ""),
            v.get("detail", ""),
            v.get("category", ""),
        ]).lower()
        if query in searchable:
            results["vigilance"].append({
                "title": v.get("title"),
                "category": v.get("category"),
                "severity": v.get("severity"),
            })

    total_matches = sum(len(v) for v in results.values())
    return {
        "query": q,
        "total_matches": total_matches,
        "results": results,
    }


# ── aggregated load (single round-trip for the frontend) ───────────────────
@app.get("/api/all")
def all_data():
    return {
        "meta": load("meta.json"),
        "constitutionalPositions": load("positions.json"),
        "cabinetMinisters": load("ministers.json")["cabinet"],
        "mosIC": load("ministers.json")["mos_independent_charge"],
        "mos": load("ministers.json")["mos"],
        "ministries": load("ministries.json"),
        "states": load("states.json"),
        "uts": load("uts.json"),
        "budget": load("union_budget.json")["headline"],
        "ministryBudgets": load("union_budget.json")["ministry_budgets"],
        "schemeBudgets": load("union_budget.json")["scheme_budgets"],
        "stateBudgets": load("state_budgets.json"),
        "unionActuals": load("expenditure.json")["union_actuals"],
        "schemeUtilisation": load("expenditure.json")["scheme_utilisation"],
        "cagHighlights": load("expenditure.json")["cag_highlights"],
        "statesCombined": load("expenditure.json")["states_combined"],
        "vigilance": load("vigilance.json"),
        "dataSources": load("sources.json"),
        "pibUpdates": load("pib_updates.json"),
        "pibStatus": updates_status(),
    }


# ── frontend ────────────────────────────────────────────────────────────────
@app.get("/")
def index():
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return JSONResponse(
        {"detail": "frontend/index.html not found — API is running at /api/health"},
        status_code=404,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
