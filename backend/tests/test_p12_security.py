"""Phase 12 production security & launch readiness tests.

Covers: emailed password reset flow (no enumeration, single-use, supersede, revoke sessions),
rate limits (forgot / login-fail), admin bootstrap CLI one-time guard, production config guard,
permission bypass attempts, upload magic-byte validation, security headers, disabled accounts +
notify() skip for inactive users.

Uses MONGO_URL / DB_NAME from /app/backend/.env to insert / inspect reset tokens, rate limit
rows, system marker and notifications. No real emails are sent (all test addresses are @example.com
or @remedialflow.dev, which the backend never emails).
"""
import asyncio
import hashlib
import io
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from PIL import Image

load_dotenv("/app/backend/.env")

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://field-evidence-app-1.preview.emergentagent.com").rstrip("/")
API = BASE_URL + "/api"
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
BACKEND_DIR = "/app/backend"

ADMIN = ("admin@remedialflow.dev", "AdminDemo!2026")
INSTALLER = ("installer@remedialflow.dev", "InstallerDemo!2026")
EMPLOYEE = ("employee@remedialflow.dev", "EmployeeDemo!2026")


def _h(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _mongo():
    return AsyncIOMotorClient(MONGO_URL)[DB_NAME]


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.new_event_loop().run_until_complete(coro)


def login(email, password):
    return requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)


def bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(scope="module")
def admin_token():
    r = login(*ADMIN)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def installer_token():
    r = login(*INSTALLER)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def employee_token():
    r = login(*EMPLOYEE)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


# ---------------- Reset flow ----------------
class TestResetFlow:
    def test_full_reset_flow(self, admin_token):
        email = f"test-p12-{uuid.uuid4().hex[:8]}@example.com"
        old_pw = "OldPass!2026"
        new_pw = "NewPass!2026"
        # 1) Create user via admin
        r = requests.post(f"{API}/admin/users",
                          json={"email": email, "name": "P12 Reset User", "role": "installer", "password": old_pw},
                          headers=bearer(admin_token), timeout=15)
        assert r.status_code in (200, 201), r.text
        uid = r.json()["user"]["id"]

        # Login once to establish an access token that should be invalidated after reset
        r_login = login(email, old_pw)
        assert r_login.status_code == 200, r_login.text
        old_access = r_login.json()["access_token"]
        assert requests.get(f"{API}/auth/me", headers=bearer(old_access), timeout=10).status_code == 200

        # 2) /auth/forgot for known & unknown emails returns identical generic message
        r_known = requests.post(f"{API}/auth/forgot", json={"email": email}, timeout=15)
        r_unknown = requests.post(f"{API}/auth/forgot", json={"email": f"nobody-{uuid.uuid4().hex[:8]}@example.com"}, timeout=15)
        assert r_known.status_code == 200 and r_unknown.status_code == 200
        assert r_known.json() == r_unknown.json(), f"enumeration leak: {r_known.json()} vs {r_unknown.json()}"
        assert "token" not in r_known.json() and "reset_code" not in r_known.json()

        # 3) example.com is a no-send domain: directly insert a link token like the emailer would
        async def _work():
            db = _mongo()
            raw = "p12-raw-" + uuid.uuid4().hex
            await db.reset_tokens.insert_one({
                "user_id": uid, "token_hash": _h(raw), "used": False, "kind": "link",
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
            })
            # Also prepare an expired token to test later
            expired_raw = "p12-expired-" + uuid.uuid4().hex
            await db.reset_tokens.insert_one({
                "user_id": uid, "token_hash": _h(expired_raw), "used": False, "kind": "link",
                "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            })
            return raw, expired_raw
        raw, expired_raw = _run(_work())

        # 4) Reset with raw -> 200
        r_reset = requests.post(f"{API}/auth/reset", json={"token": raw, "password": new_pw}, timeout=15)
        assert r_reset.status_code == 200, r_reset.text

        # 5) old password now fails, new works
        assert login(email, old_pw).status_code == 401
        r_new = login(email, new_pw)
        assert r_new.status_code == 200, r_new.text

        # 6) old access token is now 401 (token_version bumped)
        r_me = requests.get(f"{API}/auth/me", headers=bearer(old_access), timeout=10)
        assert r_me.status_code == 401, f"old access token should be invalid: {r_me.status_code}"

        # 7) reusing the same token -> 400
        r_reuse = requests.post(f"{API}/auth/reset", json={"token": raw, "password": new_pw}, timeout=15)
        assert r_reuse.status_code == 400

        # 8) expired token -> 400
        r_exp = requests.post(f"{API}/auth/reset", json={"token": expired_raw, "password": new_pw}, timeout=15)
        assert r_exp.status_code == 400

        # 9) Admin-issued setup code still works once (case-insensitive)
        r_adm = requests.post(f"{API}/admin/users/{uid}/reset", headers=bearer(admin_token), timeout=10)
        assert r_adm.status_code == 200, r_adm.text
        code = r_adm.json()["reset_code"]
        another_pw = "AnotherPass!2026"
        # case-insensitive: send lower-case variant to confirm /reset tries both forms
        r_code = requests.post(f"{API}/auth/reset", json={"token": code.lower(), "password": another_pw}, timeout=15)
        assert r_code.status_code == 200, r_code.text
        assert login(email, another_pw).status_code == 200

        # second use of the admin code -> 400
        r_code2 = requests.post(f"{API}/auth/reset", json={"token": code, "password": "ShouldFail!2026"}, timeout=15)
        assert r_code2.status_code == 400

        # Cleanup: delete the test user & its reset tokens
        async def _cleanup():
            db = _mongo()
            await db.users.delete_one({"email": email})
            await db.reset_tokens.delete_many({"user_id": uid})
            await db.sessions.delete_many({"user_id": uid})
            await db.login_attempts.delete_many({"email": email})
        _run(_cleanup())


# ---------------- Rate limits ----------------
class TestRateLimits:
    def test_forgot_rate_limit(self, admin_token):
        # 4th /auth/forgot for same email within 15min -> 429
        email = f"test-rl-{uuid.uuid4().hex[:8]}@example.com"
        # Clean possible stale rows first
        async def _cleanup_prep():
            db = _mongo()
            await db.rate_limits.delete_many({})
        _run(_cleanup_prep())
        codes = []
        for i in range(5):
            r = requests.post(f"{API}/auth/forgot", json={"email": email}, timeout=15)
            codes.append(r.status_code)
        assert codes[:3] == [200, 200, 200], codes
        assert 429 in codes[3:], f"expected 429 after 3 attempts, got {codes}"
        # Cleanup
        async def _cleanup():
            db = _mongo()
            await db.rate_limits.delete_many({})
        _run(_cleanup())

    def test_login_fail_rate_limit(self, admin_token):
        email = f"test-lrl-{uuid.uuid4().hex[:8]}@example.com"
        # Create a user to attack
        r = requests.post(f"{API}/admin/users",
                          json={"email": email, "name": "RL User", "role": "installer", "password": "GoodPass!2026"},
                          headers=bearer(admin_token), timeout=15)
        assert r.status_code in (200, 201)
        uid = r.json()["user"]["id"]

        async def _cleanup_prep():
            db = _mongo()
            await db.login_attempts.delete_many({"email": email})
            await db.rate_limits.delete_many({})
        _run(_cleanup_prep())

        codes = []
        for i in range(9):
            r = login(email, "WrongPw!1234")
            codes.append(r.status_code)
        assert codes[:8].count(401) == 8, codes
        assert codes[8] == 429, f"expected 429 on 9th attempt, got {codes}"

        async def _cleanup():
            db = _mongo()
            await db.login_attempts.delete_many({"email": email})
            await db.rate_limits.delete_many({})
            await db.users.delete_one({"_id": __import__("bson").ObjectId(uid)})
        _run(_cleanup())

    def test_successful_logins_not_rate_limited(self):
        # Many successful logins for admin should all succeed
        for _ in range(10):
            r = login(*ADMIN)
            assert r.status_code == 200, r.text


# ---------------- Bootstrap CLI ----------------
class TestBootstrapCLI:
    def test_bootstrap_once_then_refused(self):
        async def _clear_marker():
            db = _mongo()
            await db.system.delete_one({"_id": "admin_bootstrap"})
        _run(_clear_marker())

        secret = "a" * 32  # >=24 chars
        wrong = "b" * 32
        email = f"test-owner-{uuid.uuid4().hex[:6]}@example.com"
        pw = "BootstrapPass!2026"

        def run(extra_env):
            env = {**os.environ, **extra_env}
            return subprocess.run(
                [sys.executable, "bootstrap_admin.py", email, "Test Owner"],
                cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=30,
            )

        # 1) wrong secret refused
        r = run({"ADMIN_BOOTSTRAP_SECRET": secret, "BOOTSTRAP_SECRET_INPUT": wrong, "BOOTSTRAP_PASSWORD_INPUT": pw})
        assert r.returncode != 0
        assert "Invalid bootstrap secret" in (r.stderr + r.stdout)

        # 2) demo-domain email refused (marker already cleared at test start)
        env = {**os.environ, "ADMIN_BOOTSTRAP_SECRET": secret, "BOOTSTRAP_SECRET_INPUT": secret, "BOOTSTRAP_PASSWORD_INPUT": pw}
        r_demo = subprocess.run(
            [sys.executable, "bootstrap_admin.py", "admin@remedialflow.dev", "Demo"],
            cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=30,
        )
        assert r_demo.returncode != 0, r_demo.stdout + r_demo.stderr
        combined = (r_demo.stderr + r_demo.stdout).lower()
        assert "demo" in combined or "real owner email" in combined, combined

        # 3) success
        r_ok = run({"ADMIN_BOOTSTRAP_SECRET": secret, "BOOTSTRAP_SECRET_INPUT": secret, "BOOTSTRAP_PASSWORD_INPUT": pw})
        assert r_ok.returncode == 0, r_ok.stdout + r_ok.stderr
        assert "Admin ready" in r_ok.stdout

        # 4) second run refused (marker consumed)
        r_dup = run({"ADMIN_BOOTSTRAP_SECRET": secret, "BOOTSTRAP_SECRET_INPUT": secret, "BOOTSTRAP_PASSWORD_INPUT": pw})
        assert r_dup.returncode != 0
        assert "already completed" in (r_dup.stderr + r_dup.stdout).lower()

        # 5) short password refused (reset marker first since we consumed it; still bootstrap flow validates pw length)
        async def _reset_marker():
            db = _mongo()
            await db.system.delete_one({"_id": "admin_bootstrap"})
            # also deactivate the test owner so next test doesn't collide
            await db.users.update_one({"email": email}, {"$set": {"active": False}})
        _run(_reset_marker())
        r_short = run({"ADMIN_BOOTSTRAP_SECRET": secret, "BOOTSTRAP_SECRET_INPUT": secret, "BOOTSTRAP_PASSWORD_INPUT": "short"})
        assert r_short.returncode != 0
        assert "12 characters" in (r_short.stderr + r_short.stdout).lower() or "password" in (r_short.stderr + r_short.stdout).lower()

        # Final cleanup: remove the marker and deactivate test owner so real owner can bootstrap later
        async def _final():
            db = _mongo()
            await db.system.delete_one({"_id": "admin_bootstrap"})
            await db.users.update_one({"email": email}, {"$set": {"active": False}})
            # Confirm it's gone / deactivated
            m = await db.system.find_one({"_id": "admin_bootstrap"})
            assert m is None
            u = await db.users.find_one({"email": email})
            assert u is None or u.get("active") is False
        _run(_final())


# ---------------- Production config guard ----------------
class TestProductionConfig:
    def _import(self, env):
        full_env = {**os.environ, **env}
        return subprocess.run(
            [sys.executable, "-c", "import server"],
            cwd=BACKEND_DIR, env=full_env, capture_output=True, text=True, timeout=30,
        )

    def test_unsafe_production_import_fails(self):
        r = self._import({"APP_ENV": "production", "DEMO_SEED": "true"})
        assert r.returncode != 0
        out = (r.stderr + r.stdout).lower()
        assert "unsafe production configuration" in out

    def test_safe_production_import_succeeds_and_docs_disabled(self):
        env = {
            "APP_ENV": "production",
            "DEMO_SEED": "false",
            "JWT_SECRET": "x" * 40,
            "APP_PUBLIC_URL": "https://x.com",
            "CORS_ORIGINS": "https://x.com",
        }
        env.pop("EXPOSE_RESET_CODES", None)
        r = subprocess.run(
            [sys.executable, "-c", "import server; print('docs=' + str(server.app.docs_url))"],
            cwd=BACKEND_DIR, env={**os.environ, **env, "EXPOSE_RESET_CODES": ""}, capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 0, r.stderr
        assert "docs=None" in r.stdout, r.stdout


# ---------------- Permissions bypass ----------------
class TestPermissions:
    def test_employee_cannot_reach_admin_endpoints(self, employee_token):
        for path in ("/admin/users", "/admin/activity", "/admin/deleted-projects", "/admin/payments"):
            r = requests.get(f"{API}{path}", headers=bearer(employee_token), timeout=10)
            assert r.status_code == 403, f"{path}: {r.status_code}"

    def test_employee_cannot_patch_user_role(self, employee_token):
        # employee tries to self-promote by hitting admin endpoint
        r = requests.get(f"{API}/auth/me", headers=bearer(employee_token), timeout=10)
        assert r.status_code == 200
        uid = r.json()["id"]
        r2 = requests.patch(f"{API}/admin/users/{uid}", json={"role": "admin"},
                            headers=bearer(employee_token), timeout=10)
        assert r2.status_code == 403

    def test_employee_cannot_put_rates(self, employee_token, admin_token):
        # pick a real project id so we hit the auth gate not a 404
        pr = requests.get(f"{API}/projects", params={"search": "EF-1024"},
                          headers=bearer(admin_token), timeout=10).json()
        pid = pr["items"][0]["id"]
        iid = pr["items"][0].get("installer_id") or "000000000000000000000000"
        r = requests.put(f"{API}/projects/{pid}/rates/{iid}", json={"rate": 25.0},
                         headers=bearer(employee_token), timeout=10)
        assert r.status_code == 403, f"got {r.status_code}: {r.text}"

    def test_installer_cannot_hit_staff_endpoints(self, installer_token):
        for path in ("/admin/users", "/admin/activity", "/review-queue", "/review-groups"):
            r = requests.get(f"{API}{path}", headers=bearer(installer_token), timeout=10)
            assert r.status_code == 403, f"{path}: {r.status_code}"

    def test_no_auth_401(self):
        for path in ("/auth/me", "/projects", "/admin/users"):
            r = requests.get(f"{API}{path}", timeout=10)
            assert r.status_code in (401, 403), f"{path}: {r.status_code}"


# ---------------- Uploads magic-byte validation ----------------
class TestUploadMagicBytes:
    def _get_installer_nc(self, installer_token):
        pr = requests.get(f"{API}/projects", params={"search": "EF-1024"}, headers=bearer(installer_token), timeout=10).json()
        pid = pr["items"][0]["id"]
        pd = requests.get(f"{API}/projects/{pid}", headers=bearer(installer_token), timeout=10).json()
        nc = next(n for n in pd["ncs"] if n["status"] not in ("approved", "ai_verified"))
        return nc["id"]

    def test_text_as_jpg_rejected_415(self, installer_token):
        nc_id = self._get_installer_nc(installer_token)
        files = {"file": ("x.jpg", b"this is not a real jpeg", "image/jpeg")}
        meta = {"kind": "photo", "source": "in_app_camera", "lat": 51.87, "lng": -0.41, "accuracy_m": 10}
        r = requests.post(f"{API}/ncs/{nc_id}/evidence", headers=bearer(installer_token),
                          files=files, data={"meta": json.dumps(meta)}, timeout=20)
        assert r.status_code == 415, f"expected 415, got {r.status_code}: {r.text}"

    def test_real_jpeg_accepted(self, installer_token):
        nc_id = self._get_installer_nc(installer_token)
        img = Image.new("RGB", (64, 64), (10, 20, 30))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        files = {"file": ("ok.jpg", buf.getvalue(), "image/jpeg")}
        meta = {"kind": "photo", "source": "in_app_camera", "lat": 51.87, "lng": -0.41, "accuracy_m": 10,
                "client_id": str(uuid.uuid4())}
        r = requests.post(f"{API}/ncs/{nc_id}/evidence", headers=bearer(installer_token),
                          files=files, data={"meta": json.dumps(meta)}, timeout=30)
        assert r.status_code == 200, r.text


# ---------------- Security headers ----------------
class TestSecurityHeaders:
    def test_api_response_has_security_headers(self):
        r = requests.post(f"{API}/auth/forgot", json={"email": "test-hdr@example.com"}, timeout=15)
        assert r.headers.get("X-Content-Type-Options", "").lower() == "nosniff"
        assert r.headers.get("X-Frame-Options", "").upper() == "DENY"
        assert "max-age" in r.headers.get("Strict-Transport-Security", "")


# ---------------- Disabled account ----------------
class TestDisabledAccount:
    def test_disable_token_401_and_no_notification(self, admin_token):
        email = f"test-dis-{uuid.uuid4().hex[:8]}@example.com"
        r = requests.post(f"{API}/admin/users",
                          json={"email": email, "name": "Disable User", "role": "installer", "password": "DisPass!2026"},
                          headers=bearer(admin_token), timeout=15)
        assert r.status_code in (200, 201)
        uid = r.json()["user"]["id"]

        # Login
        r_login = login(email, "DisPass!2026")
        assert r_login.status_code == 200
        access = r_login.json()["access_token"]
        refresh = r_login.json().get("refresh_token")
        assert requests.get(f"{API}/auth/me", headers=bearer(access), timeout=10).status_code == 200

        # Deactivate
        r_pat = requests.patch(f"{API}/admin/users/{uid}", json={"active": False},
                               headers=bearer(admin_token), timeout=10)
        assert r_pat.status_code == 200, r_pat.text

        # Access token 401 immediately
        r_me = requests.get(f"{API}/auth/me", headers=bearer(access), timeout=10)
        assert r_me.status_code == 401

        # Refresh 401
        if refresh:
            r_ref = requests.post(f"{API}/auth/refresh", json={"refresh_token": refresh}, timeout=10)
            assert r_ref.status_code == 401

        # Try to assign an NC to this inactive installer and verify no notification
        # Pick any NC not already assigned to this user
        proj = requests.get(f"{API}/projects", params={"search": "EF-1024"},
                            headers=bearer(admin_token), timeout=10).json()
        pid = proj["items"][0]["id"]
        pd = requests.get(f"{API}/projects/{pid}", headers=bearer(admin_token), timeout=10).json()
        nc_id = pd["ncs"][0]["id"]
        async def _count_before():
            db = _mongo()
            return await db.notifications.count_documents({"user_id": uid})
        before = _run(_count_before())
        requests.post(f"{API}/ncs/{nc_id}/assign", json={"installer_id": uid},
                      headers=bearer(admin_token), timeout=10)
        async def _count_after():
            db = _mongo()
            return await db.notifications.count_documents({"user_id": uid})
        after = _run(_count_after())
        assert after == before, f"inactive user received notification: before={before} after={after}"

        # Cleanup: delete the test user and any notifications
        async def _cleanup():
            db = _mongo()
            await db.users.delete_one({"email": email})
            await db.notifications.delete_many({"user_id": uid})
            await db.sessions.delete_many({"user_id": uid})
        _run(_cleanup())
