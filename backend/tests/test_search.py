"""Tests for GET /api/search — the unified cross-dataset search endpoint."""


# ── helpers ──────────────────────────────────────────────────────────────────

EXPECTED_CATEGORIES = {"ministers", "ministries", "states", "budget_items",
                       "pib_updates", "vigilance"}


def _search(client, q, expected_status=200):
    r = client.get("/api/search", params={"q": q})
    assert r.status_code == expected_status, (
        f"q={q!r}: expected HTTP {expected_status}, got {r.status_code}\n{r.text}"
    )
    return r.json()


# ── structure tests ───────────────────────────────────────────────────────────

def test_search_response_shape(client):
    """Envelope always contains query, total_matches, and all 6 category keys."""
    data = _search(client, "defence")
    assert data["query"] == "defence"
    assert isinstance(data["total_matches"], int)
    assert set(data["results"].keys()) == EXPECTED_CATEGORIES


def test_search_total_matches_is_sum_of_categories(client):
    """total_matches must equal the sum of all per-category lists."""
    data = _search(client, "finance")
    computed = sum(len(v) for v in data["results"].values())
    assert data["total_matches"] == computed


# ── correctness tests ─────────────────────────────────────────────────────────

def test_search_ministers_by_name(client):
    """Searching 'rajnath' should surface Rajnath Singh in ministers."""
    data = _search(client, "rajnath")
    ministers = data["results"]["ministers"]
    assert any("Rajnath" in m["name"] for m in ministers), (
        f"Expected Rajnath Singh in ministers results; got: {ministers}"
    )


def test_search_ministers_by_portfolio(client):
    """Searching by portfolio keyword 'defence' should return Rajnath Singh."""
    data = _search(client, "defence")
    ministers = data["results"]["ministers"]
    assert any("Rajnath" in m["name"] for m in ministers), (
        f"Expected Rajnath Singh via portfolio match; got: {ministers}"
    )
    # every returned minister result must have the required fields
    for m in ministers:
        assert "name" in m and "portfolio" in m and "category" in m


def test_search_ministries(client):
    """Searching 'agriculture' should return the Agriculture ministry."""
    data = _search(client, "agriculture")
    ministries = data["results"]["ministries"]
    assert any("Agriculture" in m["ministry"] for m in ministries), (
        f"Expected Agriculture ministry; got: {ministries}"
    )
    for m in ministries:
        assert "ministry" in m and "minister_in_charge" in m


def test_search_states_by_name(client):
    """Searching 'kerala' should return Kerala in states."""
    data = _search(client, "kerala")
    states = data["results"]["states"]
    assert any(s["state"] == "Kerala" for s in states), (
        f"Expected Kerala in states; got: {states}"
    )
    for s in states:
        assert "state" in s and "chief_minister" in s


def test_search_states_by_chief_minister(client):
    """Searching a CM's name should surface their state."""
    # Chandrababu Naidu is CM of Andhra Pradesh
    data = _search(client, "naidu")
    states = data["results"]["states"]
    assert any("Andhra Pradesh" in s["state"] for s in states), (
        f"Expected Andhra Pradesh via CM name; got: {states}"
    )


def test_search_budget_ministry_allocation(client):
    """Searching 'railways' should return Railways in budget_items."""
    data = _search(client, "railways")
    items = data["results"]["budget_items"]
    assert any(i["name"] == "Railways" for i in items), (
        f"Expected Railways budget item; got: {items}"
    )
    for item in items:
        assert "type" in item and "name" in item


def test_search_vigilance(client):
    """Searching 'cag' should return at least one CAG vigilance finding."""
    data = _search(client, "cag")
    flags = data["results"]["vigilance"]
    assert len(flags) > 0, "Expected at least one CAG vigilance flag"
    for flag in flags:
        assert "title" in flag and "severity" in flag and "category" in flag


# ── edge cases ────────────────────────────────────────────────────────────────

def test_search_no_results(client):
    """A nonsense query should return 0 matches with correct structure."""
    data = _search(client, "xyzzy99999")
    assert data["total_matches"] == 0
    for category_list in data["results"].values():
        assert category_list == []


def test_search_case_insensitive(client):
    """Search must be case-insensitive — DEFENCE and defence return same count."""
    lower = _search(client, "defence")
    upper = _search(client, "DEFENCE")
    mixed = _search(client, "Defence")
    assert lower["total_matches"] == upper["total_matches"] == mixed["total_matches"]


def test_search_too_short_returns_400(client):
    """A single-character query must return HTTP 400."""
    _search(client, "a", expected_status=400)


def test_search_missing_q_param_returns_422(client):
    """Omitting the required `q` parameter must return HTTP 422."""
    r = client.get("/api/search")
    assert r.status_code == 422
