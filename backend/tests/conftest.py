"""Shared fixtures for the JanDrishti test suite.

Sets a test-safe environment BEFORE importing the app (scheduler off),
puts the backend root on sys.path, and exposes a session-scoped
FastAPI TestClient.
"""
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Test-safe env — set BEFORE `import main` anywhere in the suite.
os.environ["PIB_REFRESH_INTERVAL_MINUTES"] = "0"  # no scheduler during tests
os.environ.setdefault("DEBUG", "0")                # production-style CORS

import pytest
from fastapi.testclient import TestClient

import main  # noqa: E402


@pytest.fixture(scope="session")
def client():
    """FastAPI TestClient with lifespan (scheduler disabled via env above)."""
    with TestClient(main.app) as c:
        yield c
