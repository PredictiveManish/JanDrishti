"""Tests for POST /api/updates/refresh — with the PIB fetch mocked so CI
never depends on pib.gov.in (success, 403-rejection, and crash cases)."""

import json
import shutil

import main


def _seed_status(monkeypatch, tmp_path, last_success="2026-09-15 10:00 UTC"):
    """Isolate the API from repo data: copy datasets into tmp, seed a stale
    last-known-good PIB feed + a prior successful status, and point both the
    API (main.DATA_DIR) and the pipeline at the tmp copies."""
    for f in main.DATA_DIR.glob("*.json"):
        shutil.copy(f, tmp_path / f.name)
    status_path = tmp_path / "pib_status.json"
    out_path = tmp_path / "pib_updates.json"
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main.fetch_pib, "STATUS_PATH", status_path)
    monkeypatch.setattr(main.fetch_pib, "OUT_PATH", out_path)
    out_path.write_text(json.dumps([
        {"title": "stale release", "ministry": "X", "fetched_at": last_success}
    ]), encoding="utf-8")
    status_path.write_text(json.dumps({
        "last_success": last_success, "last_attempt": None,
        "last_error": None, "source": "rss", "item_count": 1,
    }), encoding="utf-8")
    return out_path


def test_refresh_success(client, monkeypatch, tmp_path):
    out_path = _seed_status(monkeypatch, tmp_path)

    def fake_fetch(limit=40, **kwargs):
        items = [{"title": "new release", "ministry": "Y",
                  "fetched_at": "2026-09-15 12:00 UTC"}]
        out_path.write_text(json.dumps(items), encoding="utf-8")
        return {"ok": True, "count": 1, "fetched_at": "2026-09-15 12:00 UTC",
                "source": "rss"}

    monkeypatch.setattr(main.fetch_pib, "fetch_with_retries", fake_fetch)
    r = client.post("/api/updates/refresh")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 1
    assert body["fetched_at"] == "2026-09-15 12:00 UTC"
    assert body["source"] == "rss"
    # and the list endpoint reflects the fresh data
    assert client.get("/api/updates").json()[0]["title"] == "new release"


def test_refresh_403_returns_structured_degradation(client, monkeypatch, tmp_path):
    """pib.gov.in rejects us with 403 — API must answer 200 + ok=false +
    last_good, and must NOT touch the last-known-good file."""
    out_path = _seed_status(monkeypatch, tmp_path)
    before = out_path.read_text()

    def blocked_fetch(limit=40, **kwargs):
        return {"ok": False,
                "error": "rss attempt 3/3: HTTPError: 403 Client Error: Forbidden",
                "last_good": "2026-09-15 10:00 UTC",
                "count": 1}

    monkeypatch.setattr(main.fetch_pib, "fetch_with_retries", blocked_fetch)
    r = client.post("/api/updates/refresh")
    assert r.status_code == 200  # never a raw 502
    body = r.json()
    assert body["ok"] is False
    assert "403" in body["error"]
    assert body["last_good"] == "2026-09-15 10:00 UTC"
    # last-known-good file is untouched
    assert out_path.read_text() == before
    # the stale feed is still served
    updates = client.get("/api/updates").json()
    assert updates[0]["title"] == "stale release"


def test_refresh_crash_is_caught(client, monkeypatch, tmp_path):
    """Even an unexpected pipeline crash returns a structured answer."""
    _seed_status(monkeypatch, tmp_path)

    def crashing_fetch(limit=40, **kwargs):
        raise RuntimeError("unexpected boom")

    monkeypatch.setattr(main.fetch_pib, "fetch_with_retries", crashing_fetch)
    r = client.post("/api/updates/refresh")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "boom" in body["error"]
