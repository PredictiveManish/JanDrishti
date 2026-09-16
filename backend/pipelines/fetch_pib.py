#!/usr/bin/env python3
"""
JanDrishti Pipeline #1 — PIB Press Releases (hardened)
=======================================================
Fetches the latest press releases from the Press Information Bureau
(pib.gov.in) and writes backend/data/pib_updates.json.

Sources (in order):
  1. PRIMARY:   Official RSS feed  (RssMain.aspx)
  2. FALLBACK:  Official HTML release listing (AllRelease.aspx)

pib.gov.in intermittently rejects scripted clients with 403 Forbidden
(bot detection). This module therefore:
  - uses a browser-like requests.Session with full headers,
  - retries each source with exponential backoff,
  - falls back to the HTML listing if the RSS is blocked,
  - on total failure, KEEPS the last-known-good pib_updates.json and
    records diagnostics in pib_status.json (last attempt/error/success).

API:
  fetch_with_retries(limit)  -> dict  {ok, count, fetched_at?, error?, last_good?, source?}
  fetch(limit)               -> list  (back-compat: items if success, [] if not)

CLI:  python3 pipelines/fetch_pib.py [--limit 40]
"""
import argparse
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

try:
    import feedparser
except ImportError:  # only fatal when actually fetching, keeps module importable
    feedparser = None

log = logging.getLogger("jandrishti.pib")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUT_PATH = DATA_DIR / "pib_updates.json"
STATUS_PATH = DATA_DIR / "pib_status.json"

PRIMARY_URL = "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3"
FALLBACK_URL = "https://pib.gov.in/AllRelease.aspx"

REQUEST_TIMEOUT = (10, 25)  # (connect, read) seconds
MAX_ATTEMPTS = 3
BACKOFF_BASE = 2.0  # seconds; 2, 4 between attempts

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en-GB;q=0.9,en;q=0.8,hi;q=0.7",
    "Referer": "https://pib.gov.in/index.aspx",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}


# ── session ─────────────────────────────────────────────────────────────────
def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    return s


# ── status bookkeeping ──────────────────────────────────────────────────────
def _read_status() -> dict:
    if STATUS_PATH.exists():
        try:
            with open(STATUS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_success": None, "last_attempt": None, "last_error": None,
            "source": None, "item_count": 0}


def _write_status(**updates) -> dict:
    status = _read_status()
    status.update(updates)
    status["last_attempt"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    return status


def _read_last_good():
    """Return (items, fetched_at) of the last-known-good file, or ([], None)."""
    if OUT_PATH.exists():
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                items = json.load(f)
            fetched = items[0].get("fetched_at") if items else None
            return items, fetched
        except (json.JSONDecodeError, OSError):
            pass
    return [], None


# ── parsers ─────────────────────────────────────────────────────────────────
def guess_ministry(title: str, summary: str = "") -> str:
    """Best-effort ministry tagging from title keywords (English + Hindi)."""
    t = (title + " " + summary).lower()
    rules = [
        ("President's Secretariat", ["president of india", "rashtrapati", "\u0930\u093e\u0937\u094d\u091f\u094d\u0930\u092a\u0924\u093f"]),
        ("PMO / Cabinet", ["prime minister", "pmo", "cabinet", "union cabinet", "ccep", "\u092a\u094d\u0930\u0927\u093e\u0928\u092e\u0902\u0924\u094d\u0930\u0940", "\u092e\u0902\u0924\u094d\u0930\u093f\u092a\u0930\u093f\u0937\u0926"]),
        ("Ministry of Finance", ["finance", "tax", "gst", "customs", "budget", "expenditure", "revenue", "\u0935\u093f\u0924\u094d\u0924 \u092e\u0902\u0924\u094d\u0930\u093e\u0932\u092f", "\u0930\u093e\u091c\u0938\u0935"]),
        ("Ministry of Defence", ["defence", "army", "navy", "air force", "drdo", "\u0930\u0915\u094d\u0937\u093e", "\u0938\u0947\u0928\u093e"]),
        ("Ministry of Home Affairs", ["home affairs", "mha", "police", "border security", "bsf", "crpf", "\u0917\u0943\u0939", "\u0906\u0902\u0924\u0930\u093f\u0915"]),
        ("Ministry of External Affairs", ["external affairs", "mea", "diplomat", "bilateral", "g20", "\u0935\u093f\u0926\u0947\u0936"]),
        ("Ministry of Health & Family Welfare", ["health", "medical", "aiims", "ayushman", "vaccin", "icmr", "\u0938\u094d\u0935\u093e\u0938\u094d\u0925\u094d\u092f"]),
        ("Ministry of Education", ["education", "school", "higher education", "ugc", "iit", "neet", "university", "navodaya", "\u0936\u093f\u0915\u094d\u0937\u093e", "\u0935\u093f\u0926\u094d\u092f\u093e\u0932\u092f", "\u0935\u093f\u0926\u094d\u092f\u093e"]),
        ("Ministry of Agriculture & Farmers Welfare", ["agricultur", "farmer", "pm-kisan", "crop", "krishi", "\u0915\u0943\u0937\u093f", "\u0915\u093f\u0938\u093e\u0928", "\u092c\u0940\u091c"]),
        ("Ministry of Rural Development", ["rural development", "mgnrega", "pmay", "gram sadak", "panchayat", "\u0917\u094d\u0930\u093e\u092e\u0940\u0923", "\u0917\u094d\u0930\u093e\u092e"]),
        ("Ministry of Jal Shakti", ["jal shakti", "water", "jal jeevan", "river", "groundwater", "\u091c\u0932 \u0936\u0915\u094d\u0924\u093f"]),
        ("Ministry of Railways", ["railway", "train", "vande bharat", "rail", "\u0930\u0947\u0932", "\u0930\u0947\u0932\u0935\u0947"]),
        ("Ministry of Road Transport & Highways", ["nhai", "highway", "road transport", "bharatmala", "\u0930\u093e\u091c\u092e\u093e\u0930\u094d\u0917", "\u0938\u0921\u093c\u0915"]),
        ("Ministry of Power", ["power", "electricity", "grid", "renewable", "solar", "\u092c\u093f\u091c\u0932\u0940", "\u092a\u0935\u0930"]),
        ("Ministry of Environment, Forest & Climate Change", ["environment", "forest", "climate", "wildlife", "\u092a\u0930\u094d\u092f\u093e\u0935\u0930\u0923", "\u0935\u0928"]),
        ("Ministry of Commerce & Industry", ["commerce", "trade", "export", "import", "dpiit", "\u0935\u093e\u0923\u093f\u091c\u094d\u092f", "\u0909\u0926\u094d\u092f\u094b\u0917"]),
        ("Ministry of MSME", ["msme", "small enterpris", "khadi", "village industr", "\u0938\u0942\u0915\u094d\u0937\u094d\u092e", "\u0932\u0918\u0941"]),
        ("Ministry of Women & Child Development", ["women", "child development", "anganwadi", "poshan", "\u092e\u0939\u093f\u0932\u093e", "\u092c\u093e\u0932"]),
        ("Ministry of Social Justice & Empowerment", ["social justice", "obc", "divyang", "scholarship", "\u0938\u093e\u092e\u093e\u091c\u093f\u0915 \u0928\u094d\u092f\u093e\u092f"]),
        ("Ministry of Tribal Affairs", ["tribal", "adivasi", "vanbandhu", "\u091c\u0928\u091c\u093e\u0924\u093f"]),
        ("Ministry of Labour & Employment", ["labour", "employment", "esic", "epfo", "apprenticeship", "\u0936\u094d\u0930\u092e", "\u0930\u094b\u091c\u0917\u093e\u0930"]),
        ("Ministry of Housing & Urban Affairs", ["urban affairs", "smart city", "amrut", "metro", "housing", "\u0928\u0917\u0930", "\u0906\u0935\u093e\u0938"]),
        ("Ministry of Petroleum & Natural Gas", ["petroleum", "natural gas", "oil", "ongc", "lpg", "\u092a\u0947\u091f\u094d\u0930\u094b\u0932\u093f\u092f\u092e"]),
        ("Ministry of Coal", ["coal", "coal india", "\u0915\u094b\u092f\u0932\u093e"]),
        ("Ministry of Mines", ["mines", "mineral", "geological survey", "\u0916\u0928\u0928"]),
        ("Ministry of Steel", ["steel", "sail", "\u0907\u0938\u094d\u092a\u093e\u0924"]),
        ("Ministry of Civil Aviation", ["aviation", "aircraft", "airline", "aai", "udan", "\u0928\u093e\u0917\u0930\u093f\u0915 \u0949\u0921\u094d\u0921\u092f\u093e\u0928"]),
        ("Ministry of Ports, Shipping & Waterways", ["ports", "shipping", "sagarmala", "\u092c\u0902\u0926\u0930", "\u0938\u092e\u0941\u0926\u094d\u0930"]),
        ("Ministry of Chemicals & Fertilizers", ["fertiliz", "pharma", "chemicals", "medicine price", "\u0909\u0930\u094d\u0935\u0930\u0915", "\u092b\u093e\u0930\u094d\u092e"]),
        ("Ministry of Consumer Affairs", ["consumer", "food and public distribution", "price", "bureau of indian standards", "\u0909\u092a\u092d\u094b\u0915\u094d\u0924\u093e"]),
        ("Ministry of Information & Broadcasting", ["information and broadcasting", "prasar bharati", "dd news", "films", "\u0938\u0942\u091a\u0928\u093e \u092a\u094d\u0930\u0938\u093e\u0930\u0923", "\u092a\u094d\u0930\u0938\u093e\u0930\u0923"]),
        ("Ministry of Electronics & IT", ["electronics", "information technology", "digital india", "semiconductor", "meity", "\u0907\u0932\u0947\u0915\u094d\u091f\u094d\u0930\u0949\u0928\u093f\u0915\u0940", "\u0921\u093f\u091c\u093f\u091f\u0932"]),
        ("Ministry of Communications", ["telecom", "postal", "bsnl", "spectrum", "5g", "\u0938\u0902\u091a\u093e\u0930"]),
        ("Ministry of Science & Technology", ["science and technology", "isro", "space", "research", "dst", "\u0935\u093f\u091c\u094d\u091e\u093e\u0928", "\u0905\u0902\u0924\u0930\u093f\u0915\u094d\u0937", "\u0905\u0902\u0924\u0930\u093f\u0915", "\u092a\u094d\u0930\u092f\u094b\u0917"]),
        ("Ministry of Youth Affairs & Sports", ["sports", "youth affairs", "khelo india", "olympic", "\u0916\u0947\u0932", "\u092f\u0941\u0935\u093e"]),
        ("Ministry of Culture", ["culture", "museum", "heritage", "archaeological", "\u0938\u0902\u0938\u094d\u0915\u0943\u0924\u093f"]),
        ("Ministry of Tourism", ["tourism", "incredible india", "\u092a\u0930\u094d\u092f\u091f\u0928"]),
        ("Ministry of Textiles", ["textile", "handloom", "handicraft", "\u0915\u092a\u0921\u093c\u093e", "\u0939\u0925\u0915\u0930\u0918\u093e"]),
        ("NITI Aayog", ["niti aayog", "niti"]),
    ]
    for ministry, keywords in rules:
        if any(k in t for k in keywords):
            return ministry
    return "Government of India"


def _clean_summary(text, limit=280):
    if not text:
        return ""
    text = " ".join(text.split())
    for prefix in ("Posted On:", "PIB", "New Delhi,"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "\u2026"
    return text


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ── source 1: RSS ───────────────────────────────────────────────────────────
def parse_rss(content: bytes):
    if feedparser is None:
        raise RuntimeError("feedparser is not installed (pip install feedparser)")
    feed = feedparser.parse(content)
    items = []
    for entry in feed.entries:
        title = getattr(entry, "title", "").strip()
        if not title:
            continue
        link = getattr(entry, "link", "")
        summary = _clean_summary(getattr(entry, "summary", ""))
        date = None
        for attr in ("published_parsed", "updated_parsed"):
            val = getattr(entry, attr, None)
            if val:
                try:
                    dt = datetime(*val[:6], tzinfo=timezone.utc)
                    date = dt.strftime("%d %b %Y")
                    break
                except Exception:
                    pass
        items.append({
            "title": title,
            "ministry": guess_ministry(title, summary),
            "date": date or datetime.now(timezone.utc).strftime("%d %b %Y"),
            "url": link,
            "prid": link.split("PRID=")[-1].split("&")[0] if "PRID=" in link else None,
            "summary": summary,
        })
    return items


# ── source 2: HTML release listing ─────────────────────────────────────────
# AllRelease.aspx items look like:
#   <li><a title='TITLE' href='/PressReleseDetail.aspx?PRID=2308717' ...>TITLE</a>
#       <span class='publishdatesmall'>Posted on: 10 Sep 2026</li>
_LISTING_RE = re.compile(
    r"<a\s+title='([^']+)'\s+href='/PressReleseDetail\.aspx\?PRID=(\d+)'[^>]*>.*?"
    r"Posted on:\s*([^<]+)</",
    re.DOTALL,
)


def parse_html_listing(html: str):
    items = []
    for title, prid, date in _LISTING_RE.findall(html):
        title = title.strip()
        if not title or title == "\u092a\u094d\u0930\u0947\u0938 \u0935\u093f\u091c\u094d\u091e\u092a\u094d\u0924\u093f":  # skip 'Press Release' placeholders
            continue
        items.append({
            "title": title,
            "ministry": guess_ministry(title),
            "date": " ".join(date.split()),
            "url": f"https://pib.gov.in/PressReleseDetail.aspx?PRID={prid}",
            "prid": prid,
            "summary": "",
        })
    # newest first (listing is newest-first already, but be safe)
    return items


# ── fetch orchestration ─────────────────────────────────────────────────────
def _attempt_source(session, source):
    """One attempt at one source. Returns list of items."""
    if source == "rss":
        resp = session.get(PRIMARY_URL, timeout=REQUEST_TIMEOUT, verify=False)
        resp.raise_for_status()
        items = parse_rss(resp.content)
    else:
        resp = session.get(FALLBACK_URL, timeout=REQUEST_TIMEOUT, verify=False)
        resp.raise_for_status()
        items = parse_html_listing(resp.text)
    if not items:
        raise RuntimeError(f"{source} source returned 0 parseable releases")
    return items


def fetch_with_retries(limit=40, attempts=None, backoff=None):
    """
    Try RSS then HTML listing, each with retries + exponential backoff.
    On success: write pib_updates.json + status; return success dict.
    On total failure: keep last-known-good, write status with the error;
    return {ok: False, error, last_good, count} — never raises.
    """
    attempts = MAX_ATTEMPTS if attempts is None else attempts
    backoff = BACKOFF_BASE if backoff is None else backoff
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    session = _session()
    last_error = None

    for source in ("rss", "html_listing"):
        for attempt in range(1, attempts + 1):
            try:
                items = _attempt_source(session, source)
                fetched_at = _now()
                out = items[:limit]
                for it in out:
                    it["fetched_at"] = fetched_at
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                with open(OUT_PATH, "w", encoding="utf-8") as f:
                    json.dump(out, f, ensure_ascii=False, indent=2)
                status = _write_status(
                    last_success=fetched_at, last_error=None,
                    source=source, item_count=len(out))
                log.info("PIB fetch OK via %s: %d releases", source, len(out))
                return {
                    "ok": True, "count": len(out), "fetched_at": fetched_at,
                    "source": source, "status": status,
                }
            except Exception as e:  # noqa: BLE001 — record everything, keep trying
                last_error = f"{source} attempt {attempt}/{attempts}: {e.__class__.__name__}: {e}"
                log.warning("PIB fetch failed (%s)", last_error)
                if attempt < attempts:
                    time.sleep(backoff * attempt)

    # total failure — keep last-known-good, record diagnostics
    items, last_good = _read_last_good()
    status = _write_status(last_error=last_error, item_count=len(items))
    log.error("PIB fetch failed on all sources; serving last-known-good from %s",
              last_good or "never")
    return {
        "ok": False, "error": last_error, "last_good": last_good,
        "count": len(items), "status": status,
    }


def fetch(limit=40):
    """Back-compat wrapper: returns the item list ([] on failure)."""
    result = fetch_with_retries(limit=limit)
    if result.get("ok"):
        with open(OUT_PATH, encoding="utf-8") as f:
            return json.load(f)
    return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Fetch PIB press releases")
    parser.add_argument("--limit", type=int, default=40, help="Max releases to keep")
    args = parser.parse_args()
    result = fetch_with_retries(limit=args.limit)
    if result["ok"]:
        print(f"OK: {result['count']} releases via {result['source']} at {result['fetched_at']}")
    else:
        print(f"FAILED: {result['error']}")
        print(f"Serving last-known-good from: {result['last_good'] or 'never'} ({result['count']} items)")
