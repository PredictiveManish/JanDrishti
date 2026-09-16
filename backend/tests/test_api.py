"""Endpoint contract tests: every GET route returns 200 with the expected
JSON shape. No network access — all data comes from backend/data/."""

ENDPOINT_CASES = [
    ("/api/health", lambda d: d["status"] == "ok" and "time" in d and "version" in d),
    ("/api/meta", lambda d: d["as_of"] == "2026-09-11" and d["counts"]["ministries"] == 52),
    ("/api/positions", lambda d: len(d) == 19 and all("office" in p and "holder" in p for p in d)),
    ("/api/ministers", lambda d: len(d["cabinet"]) == 30 and len(d["mos_independent_charge"]) == 5),
    ("/api/ministries", lambda d: len(d) == 52 and all("secretary_or_head" in m for m in d)),
    ("/api/states", lambda d: len(d) == 28 and all("chief_minister" in s for s in d)),
    ("/api/uts", lambda d: len(d) == 8),
    ("/api/union-budget", lambda d: set(d) == {"headline", "ministry_budgets", "scheme_budgets"}),
    ("/api/state-budgets", lambda d: len(d) == 28 and all("budget_size_display" in s for s in d)),
    ("/api/expenditure", lambda d: set(d) == {"union_actuals", "scheme_utilisation", "cag_highlights", "states_combined"}),
    ("/api/vigilance", lambda d: len(d) == 15 and all("title" in v and "severity" in v for v in d)),
    ("/api/sources", lambda d: len(d) == 14 and all("url" in s for s in d)),
    ("/api/updates", lambda d: isinstance(d, list) and all("title" in u and "ministry" in u for u in d)),
    ("/api/updates/status", lambda d: "last_success" in d and "last_error" in d),
    ("/api/all", None),  # deep-checked below
]


def test_get_endpoints_return_200_and_shape(client):
    failures = []
    for endpoint, check in ENDPOINT_CASES:
        r = client.get(endpoint)
        if r.status_code != 200:
            failures.append(f"{endpoint}: HTTP {r.status_code}")
            continue
        if check is not None:
            try:
                ok = check(r.json())
            except Exception as e:
                ok = False
                failures.append(f"{endpoint}: shape check raised {e}")
                continue
            if not ok:
                failures.append(f"{endpoint}: shape check failed")
    assert not failures, "\n".join(failures)


def test_all_payload_counts(client):
    d = client.get("/api/all").json()
    assert len(d["ministries"]) == 52
    assert len(d["states"]) == 28
    assert len(d["uts"]) == 8
    assert len(d["cabinetMinisters"]) == 30
    assert len(d["schemeBudgets"]) == 14
    assert len(d["stateBudgets"]) == 28
    assert len(d["pibUpdates"]) > 0
    assert "pibStatus" in d


def test_frontend_served_at_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "JanDrishti" in r.text
    assert "api/all" in r.text  # dynamic data loading is wired in


def test_unknown_api_route_404(client):
    assert client.get("/api/nope").status_code == 404
