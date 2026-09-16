#!/usr/bin/env python3
"""
JanDrishti Pipeline #1 — PIB Press Releases (backend edition)
==============================================================
Fetches the latest press releases from the Press Information Bureau (pib.gov.in)
via its official RSS feed and writes them to backend/data/pib_updates.json.

Run manually:
    python3 pipelines/fetch_pib.py --limit 40

Or via the API:
    POST /api/updates/refresh

Or on a schedule (Linux/Mac cron, hourly):
    0 * * * * cd /path/to/jandrishti/backend && python3 pipelines/fetch_pib.py >> pib.log 2>&1

Requirements: requests, feedparser
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    raise SystemExit("Missing dependency: pip install requests")
try:
    import feedparser
except ImportError:
    raise SystemExit("Missing dependency: pip install feedparser")

PIB_RSS_URL = "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "pib_updates.json"


def parse_date(entry):
    for attr in ("published", "updated"):
        val = getattr(entry, attr, None)
        if val:
            try:
                dt = datetime(*val[:6], tzinfo=timezone.utc)
                return dt.strftime("%d %b %Y")
            except Exception:
                pass
    for attr in ("published", "updated", "summary"):
        val = getattr(entry, attr, None)
        if val and isinstance(val, str) and len(val) >= 10:
            return val[:16]
    return datetime.now(timezone.utc).strftime("%d %b %Y")


def clean_summary(text, limit=280):
    if not text:
        return ""
    text = " ".join(text.split())
    for prefix in ("Posted On:", "PIB", "New Delhi,"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "\u2026"
    return text


def extract_prid(link):
    if not link:
        return None
    if "PRID=" in link:
        return link.split("PRID=")[-1].split("&")[0]
    return None


def guess_ministry(title, summary=""):
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


def fetch(limit=40):
    """Fetch PIB RSS and write pib_updates.json. Returns the parsed items."""
    print(f"Fetching PIB RSS: {PIB_RSS_URL}")
    resp = requests.get(PIB_RSS_URL, headers=HEADERS, timeout=30, verify=False)
    resp.raise_for_status()
    print(f"  HTTP {resp.status_code}, {len(resp.content):,} bytes")

    feed = feedparser.parse(resp.content)
    print(f"  Parsed {len(feed.entries)} entries")

    updates = []
    for entry in feed.entries[:limit]:
        title = getattr(entry, "title", "").strip()
        if not title:
            continue
        link = getattr(entry, "link", "")
        summary = clean_summary(getattr(entry, "summary", ""))
        updates.append({
            "title": title,
            "ministry": guess_ministry(title, summary),
            "date": parse_date(entry),
            "url": link,
            "prid": extract_prid(link),
            "summary": summary,
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(updates, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(updates)} updates to {OUT_PATH}")
    return updates


if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    parser = argparse.ArgumentParser(description="Fetch PIB press releases")
    parser.add_argument("--limit", type=int, default=40, help="Max releases to keep")
    args = parser.parse_args()
    fetch(args.limit)
