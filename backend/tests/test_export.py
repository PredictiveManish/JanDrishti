"""Tests for GET /api/export/{dataset} — CSV export endpoint."""
import csv
import io


# ── helpers ──────────────────────────────────────────────────────────────────

SUPPORTED_DATASETS = [
    "state-budgets",
    "ministers",
    "ministries",
    "vigilance",
    "scheme-budgets",
    "positions",
    "sources",
]


def _get_csv(client, dataset, expected_status=200):
    r = client.get(f"/api/export/{dataset}")
    assert r.status_code == expected_status, (
        f"dataset={dataset!r}: expected HTTP {expected_status}, got {r.status_code}\n{r.text}"
    )
    if expected_status == 200:
        assert r.headers["content-type"].startswith("text/csv")
        assert f'filename="{dataset}.csv"' in r.headers.get("content-disposition", "")
    return r


# ── tests ────────────────────────────────────────────────────────────────────

def test_all_supported_datasets_return_200_and_valid_csv(client):
    """Every supported dataset must return 200, CSV headers, and non-empty rows."""
    for ds in SUPPORTED_DATASETS:
        res = _get_csv(client, ds)
        reader = csv.reader(io.StringIO(res.text))
        rows = list(reader)
        assert len(rows) > 1, f"Dataset '{ds}' returned no data rows"
        # First row is header
        assert len(rows[0]) > 0, f"Dataset '{ds}' header is empty"


def test_export_state_budgets_content(client):
    """State budgets export must contain 28 states with proper headers."""
    res = _get_csv(client, "state-budgets")
    reader = csv.DictReader(io.StringIO(res.text))
    rows = list(reader)
    assert len(rows) == 28
    assert "state" in reader.fieldnames
    assert "budget_size_display" in reader.fieldnames
    assert any(r["state"] == "Andhra Pradesh" for r in rows)


def test_export_ministers_content(client):
    """Ministers export must contain cabinet, MoS IC, and MoS entries."""
    res = _get_csv(client, "ministers")
    reader = csv.DictReader(io.StringIO(res.text))
    rows = list(reader)
    assert len(rows) > 50
    assert "name" in reader.fieldnames
    assert "portfolio" in reader.fieldnames
    assert "category" in reader.fieldnames
    categories = {r["category"] for r in rows}
    assert "Cabinet" in categories
    assert "MoS (IC)" in categories
    assert "MoS" in categories


def test_export_vigilance_content(client):
    """Vigilance export must contain 15 red flags."""
    res = _get_csv(client, "vigilance")
    reader = csv.DictReader(io.StringIO(res.text))
    rows = list(reader)
    assert len(rows) == 15
    assert "title" in reader.fieldnames
    assert "severity" in reader.fieldnames


def test_export_invalid_dataset_returns_400(client):
    """An unknown dataset name must return HTTP 400 Bad Request."""
    res = client.get("/api/export/invalid-dataset-name")
    assert res.status_code == 400
    assert "Unknown dataset" in res.json()["detail"]


def test_export_case_insensitive(client):
    """Dataset name should be case-insensitive."""
    res1 = client.get("/api/export/STATE-BUDGETS")
    res2 = client.get("/api/export/state-budgets")
    assert res1.status_code == 200
    assert res1.text == res2.text
