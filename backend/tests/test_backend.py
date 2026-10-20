"""RemedialFlow comprehensive backend regression tests."""
import io
import os
import json
import time
import uuid
import hashlib
import pytest
import requests
from PIL import Image

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://field-evidence-app-1.preview.emergentagent.com").rstrip("/")
API = BASE_URL + "/api"

ADMIN = ("admin@remedialflow.dev", "AdminDemo!2026")
EMPLOYEE = ("employee@remedialflow.dev", "EmployeeDemo!2026")
INSTALLER = ("installer@remedialflow.dev", "InstallerDemo!2026")
INSTALLER2 = ("installer2@remedialflow.dev", "InstallerDemo!2026")


def login(email, password, web=False):
    h = {"X-Client": "web"} if web else {}
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, headers=h, timeout=15)
    return r


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def realistic_jpeg(seed: int = 1, size=(640, 480)) -> bytes:
    """Create a realistic JPEG with visual features (gradients + shapes)."""
    import random
    random.seed(seed)
    img = Image.new("RGB", size, (120, 140, 160))
    px = img.load()
    for y in range(size[1]):
        for x in range(size[0]):
            r = (x * 255 // size[0] + seed) % 256
            g = (y * 255 // size[1] + seed * 2) % 256
            b = ((x + y) * 255 // (size[0] + size[1]) + seed * 3) % 256
            px[x, y] = (r, g, b)
    # Draw some rectangles for features
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    for i in range(10):
        x0 = random.randint(0, size[0] - 60)
        y0 = random.randint(0, size[1] - 60)
        d.rectangle([x0, y0, x0 + 50, y0 + 50], fill=(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)))
        d.ellipse([x0, y0, x0 + 40, y0 + 40], outline=(0, 0, 0), width=3)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


@pytest.fixture(scope="session")
def installer_token():
    r = login(*INSTALLER)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def installer2_token():
    r = login(*INSTALLER2)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def admin_token():
    r = login(*ADMIN)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def employee_token():
    r = login(*EMPLOYEE)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


# ---------------------- AUTH ----------------------
class TestAuth:
    def test_login_all_four(self):
        for email, pw in [ADMIN, EMPLOYEE, INSTALLER, INSTALLER2]:
            r = login(email, pw)
            assert r.status_code == 200, f"{email}: {r.text}"
            j = r.json()
            assert "access_token" in j and "refresh_token" in j
            assert j["user"]["email"] == email

    def test_login_wrong_password(self):
        r = login(ADMIN[0], "wrong_pwd_123")
        assert r.status_code == 401

    def test_me_endpoint(self, installer_token):
        r = requests.get(f"{API}/auth/me", headers=bearer(installer_token), timeout=10)
        assert r.status_code == 200
        assert r.json()["role"] == "installer"

    def test_me_no_token(self):
        r = requests.get(f"{API}/auth/me", timeout=10)
        assert r.status_code == 401

    def test_refresh_rotation_and_reuse(self):
        r = login(*EMPLOYEE)
        rt = r.json()["refresh_token"]
        r2 = requests.post(f"{API}/auth/refresh", json={"refresh_token": rt}, timeout=10)
        assert r2.status_code == 200, r2.text
        new_rt = r2.json()["refresh_token"]
        assert new_rt != rt
        # reuse of old token -> 401 and family revoked
        r3 = requests.post(f"{API}/auth/refresh", json={"refresh_token": rt}, timeout=10)
        assert r3.status_code == 401
        # new one should now also be revoked (family)
        r4 = requests.post(f"{API}/auth/refresh", json={"refresh_token": new_rt}, timeout=10)
        assert r4.status_code == 401

    def test_logout(self):
        r = login(*EMPLOYEE)
        rt = r.json()["refresh_token"]
        rl = requests.post(f"{API}/auth/logout", json={"refresh_token": rt}, timeout=10)
        assert rl.status_code == 200
        rb = requests.post(f"{API}/auth/refresh", json={"refresh_token": rt}, timeout=10)
        assert rb.status_code == 401

    def test_rate_limit(self):
        fake = f"nobody-{uuid.uuid4().hex[:8]}@example.com"
        last = None
        for _ in range(10):
            last = login(fake, "x" * 10)
        # After 8 failures should return 429
        assert last.status_code == 429, f"Expected 429 got {last.status_code}"

    def test_forgot_demo_mode(self):
        r = requests.post(f"{API}/auth/forgot", json={"email": EMPLOYEE[0]}, timeout=10)
        assert r.status_code == 200
        j = r.json()
        # P0 security fix: the public endpoint must never reveal a reset code
        assert "dev_reset_code" not in j and "code" not in j, j
        assert "message" in j
        # Login still works
        assert login(*EMPLOYEE).status_code == 200


# ---------------------- RBAC ----------------------
class TestRBAC:
    def test_installer_blocked_admin_endpoints(self, installer_token):
        for ep in ["/admin/users", "/dashboard", "/review-queue"]:
            r = requests.get(f"{API}{ep}", headers=bearer(installer_token), timeout=10)
            assert r.status_code == 403, f"{ep}: {r.status_code}"

    def test_installer2_cannot_access_other_project(self, installer2_token, installer_token):
        # find EF-1024 id via installer who owns it
        r = requests.get(f"{API}/projects", params={"search": "EF-1024"}, headers=bearer(installer_token), timeout=10)
        assert r.status_code == 200
        pid = r.json()["items"][0]["id"]
        r2 = requests.get(f"{API}/projects/{pid}", headers=bearer(installer2_token), timeout=10)
        # P2: access is per NC. Installer 2 may hold an individually reassigned NC here; then they must see ONLY theirs.
        me = requests.get(f"{API}/auth/me", headers=bearer(installer2_token), timeout=10).json()
        if r2.status_code == 200:
            ncs = r2.json()["ncs"]
            assert ncs and all(n["assigned_installer_id"] == me["id"] for n in ncs)
            other = next(n for n in requests.get(f"{API}/projects/{pid}", headers=bearer(installer_token), timeout=10).json()["ncs"])
            r3 = requests.post(f"{API}/ncs/{other['id']}/evidence", headers=bearer(installer2_token),
                               files={"file": ("x.jpg", b"x", "image/jpeg")}, data={"meta": "{}"}, timeout=10)
            assert r3.status_code == 403
        else:
            assert r2.status_code == 403

    def test_employee_cannot_write_settings(self, employee_token):
        body = {"default_job_rate": 150, "currency": "GBP", "payment_terms": "weekly",
                "auto_verify": True, "confidence_threshold": 0.8, "location_policy": "accept_flag",
                "gallery_policy": "manual_review"}
        r = requests.put(f"{API}/settings", json=body, headers=bearer(employee_token), timeout=10)
        assert r.status_code == 403


# ---------------------- ADMIN USERS ----------------------
class TestAdminUserFlow:
    def test_create_invite_reset_login_deactivate(self, admin_token):
        email = f"TEST_user_{uuid.uuid4().hex[:6]}@remedialflow.dev"
        body = {"email": email, "name": "TEST Temp", "role": "employee"}
        r = requests.post(f"{API}/admin/users", json=body, headers=bearer(admin_token), timeout=10)
        assert r.status_code == 200, r.text
        j = r.json()
        setup_code = j["setup_code"]
        uid = j["user"]["id"]
        assert setup_code

        # cannot login yet (no password)
        r_bad = login(email, "SomePassword!2026")
        assert r_bad.status_code == 401

        # reset with code
        new_pw = "NewUserPass!2026"
        rr = requests.post(f"{API}/auth/reset", json={"token": setup_code, "password": new_pw}, timeout=10)
        assert rr.status_code == 200

        # login works
        rl = login(email, new_pw)
        assert rl.status_code == 200
        user_token = rl.json()["access_token"]
        user_refresh = rl.json()["refresh_token"]

        # /me works
        rm = requests.get(f"{API}/auth/me", headers=bearer(user_token), timeout=10)
        assert rm.status_code == 200

        # Deactivate -> token immediately invalid
        rd = requests.patch(f"{API}/admin/users/{uid}", json={"active": False}, headers=bearer(admin_token), timeout=10)
        assert rd.status_code == 200
        rm2 = requests.get(f"{API}/auth/me", headers=bearer(user_token), timeout=10)
        assert rm2.status_code == 401
        # refresh fails
        rr2 = requests.post(f"{API}/auth/refresh", json={"refresh_token": user_refresh}, timeout=10)
        assert rr2.status_code == 401


# ---------------------- DASHBOARD / PROJECTS ----------------------
class TestDashboardProjects:
    def test_dashboard_counts(self, employee_token):
        r = requests.get(f"{API}/dashboard", headers=bearer(employee_token), timeout=10)
        assert r.status_code == 200
        j = r.json()
        # P1 counters (replaced the original active/outstanding/completed keys)
        for k in ("unassigned", "in_progress", "awaiting_review", "completed", "trustmark_resolved"):
            assert k in j and isinstance(j[k], int)

    def test_projects_search_and_filter(self, employee_token):
        r = requests.get(f"{API}/projects", params={"search": "EF-1024"}, headers=bearer(employee_token), timeout=10)
        assert r.status_code == 200
        items = r.json()["items"]
        assert any(i["ref"] == "EF-1024" for i in items)

    def test_projects_pagination(self, employee_token):
        r = requests.get(f"{API}/projects", params={"page": 1, "limit": 2}, headers=bearer(employee_token), timeout=10)
        assert r.status_code == 200
        j = r.json()
        assert j["page"] == 1
        assert len(j["items"]) <= 2


# ---------------------- EVIDENCE UPLOAD ----------------------
class TestEvidence:
    @pytest.fixture(scope="class")
    def ef1024(self, installer_token):
        r = requests.get(f"{API}/projects", params={"search": "EF-1024"},
                         headers=bearer(installer_token), timeout=10)
        pid = r.json()["items"][0]["id"]
        pd = requests.get(f"{API}/projects/{pid}", headers=bearer(installer_token), timeout=10).json()
        # pick first non-safety-critical NC that is not approved
        nc = next(n for n in pd["ncs"] if n["status"] not in ("approved", "ai_verified"))
        return pid, nc["id"], nc.get("number")

    def test_upload_photo_with_gps(self, installer_token, ef1024):
        pid, nc_id, _ = ef1024
        img = realistic_jpeg(seed=42)
        client_id = str(uuid.uuid4())
        meta = {"kind": "photo", "source": "in_app_camera", "captured_at": "2026-01-07T12:00:00Z",
                "tz": "Europe/London", "tz_offset_min": 0,
                "lat": 51.8781, "lng": -0.4164, "accuracy_m": 10.0, "client_id": client_id}
        files = {"file": ("photo.jpg", img, "image/jpeg")}
        data = {"meta": json.dumps(meta)}
        r = requests.post(f"{API}/ncs/{nc_id}/evidence", headers=bearer(installer_token),
                          files=files, data=data, timeout=30)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["sha256"] == hashlib.sha256(img).hexdigest()
        assert j["location_verified"] is True
        fid = j["id"]

        # idempotency: resend same client_id -> same file id
        r2 = requests.post(f"{API}/ncs/{nc_id}/evidence", headers=bearer(installer_token),
                           files={"file": ("photo.jpg", img, "image/jpeg")}, data=data, timeout=30)
        assert r2.status_code == 200
        assert r2.json()["id"] == fid

        # Wait for processing then check project detail has processed photo
        for _ in range(20):
            pd = requests.get(f"{API}/projects/{pid}", headers=bearer(installer_token), timeout=10).json()
            found = None
            for n in pd["ncs"]:
                if n["id"] == nc_id:
                    for p in n["photos"]:
                        if p["id"] == fid:
                            found = p
                            break
            if found and found.get("processing") == "ready":
                break
            time.sleep(1.5)
        assert found and found["processing"] == "ready", f"photo not ready: {found}"
        for v in ("original", "stamped", "display", "thumb"):
            assert v in found["urls"], f"missing variant {v}"

        # signed URL returns image, tampered sig 403
        url = BASE_URL + found["urls"]["display"]
        rimg = requests.get(url, timeout=15)
        assert rimg.status_code == 200
        assert rimg.headers.get("content-type", "").startswith("image/")
        tampered = url.replace("sig=", "sig=aa")
        rbad = requests.get(tampered, timeout=15)
        assert rbad.status_code == 403

    def test_upload_photo_without_gps_accept_flag(self, installer_token, ef1024):
        pid, nc_id, _ = ef1024
        img = realistic_jpeg(seed=77)
        meta = {"kind": "photo", "source": "in_app_camera", "captured_at": "2026-01-07T12:05:00Z",
                "tz": "Europe/London", "tz_offset_min": 0, "client_id": str(uuid.uuid4())}
        files = {"file": ("photo.jpg", img, "image/jpeg")}
        r = requests.post(f"{API}/ncs/{nc_id}/evidence", headers=bearer(installer_token),
                          files=files, data={"meta": json.dumps(meta)}, timeout=30)
        assert r.status_code == 200, r.text
        assert r.json()["location_verified"] is False

    def test_upload_gallery_source(self, installer_token, ef1024):
        pid, nc_id, _ = ef1024
        img = realistic_jpeg(seed=99)
        meta = {"kind": "photo", "source": "gallery", "captured_at": "2026-01-07T12:10:00Z",
                "tz": "Europe/London", "tz_offset_min": 0, "lat": 51.9, "lng": -0.4, "accuracy_m": 5.0,
                "client_id": str(uuid.uuid4())}
        files = {"file": ("photo.jpg", img, "image/jpeg")}
        r = requests.post(f"{API}/ncs/{nc_id}/evidence", headers=bearer(installer_token),
                          files=files, data={"meta": json.dumps(meta)}, timeout=30)
        assert r.status_code == 200
        # gallery source -> location_verified false (per spec, only in_app_camera verifies)
        assert r.json()["location_verified"] is False


# ---------------------- LOCATION POLICY REJECT ----------------------
class TestLocationPolicyReject:
    def test_reject_without_gps_then_restore(self, admin_token, installer_token):
        # read settings
        rs = requests.get(f"{API}/settings", headers=bearer(admin_token), timeout=10)
        assert rs.status_code == 200
        settings = rs.json()
        try:
            s2 = dict(settings)
            s2["location_policy"] = "reject"
            r = requests.put(f"{API}/settings", json=s2, headers=bearer(admin_token), timeout=10)
            assert r.status_code == 200
            # find NC on EF-1025
            pr = requests.get(f"{API}/projects", params={"search": "EF-1025"},
                              headers=bearer(installer_token), timeout=10).json()
            pid = pr["items"][0]["id"]
            pd = requests.get(f"{API}/projects/{pid}", headers=bearer(installer_token), timeout=10).json()
            nc = next(n for n in pd["ncs"] if n["status"] not in ("approved", "ai_verified"))
            img = realistic_jpeg(seed=55)
            meta = {"kind": "photo", "source": "in_app_camera", "captured_at": "2026-01-07T12:20:00Z",
                    "tz": "Europe/London", "tz_offset_min": 0, "client_id": str(uuid.uuid4())}
            files = {"file": ("photo.jpg", img, "image/jpeg")}
            rr = requests.post(f"{API}/ncs/{nc['id']}/evidence", headers=bearer(installer_token),
                               files=files, data={"meta": json.dumps(meta)}, timeout=30)
            assert rr.status_code == 422, f"expected 422 got {rr.status_code}: {rr.text}"
        finally:
            # restore
            settings["location_policy"] = "accept_flag"
            requests.put(f"{API}/settings", json=settings, headers=bearer(admin_token), timeout=10)


# ---------------------- VIDEO ----------------------
class TestVideo:
    def _mp4_bytes(self):
        # Use imageio_ffmpeg to make a tiny mp4
        import subprocess
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        out = "/tmp/test_vid.mp4"
        cmd = [ff, "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
               "-pix_fmt", "yuv420p", out]
        subprocess.run(cmd, capture_output=True, check=True)
        with open(out, "rb") as f:
            return f.read()

    def test_video_upload_and_supersede(self, installer_token):
        pr = requests.get(f"{API}/projects", params={"search": "EF-1024"},
                          headers=bearer(installer_token), timeout=10).json()
        pid = pr["items"][0]["id"]
        pd = requests.get(f"{API}/projects/{pid}", headers=bearer(installer_token), timeout=10).json()
        nc = next(n for n in pd["ncs"] if n["status"] not in ("approved", "ai_verified"))
        base = nc["video_versions"]  # earlier runs leave retained versions (never deleted)
        vb = self._mp4_bytes()
        meta = {"kind": "video", "source": "in_app_camera", "lat": 51.87, "lng": -0.41,
                "accuracy_m": 10, "duration_s": 2, "client_id": str(uuid.uuid4())}
        files = {"file": ("v1.mp4", vb, "video/mp4")}
        r = requests.post(f"{API}/ncs/{nc['id']}/evidence", headers=bearer(installer_token),
                          files=files, data={"meta": json.dumps(meta)}, timeout=60)
        assert r.status_code == 200, r.text
        v1_id = r.json()["id"]

        # verify active_video_id
        pd2 = requests.get(f"{API}/projects/{pid}", headers=bearer(installer_token), timeout=10).json()
        nc2 = next(n for n in pd2["ncs"] if n["id"] == nc["id"])
        assert nc2["active_video_id"] == v1_id
        assert nc2["video_versions"] == base + 1

        # upload second video -> supersedes first
        meta2 = {**meta, "client_id": str(uuid.uuid4())}
        r2 = requests.post(f"{API}/ncs/{nc['id']}/evidence", headers=bearer(installer_token),
                           files={"file": ("v2.mp4", vb, "video/mp4")},
                           data={"meta": json.dumps(meta2)}, timeout=60)
        assert r2.status_code == 200, r2.text
        v2_id = r2.json()["id"]
        pd3 = requests.get(f"{API}/projects/{pid}", headers=bearer(installer_token), timeout=10).json()
        nc3 = next(n for n in pd3["ncs"] if n["id"] == nc["id"])
        assert nc3["active_video_id"] == v2_id
        assert nc3["video_versions"] == base + 2


# ---------------------- SUBMIT + AI ----------------------
class TestSubmitAI:
    def test_submit_runs_assessment(self, installer_token):
        pr = requests.get(f"{API}/projects", params={"search": "EF-1025"},
                          headers=bearer(installer_token), timeout=10).json()
        pid = pr["items"][0]["id"]
        pd = requests.get(f"{API}/projects/{pid}", headers=bearer(installer_token), timeout=10).json()
        nc = next((n for n in pd["ncs"] if n["status"] not in ("approved", "ai_verified")), None)
        if not nc:
            pytest.skip("No open NC on EF-1025")
        # Always add a fresh photo as a draft (submit requires draft_submission_id)
        img = realistic_jpeg(seed=123)
        meta = {"kind": "photo", "source": "in_app_camera", "captured_at": "2026-01-07T12:30:00Z",
                "tz": "Europe/London", "tz_offset_min": 0, "lat": 51.88, "lng": -0.42,
                "accuracy_m": 8, "client_id": str(uuid.uuid4())}
        r = requests.post(f"{API}/ncs/{nc['id']}/evidence", headers=bearer(installer_token),
                          files={"file": ("p.jpg", img, "image/jpeg")},
                          data={"meta": json.dumps(meta)}, timeout=30)
        assert r.status_code == 200
        rs = requests.post(f"{API}/ncs/{nc['id']}/submit", headers=bearer(installer_token), timeout=20)
        assert rs.status_code == 200, rs.text
        assert rs.json()["status"] == "assessing"
        # poll
        final = None
        for _ in range(40):
            pd2 = requests.get(f"{API}/projects/{pid}", headers=bearer(installer_token), timeout=10).json()
            nc2 = next(n for n in pd2["ncs"] if n["id"] == nc["id"])
            if nc2["status"] != "assessing":
                final = nc2
                break
            time.sleep(2)
        assert final is not None, "AI assessment did not complete in time"
        assert final["status"] in ("ai_verified", "more_evidence", "manual_review")
        assert final.get("ai") and final["ai"].get("feedback")


# ---------------------- EXPORT ----------------------
class TestExport:
    def test_export_project_zip(self, employee_token, installer_token):
        pr = requests.get(f"{API}/projects", params={"search": "EF-1024"},
                          headers=bearer(installer_token), timeout=10).json()
        pid = pr["items"][0]["id"]
        r = requests.get(f"{API}/export/link", params={"project_id": pid},
                         headers=bearer(employee_token), timeout=10)
        assert r.status_code == 200
        url = r.json()["url"]
        full = BASE_URL + url
        rz = requests.get(full, timeout=60)
        assert rz.status_code == 200
        import zipfile
        z = zipfile.ZipFile(io.BytesIO(rz.content))
        # P8 Evidence Pack: one PDF per NC + Videos/ folder (manifest.json replaced; JSON export is separate)
        names = z.namelist()
        assert names and all(n.endswith(".pdf") or n.startswith("Videos/") for n in names), names
        assert any(n.startswith("NC-01_") and n.endswith(".pdf") for n in names)
        assert rz.headers["content-disposition"].endswith('_Evidence_Pack.zip"')

    def test_export_all_admin_only(self, admin_token, employee_token):
        r_emp = requests.get(f"{API}/export/link", headers=bearer(employee_token), timeout=10)
        assert r_emp.status_code == 403
        r = requests.get(f"{API}/export/link", headers=bearer(admin_token), timeout=10)
        assert r.status_code == 200
        url = BASE_URL + r.json()["url"]
        rd = requests.get(url, timeout=30)
        assert rd.status_code == 200


# ---------------------- EARNINGS flow ----------------------
class TestEarnings:
    def test_approve_all_ncs_creates_earning(self, employee_token, admin_token, installer2_token):
        # Use EF-1026 (installer2, 1 NC) to drive to completion
        pr = requests.get(f"{API}/projects", params={"search": "EF-1026"},
                          headers=bearer(employee_token), timeout=10).json()
        pid = pr["items"][0]["id"]
        pd = requests.get(f"{API}/projects/{pid}", headers=bearer(employee_token), timeout=10).json()
        # Approve all NCs (idempotent-ish on already-approved)
        for n in pd["ncs"]:
            if n["status"] not in ("approved", "ai_verified"):
                rr = requests.post(f"{API}/ncs/{n['id']}/review",
                                   json={"action": "approve", "note": "TEST approve"},
                                   headers=bearer(employee_token), timeout=10)
                assert rr.status_code == 200, rr.text
        # Check project status completed
        pd2 = requests.get(f"{API}/projects/{pid}", headers=bearer(employee_token), timeout=10).json()
        assert pd2["project"]["status"] == "completed"

        # Installer sees earning
        time.sleep(1)
        re_ = requests.get(f"{API}/earnings", headers=bearer(installer2_token), timeout=10)
        assert re_.status_code == 200
        items = re_.json()["items"]
        earning = next((i for i in items if i["project_id"] == pid), None)
        assert earning is not None, "earning not created"
        assert earning["status"] in ("earned", "paid")

        # Admin marks it paid
        if earning["status"] == "earned":
            rp = requests.post(f"{API}/earnings/{earning['id']}/paid",
                               headers=bearer(admin_token), timeout=10)
            assert rp.status_code == 200
