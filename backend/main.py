#!/usr/bin/env python3
"""
JanDrishti Backend — FastAPI
============================
Serves all platform data as JSON endpoints and hosts the frontend.

Run:
    uvicorn main:app --reload        (from the backend/ directory)
    # then open http://localhost:8000

Endpoints:
    GET  /                      -> frontend (index.html)
    GET  /api/health            -> service status
    GET  /api/meta              -> dataset metadata & counts
    GET  /api/positions         -> constitutional positions
    GET  /api/ministers          -> council of ministers (cabinet, MoS-IC, MoS)
    GET  /api/ministries        -> 52 union ministries w/ secretaries
    GET  /api/states            -> 28 states w/ cabinets
    GET  /api/uts               -> 8 union territories
    GET  /api/union-budget      -> allocations, schemes, taxpayer facts
    GET  /api/state-budgets     -> all 28 state budgets
    GET  /api/expenditure       -> actuals, utilisation, CAG, states combined
    GET  /api/vigilance         -> taxpayer red flags
    GET  /api/sources           -> data source registry
    GET  /api/updates           -> latest PIB press releases
    POST /api/updates/refresh   -> run the PIB pipeline NOW, return fresh items
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
FRONTEND_DIR = BASE_DIR.parent / "frontend"

app = FastAPI(
    title="JanDrishti API",
    description="Indian government transparency platform — central ministries, all 28 states, budgets, expenditure, CAG findings, and a live PIB feed.",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten for production
    allow_methods=["*"],
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


# ── live PIB refresh ────────────────────────────────────────────────────────
class RefreshResponse(BaseModel):
    ok: bool
    count: int
    fetched_at: Optional[str] = None
    message: str


@app.post("/api/updates/refresh")
def refresh_updates():
    """Run the PIB pipeline on demand and return the fresh feed."""
    sys.path.insert(0, str(BASE_DIR / "pipelines"))
    try:
        import fetch_pib  # noqa: PEP8-named module living in pipelines/
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"pipeline import failed: {e}")
    try:
        items = fetch_pib.fetch(limit=40)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"PIB fetch failed: {e}")
    return RefreshResponse(
        ok=True,
        count=len(items),
        fetched_at=items[0]["fetched_at"] if items else None,
        message=f"Fetched {len(items)} press releases from PIB",
    )


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
    uvicorn.run(app, host="0.0.0.0", port=8000)
