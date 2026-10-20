"""Final pre-deploy tests — CSRF for cookie endpoints, push registration/unregister,
SEC-001 (change-password revokes all + returns fresh tokens), NC-scoped evidence discard.

External HTTPS URL used (cookie is Secure+SameSite=Strict so localhost won't work).
"""
import asyncio
import io
import json
import os
import time
import uuid

import pytest
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")

BASE = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://field-evidence-app-1.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"
ORIGIN_OK = BASE  # same host from .env
ORIGIN_BAD = "https://evil.example"
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

ADMIN = ("admin@remedialflow.dev", "AdminDemo!2026")
EMPLOYEE = ("employee@remedialflow.dev", "EmployeeDemo!2026")
INSTALLER = ("installer@remedialflow.dev", "InstallerDemo!2026")
INSTALLER2 = ("installer2@remedialflow.dev", "InstallerDemo!2026")


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if asyncio.get_event_loop().is_running() is False else asyncio.new_event_loop().run_until_complete(coro)


def _mongo():
    return AsyncIOMotorClient(MONGO_URL)[DB_NAME]


def _run_async(fn):
    """Run an async fn in a fresh loop, binding a NEW motor client to that loop (motor is loop-sticky)."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        db = AsyncIOMotorClient(MONGO_URL)[DB_NAME]
        return loop.run_until_complete(fn(db))
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def _login_native(email, password):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password},
                      headers={"X-Client": "native"}, timeout=20)
    r.raise_for_status()
    return r.json()  # has access_token + refresh_token


def _login_web(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password},
               headers={"X-Client": "web", "Origin": ORIGIN_OK}, timeout=20)
    r.raise_for_status()
    return s, r.json()


# ============================== CSRF ==============================
class TestCsrfCookieEndpoints:
    def test_web_login_sets_cookie_and_no_refresh_in_body(self):
        s, j = _login_web(*ADMIN)
        assert "access_token" in j and "refresh_token" not in j, j
        assert s.cookies.get("rf_refresh"), f"rf_refresh cookie missing: {s.cookies.get_dict()}"

    def test_refresh_cookie_without_xclient_blocked_403(self):
        s, _ = _login_web(*ADMIN)
        # No X-Client header
        r = s.post(f"{API}/auth/refresh", json={}, headers={"Origin": ORIGIN_OK}, timeout=15)
        assert r.status_code == 403, f"expected 403 got {r.status_code}: {r.text}"

    def test_refresh_cookie_with_wrong_origin_blocked_403(self):
        s, _ = _login_web(*ADMIN)
        r = s.post(f"{API}/auth/refresh", json={}, headers={"X-Client": "web", "Origin": ORIGIN_BAD}, timeout=15)
        assert r.status_code == 403, f"expected 403 got {r.status_code}: {r.text}"

    def test_refresh_cookie_with_xclient_web_and_own_origin_ok(self):
        s, _ = _login_web(*ADMIN)
        r = s.post(f"{API}/auth/refresh", json={}, headers={"X-Client": "web", "Origin": ORIGIN_OK}, timeout=15)
        assert r.status_code == 200, f"{r.status_code}: {r.text}"
        j = r.json()
        assert "access_token" in j and "refresh_token" not in j
        # rotated cookie present
        assert s.cookies.get("rf_refresh")

    def test_native_refresh_with_body_unaffected(self):
        j = _login_native(*ADMIN)
        rt = j["refresh_token"]
        r = requests.post(f"{API}/auth/refresh", json={"refresh_token": rt},
                          headers={"X-Client": "native"}, timeout=15)
        assert r.status_code == 200, f"{r.status_code}: {r.text}"
        j2 = r.json()
        assert "access_token" in j2 and "refresh_token" in j2

    def test_logout_cookie_without_xclient_blocked_403(self):
        s, _ = _login_web(*ADMIN)
        r = s.post(f"{API}/auth/logout", json={}, headers={"Origin": ORIGIN_OK}, timeout=15)
        assert r.status_code == 403, f"{r.status_code}: {r.text}"

    def test_logout_cookie_with_evil_origin_blocked_403(self):
        s, _ = _login_web(*ADMIN)
        r = s.post(f"{API}/auth/logout", json={}, headers={"X-Client": "web", "Origin": ORIGIN_BAD}, timeout=15)
        assert r.status_code == 403, f"{r.status_code}: {r.text}"

    def test_logout_cookie_valid_ok(self):
        s, _ = _login_web(*ADMIN)
        r = s.post(f"{API}/auth/logout", json={}, headers={"X-Client": "web", "Origin": ORIGIN_OK}, timeout=15)
        assert r.status_code == 200, r.text

    def test_cookie_attributes_backend_emits_strict_httponly_secure(self):
        """Verify the backend ITSELF emits SameSite=strict (checked on the internal port).
        The public preview ingress rewrites it to SameSite=None; Partitioned for CHIPS compat
        -- flagged separately; CSRF defense still holds via X-Client + Origin checks."""
        r = requests.post("http://localhost:8001/api/auth/login",
                          json={"email": ADMIN[0], "password": ADMIN[1]},
                          headers={"X-Client": "web", "Origin": ORIGIN_OK}, timeout=15)
        set_cookie = r.headers.get("set-cookie", "")
        low = set_cookie.lower()
        assert "rf_refresh=" in low
        assert "httponly" in low
        assert "secure" in low
        assert "samesite=strict" in low, f"backend cookie not Strict: {set_cookie}"


# ============================== Push ==============================
class TestPush:
    def test_register_push_without_auth_401(self):
        r = requests.post(f"{API}/register-push",
                          json={"platform": "android", "device_token": "x" * 32}, timeout=15)
        assert r.status_code == 401, f"{r.status_code}: {r.text}"

    def test_register_push_invalid_platform_422(self):
        j = _login_native(*INSTALLER)
        r = requests.post(f"{API}/register-push",
                          headers={"Authorization": f"Bearer {j['access_token']}"},
                          json={"platform": "windows", "device_token": "x" * 32}, timeout=15)
        assert r.status_code == 422, f"{r.status_code}: {r.text}"

    def test_register_push_placeholder_key_returns_5xx_no_crash(self):
        """With placeholder EMERGENT_PUSH_KEY the upstream relay fails: backend should surface
        500 or 502 cleanly without crashing / 5xx without stack trace."""
        j = _login_native(*INSTALLER)
        # Attempt to spoof a different user_id in the body -- it must be ignored
        other = _login_native(*INSTALLER2)
        other_uid = other["user"]["id"]
        body = {"platform": "android", "device_token": "T" + "x" * 50, "user_id": other_uid}
        r = requests.post(f"{API}/register-push",
                          headers={"Authorization": f"Bearer {j['access_token']}"},
                          json=body, timeout=25)
        # placeholder key -> upstream 401 or 5xx -> our handler maps to 500/502
        assert r.status_code in (500, 502), f"{r.status_code}: {r.text}"
        # Verify push_active was NOT turned on for the OTHER user (user_id in body is ignored).
        async def _check(db):
            from bson import ObjectId
            return await db.users.find_one({"_id": ObjectId(other_uid)}, {"push_active": 1})
        other_doc = _run_async(_check)
        assert not other_doc.get("push_active"), f"user_id body-param was NOT ignored: {other_doc}"

    def test_unregister_push_sets_flag_false(self):
        j = _login_native(*INSTALLER)
        uid = j["user"]["id"]
        async def _flip(db):
            from bson import ObjectId
            await db.users.update_one({"_id": ObjectId(uid)}, {"$set": {"push_active": True}})
        _run_async(_flip)
        r = requests.post(f"{API}/unregister-push",
                          headers={"Authorization": f"Bearer {j['access_token']}"}, timeout=15)
        assert r.status_code == 200, r.text
        async def _get(db):
            from bson import ObjectId
            return await db.users.find_one({"_id": ObjectId(uid)}, {"push_active": 1})
        doc = _run_async(_get)
        assert doc.get("push_active") is False, doc

    def test_assign_nc_is_fast_and_creates_in_app_notif_even_though_push_fails(self):
        """notify() creates in-app row + fire-and-forget push. placeholder key => push will
        internally 5xx but notification row must still exist and assign must return quickly."""
        emp = _login_native(*EMPLOYEE)
        inst = _login_native(*INSTALLER)
        iid = inst["user"]["id"]
        # Create a TEST project with 1 NC assigned to installer1
        ref = f"TEST-FCSRF-{int(time.time())}"
        payload = {"ref": ref, "address": "1 CSRF Lane", "postcode": "T1 1ST",
                   "installer_id": iid,
                   "ncs": [{"number": "NC-01", "description": "assign push test",
                            "required_work": "x", "safety_critical": False}]}
        r = requests.post(f"{API}/projects", json=payload,
                          headers={"Authorization": f"Bearer {emp['access_token']}"}, timeout=25)
        assert r.status_code in (200, 201), r.text
        proj = r.json()
        pid = proj["id"]
        # fetch nc id as installer
        pr = requests.get(f"{API}/projects/{pid}",
                          headers={"Authorization": f"Bearer {inst['access_token']}"}, timeout=15)
        nid = (pr.json().get("ncs") or [{}])[0].get("id")
        assert nid
        # reassign to installer2 so new NC-level assignment notification fires
        inst2 = _login_native(*INSTALLER2)
        iid2 = inst2["user"]["id"]
        t0 = time.monotonic()
        ar = requests.post(f"{API}/ncs/{nid}/assign", json={"installer_id": iid2},
                           headers={"Authorization": f"Bearer {emp['access_token']}"}, timeout=20)
        elapsed = time.monotonic() - t0
        assert ar.status_code == 200, ar.text
        assert elapsed < 10, f"assign took too long ({elapsed:.2f}s) - push appears to block"
        # Allow background task a moment, then check installer2 got an in-app 'assigned' notif
        time.sleep(1.5)
        nr = requests.get(f"{API}/notifications",
                          headers={"Authorization": f"Bearer {inst2['access_token']}"}, timeout=15)
        assert nr.status_code == 200, nr.text
        items = nr.json().get("items", [])
        assert any(i.get("kind") == "assigned" and i.get("nc_id") == nid for i in items), items[:3]
        # cleanup project
        requests.delete(f"{API}/projects/{pid}",
                        headers={"Authorization": f"Bearer {emp['access_token']}"}, timeout=15)

    def test_disabled_installer_gets_no_new_notification(self):
        """Create a scratch installer, deactivate, then try to assign NC -> 400 and 0 notifications."""
        from bson import ObjectId
        adm = _login_native(*ADMIN)
        emp = _login_native(*EMPLOYEE)
        inst = _login_native(*INSTALLER)
        iid = inst["user"]["id"]

        email = f"TEST_dis_{uuid.uuid4().hex[:8]}@example.com"
        async def _mk(db):
            r = requests.post(f"{API}/admin/users",
                              headers={"Authorization": f"Bearer {adm['access_token']}"},
                              json={"email": email, "name": "TEST dis", "role": "installer"}, timeout=15)
            assert r.status_code in (200, 201), r.text
            nuid = r.json()["user"]["id"]
            await db.users.update_one({"_id": ObjectId(nuid)}, {"$set": {"active": False}})
            return nuid
        new_uid = _run_async(_mk)

        ref = f"TEST-DIS-{int(time.time())}"
        payload = {"ref": ref, "address": "1 Dis Lane", "postcode": "T2 2ND",
                   "installer_id": iid,
                   "ncs": [{"number": "NC-D1", "description": "dis test", "required_work": "x", "safety_critical": False}]}
        r = requests.post(f"{API}/projects", json=payload,
                          headers={"Authorization": f"Bearer {emp['access_token']}"}, timeout=25)
        assert r.status_code in (200, 201), r.text
        pid = r.json()["id"]
        pr = requests.get(f"{API}/projects/{pid}",
                          headers={"Authorization": f"Bearer {inst['access_token']}"}, timeout=15)
        nid = (pr.json().get("ncs") or [{}])[0].get("id")
        ar = requests.post(f"{API}/ncs/{nid}/assign", json={"installer_id": new_uid},
                           headers={"Authorization": f"Bearer {emp['access_token']}"}, timeout=15)
        assert ar.status_code == 400, f"expected 400 for disabled: {ar.status_code}: {ar.text}"

        async def _check(db):
            n = await db.notifications.count_documents({"user_id": new_uid})
            await db.users.delete_one({"_id": ObjectId(new_uid)})
            return n
        n = _run_async(_check)
        assert n == 0, f"disabled user has {n} notifications (expected 0)"
        requests.delete(f"{API}/projects/{pid}",
                        headers={"Authorization": f"Bearer {emp['access_token']}"}, timeout=15)


# ============================== SEC-001 change password ==============================
class TestSec001ChangePassword:
    def test_change_password_rotates_everything_for_test_user(self):
        from bson import ObjectId
        adm = _login_native(*ADMIN)
        email = f"TEST_cp_{uuid.uuid4().hex[:8]}@example.com"
        pw0 = "InitialPass!2026"
        pw1 = "RotatedPass!2026X"
        # 1. admin creates user and we set a known password via reset code
        r = requests.post(f"{API}/admin/users",
                          headers={"Authorization": f"Bearer {adm['access_token']}"},
                          json={"email": email, "name": "TEST cp", "role": "installer"}, timeout=15)
        assert r.status_code in (200, 201), r.text
        new_uid = r.json()["user"]["id"]
        # admin resets to get a code
        rr = requests.post(f"{API}/admin/users/{new_uid}/reset",
                           headers={"Authorization": f"Bearer {adm['access_token']}"}, timeout=15)
        assert rr.status_code == 200, rr.text
        code = rr.json().get("reset_code") or rr.json().get("setup_code") or rr.json().get("code")
        assert code
        # Use the code to set the password
        setr = requests.post(f"{API}/auth/reset", json={"token": code, "password": pw0}, timeout=15)
        assert setr.status_code == 200, setr.text

        # 2. login native -> get tokens
        a = _login_native(email, pw0)
        access_old = a["access_token"]
        refresh_old = a["refresh_token"]

        # 3. change password
        cp = requests.post(f"{API}/auth/change-password",
                           headers={"Authorization": f"Bearer {access_old}", "X-Client": "native"},
                           json={"current_password": pw0, "new_password": pw1}, timeout=15)
        assert cp.status_code == 200, cp.text
        j = cp.json()
        assert j.get("ok") is True
        assert "access_token" in j, j
        assert "refresh_token" in j, j  # native request => body refresh
        access_new = j["access_token"]
        refresh_new = j["refresh_token"]
        assert access_new != access_old
        assert refresh_new != refresh_old

        # 4. old refresh now 401
        rr1 = requests.post(f"{API}/auth/refresh", json={"refresh_token": refresh_old},
                            headers={"X-Client": "native"}, timeout=15)
        assert rr1.status_code == 401, f"old refresh should be dead: {rr1.status_code}: {rr1.text}"

        # 5. old access now 401 (token_version bumped)
        om = requests.get(f"{API}/auth/me",
                          headers={"Authorization": f"Bearer {access_old}"}, timeout=15)
        assert om.status_code == 401, f"old access should be dead: {om.status_code}"

        # 6. new access works
        nm = requests.get(f"{API}/auth/me",
                          headers={"Authorization": f"Bearer {access_new}"}, timeout=15)
        assert nm.status_code == 200, nm.text
        assert nm.json()["email"] == email.lower()

        # 7. new refresh works
        rr2 = requests.post(f"{API}/auth/refresh", json={"refresh_token": refresh_new},
                            headers={"X-Client": "native"}, timeout=15)
        assert rr2.status_code == 200, rr2.text

        # cleanup user
        async def _cleanup(db):
            await db.users.delete_one({"_id": ObjectId(new_uid)})
            await db.sessions.delete_many({"user_id": new_uid})
            await db.reset_tokens.delete_many({"user_id": new_uid})
        _run_async(_cleanup)


# ============================== discard_evidence NC-scoped ==============================
class TestDiscardNcScoped:
    def _png(self):
        return bytes.fromhex(
            "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489"
            "0000000D49444154789C62000100000500010D0A2DB40000000049454E44AE426082"
        )

    def _upload(self, nc_id, token):
        meta = {"kind": "photo", "source": "in_app_camera", "captured_at": None,
                "tz": "UTC", "tz_offset_min": 0, "lat": 51.5, "lng": -0.12,
                "accuracy_m": 10, "client_id": f"TEST_dsc_{uuid.uuid4().hex[:8]}"}
        files = {"file": ("photo.jpg", io.BytesIO(self._png()), "image/jpeg")}
        data = {"meta": json.dumps(meta)}
        return requests.post(f"{API}/ncs/{nc_id}/evidence",
                             headers={"Authorization": f"Bearer {token}"},
                             files=files, data=data, timeout=30)

    def test_installer2_cannot_discard_installer1_draft(self):
        emp = _login_native(*EMPLOYEE)
        inst = _login_native(*INSTALLER)
        inst2 = _login_native(*INSTALLER2)
        iid = inst["user"]["id"]

        # Project owned by installer1
        ref = f"TEST-DSC-{int(time.time())}"
        payload = {"ref": ref, "address": "1 Discard Rd", "postcode": "T3 3RD",
                   "installer_id": iid,
                   "ncs": [{"number": "NC-01", "description": "dsc", "required_work": "x",
                            "safety_critical": False}]}
        r = requests.post(f"{API}/projects", json=payload,
                          headers={"Authorization": f"Bearer {emp['access_token']}"}, timeout=25)
        assert r.status_code in (200, 201), r.text
        pid = r.json()["id"]
        pr = requests.get(f"{API}/projects/{pid}",
                          headers={"Authorization": f"Bearer {inst['access_token']}"}, timeout=15)
        nid = (pr.json().get("ncs") or [{}])[0].get("id")

        # installer1 uploads a draft
        up = self._upload(nid, inst["access_token"])
        assert up.status_code == 200, up.text
        fid = up.json()["id"]

        # installer2 attempts to delete -> 403 (NC-scoped: they can't even access this NC)
        d = requests.delete(f"{API}/evidence/{fid}",
                            headers={"Authorization": f"Bearer {inst2['access_token']}"}, timeout=15)
        assert d.status_code == 403, f"expected 403, got {d.status_code}: {d.text}"

        # installer1 (owner) can delete -> 200
        d2 = requests.delete(f"{API}/evidence/{fid}",
                             headers={"Authorization": f"Bearer {inst['access_token']}"}, timeout=15)
        assert d2.status_code == 200, d2.text

        # cleanup
        requests.delete(f"{API}/projects/{pid}",
                        headers={"Authorization": f"Bearer {emp['access_token']}"}, timeout=15)


@pytest.fixture(scope="session", autouse=True)
def _cleanup_rate_limits_at_end():
    yield
    async def _cl(db):
        await db.rate_limits.delete_many({})
    try:
        _run_async(_cl)
    except Exception:
        pass
