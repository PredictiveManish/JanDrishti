"""Unit tests for the PIB pipeline itself (parsers + failure semantics).
All network access is mocked — no dependency on pib.gov.in."""

import json

import fetch_pib

RSS_FIXTURE = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>Press Information Bureau</title>
<item>
  <title>Kisan mela organised by Ministry of Agriculture in Bhopal</title>
  <link>https://pib.gov.in/PressReleasePage.aspx?PRID=2300001</link>
  <description>Agriculture ministry announces new scheme for farmers</description>
  <pubDate>Mon, 15 Sep 2026 08:00:00 GMT</pubDate>
</item>
<item>
  <title>Defence Acquisition Council clears procurement proposal</title>
  <link>https://pib.gov.in/PressReleasePage.aspx?PRID=2300002</link>
  <description>Raksha Mantri chaired the meeting of the DAC</description>
  <pubDate>Mon, 15 Sep 2026 09:30:00 GMT</pubDate>
</item>
</channel></rss>"""

HTML_FIXTURE = """<html><body><ul>
<li><a title='Release One about Railways' href='/PressReleseDetail.aspx?PRID=2301111' target="_self">Release One about Railways</a><span class='publishdatesmall'>Posted on: 14 Sep 2026</li>
<li><a title='\u0930\u093e\u0937\u094d\u091f\u094d\u0930\u092a\u0924\u093f \u0928\u0947 \u092c\u0948\u0920\u0915 \u0930\u0916\u0940' href='/PressReleseDetail.aspx?PRID=2301112' target="_self">\u0930\u093e\u0937\u094d\u091f\u094d\u0930\u092a\u0924\u093f \u0928\u0947 \u092c\u0948\u0920\u0915 \u0930\u0916\u0940</a><span class='publishdatesmall'>Posted on: 14 Sep 2026</li>
</ul></body></html>"""


def test_parse_rss_extracts_items_and_tags_ministries():
    items = fetch_pib.parse_rss(RSS_FIXTURE.encode("utf-8"))
    assert len(items) == 2
    assert items[0]["prid"] == "2300001"
    assert items[0]["ministry"] == "Ministry of Agriculture & Farmers Welfare"
    assert items[1]["ministry"] == "Ministry of Defence"
    assert "15 Sep 2026" in items[0]["date"]


def test_parse_html_listing_extracts_items():
    items = fetch_pib.parse_html_listing(HTML_FIXTURE)
    assert len(items) == 2
    assert items[0]["prid"] == "2301111"
    assert items[0]["ministry"] == "Ministry of Railways"
    assert items[1]["ministry"] == "President's Secretariat"  # Hindi keyword tag
    assert items[1]["url"].startswith("https://pib.gov.in/")


def test_fetch_with_retries_total_failure_keeps_last_good(tmp_path, monkeypatch):
    """Simulate pib.gov.in rejecting every attempt (403): last-known-good
    survives, status records the error, and the result is structured."""
    out_path = tmp_path / "pib_updates.json"
    status_path = tmp_path / "pib_status.json"
    seeded = [{"title": "old", "ministry": "M", "fetched_at": "2026-09-15 06:00 UTC"}]
    out_path.write_text(json.dumps(seeded), encoding="utf-8")
    # a prior successful run's status — must survive the failure
    status_path.write_text(json.dumps({
        "last_success": "2026-09-15 06:00 UTC", "last_attempt": None,
        "last_error": None, "source": "rss", "item_count": 1,
    }), encoding="utf-8")

    monkeypatch.setattr(fetch_pib, "OUT_PATH", out_path)
    monkeypatch.setattr(fetch_pib, "STATUS_PATH", status_path)

    def blocked(*args, **kwargs):
        raise RuntimeError("403 Client Error: Forbidden for url pib.gov.in")

    monkeypatch.setattr(fetch_pib, "_attempt_source", blocked)

    result = fetch_pib.fetch_with_retries(limit=10, attempts=1)

    assert result["ok"] is False
    assert "403" in result["error"]
    assert result["last_good"] == "2026-09-15 06:00 UTC"
    assert result["count"] == 1
    # the last-known-good file is byte-for-byte unchanged
    assert json.loads(out_path.read_text()) == seeded
    # status file records the failure but keeps last_success
    status = json.loads(status_path.read_text())
    assert status["last_success"] == "2026-09-15 06:00 UTC"
    assert "403" in status["last_error"]


def test_fetch_with_retries_success_writes_output(tmp_path, monkeypatch):
    out_path = tmp_path / "pib_updates.json"
    status_path = tmp_path / "pib_status.json"
    monkeypatch.setattr(fetch_pib, "OUT_PATH", out_path)
    monkeypatch.setattr(fetch_pib, "STATUS_PATH", status_path)

    fake_items = [{"title": "fresh", "ministry": "M", "date": "15 Sep 2026",
                   "url": "u", "prid": "1", "summary": ""}]

    def ok_source(session, source):
        return fake_items

    monkeypatch.setattr(fetch_pib, "_attempt_source", ok_source)
    result = fetch_pib.fetch_with_retries(limit=5)

    assert result["ok"] is True
    assert result["count"] == 1
    written = json.loads(out_path.read_text())
    assert written[0]["title"] == "fresh"
    assert written[0]["fetched_at"] == result["fetched_at"]
    status = json.loads(status_path.read_text())
    assert status["last_success"] == result["fetched_at"]
    assert status["source"] == "rss"
