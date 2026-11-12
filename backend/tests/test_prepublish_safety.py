# Pre-publish safety checks: Push Test Lock default, production config guard, unauthenticated API compatibility (401s).
import asyncio
import os
import subprocess
import sys

import pytest
import requests

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = "http://localhost:8001"


# ---- Push Test Lock default (fake settings collection; preview DB untouched) ----
class _FakeSettings:
    def __init__(self, doc):
        self.doc = doc

    async def find_one(self, *a, **k):
        return self.doc


class _FakeDB:
    def __init__(self, doc):
        self.settings = _FakeSettings(doc)


@pytest.mark.parametrize("doc,expected", [
    (None, True),                              # no settings.global at all
    ({"_id": "global"}, True),                 # settings.global without push_test_lock
    ({"_id": "global", "push_test_lock": None}, True),
    ({"_id": "global", "push_test_lock": True}, True),
    ({"_id": "global", "push_test_lock": False}, False),  # only an explicit False turns it off
])
def test_push_lock_default_on(monkeypatch, doc, expected):
    import push
    monkeypatch.setattr(push, "db", _FakeDB(doc))
    st = asyncio.run(push.push_lock_state())
    assert st["enabled"] is expected
    assert st["allowed"] == ["demo@networksurveyltd.uk"]


# ---- Production config guard (subprocess with env overrides; .env not modified) ----
SAFE = {"APP_ENV": "production", "DEMO_SEED": "false", "JWT_SECRET": "x" * 40,
        "APP_PUBLIC_URL": "https://app.remedialflow.uk", "CORS_ORIGINS": "https://app.remedialflow.uk"}


def _run_check(overrides: dict) -> subprocess.CompletedProcess:
    env = {**os.environ, **SAFE, **overrides}
    env.pop("EXPOSE_RESET_CODES", None)
    if "EXPOSE_RESET_CODES" in overrides:
        env["EXPOSE_RESET_CODES"] = overrides["EXPOSE_RESET_CODES"]
    code = "import security; security.check_production_config(); print('OK')"
    return subprocess.run([sys.executable, "-c", code], cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=60)


def test_prod_config_safe_passes():
    r = _run_check({})
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-500:]


@pytest.mark.parametrize("override,msg", [
    ({"DEMO_SEED": "true"}, "DEMO_SEED must be false"),
    ({"JWT_SECRET": "short"}, "JWT_SECRET must be at least 32"),
    ({"APP_PUBLIC_URL": "http://app.remedialflow.uk"}, "APP_PUBLIC_URL"),
    ({"CORS_ORIGINS": "*"}, "CORS_ORIGINS must not contain *"),
    ({"EXPOSE_RESET_CODES": "true"}, "EXPOSE_RESET_CODES"),
])
def test_prod_config_unsafe_raises(override, msg):
    r = _run_check(override)
    assert r.returncode != 0
    assert "Unsafe production configuration" in r.stderr and msg in r.stderr, r.stderr[-500:]


def test_dev_env_does_not_raise():
    r = _run_check({"APP_ENV": "development", "DEMO_SEED": "true", "JWT_SECRET": "s"})
    assert r.returncode == 0 and "OK" in r.stdout


# ---- Unauthenticated API compatibility (desktop app uses same /api) ----
@pytest.mark.parametrize("method,path", [
    ("GET", "/api/projects"), ("GET", "/api/admin/reminders"), ("GET", "/api/admin/push-lock"),
    ("PUT", "/api/admin/push-lock"), ("POST", "/api/admin/bulk-imports"),
])
@pytest.mark.parametrize("base", [LOCAL, os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/") or None])
def test_unauthenticated_returns_401(base, method, path):
    if not base:
        pytest.skip("public URL not set")
    body = {"enabled": False} if path.endswith("push-lock") and method == "PUT" else {}
    r = requests.request(method, base + path, json=body, timeout=20)
    assert r.status_code == 401, (r.status_code, r.text[:200])
