# Phase 11 - Offline evidence sync backend tests.
# Covers: captured_at preservation vs server_received_at, client_id idempotency,
# cross-installer 403, soft-deleted project 404, approved NC 409, draft-only delete,
# audit log for evidence.discarded, and employee permissions.
import io
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests

BASE = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://field-evidence-app-1.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "admin": ("admin@remedialflow.dev", "AdminDemo!2026"),
    "employee": ("employee@remedialflow.dev", "EmployeeDemo!2026"),
    "installer": ("installer@remedialflow.dev", "InstallerDemo!2026"),
    "installer2": ("installer2@remedialflow.dev", "InstallerDemo!2026"),
}


def _login(role):
    r = requests.post(f"{API}/auth/login", json={"email": CREDS[role][0], "password": CREDS[role][1]}, timeout=15)
    r.raise_for_status()
    j = r.json()
    return j["access_token"], j["user"]


@pytest.fixture(scope="module")
def tokens():
    out = {}
    for k in CREDS:
        tok, user = _login(k)
        out[k] = {"token": tok, "user": user, "h": {"Authorization": f"Bearer {tok}"}}
    return out


@pytest.fixture(scope="module")
def test_project(tokens):
    """Create a TEST-* project with installer1 assigned."""
    inst_id = tokens["installer"]["user"]["id"]
    ref = f"TEST-P11-{int(time.time())}"
    payload = {
        "ref": ref,
        "address": "1 Test Lane",
        "postcode": "TE1 1ST",
        "installer_id": inst_id,
        "ncs": [
            {"number": "NC-01", "description": "Phase 11 test NC",
             "required_work": "Submit remedial evidence", "safety_critical": False},
        ],
    }
    r = requests.post(f"{API}/projects", json=payload, headers=tokens["employee"]["h"], timeout=20)
    assert r.status_code in (200, 201), r.text
    proj = r.json()
    yield proj
    # cleanup: soft-delete via employee (fine if already deleted)
    try:
        requests.delete(f"{API}/projects/{proj['id']}", headers=tokens["employee"]["h"], timeout=10)
    except Exception:
        pass


def _nc_id(proj):
    """Fetch the NC id from the project detail."""
    # try multiple shapes
    if proj.get("ncs"):
        n0 = proj["ncs"][0]
        return n0.get("id") or n0.get("_id")
    return None


@pytest.fixture(scope="module")
def nc_id(tokens, test_project):
    pid = test_project["id"]
    r = requests.get(f"{API}/projects/{pid}", headers=tokens["installer"]["h"], timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    ncs = data.get("ncs") or data.get("nc_list") or []
    assert ncs, f"no NCs on project: {data}"
    nid = ncs[0].get("id") or ncs[0].get("_id")
    assert nid
    return nid


def _png_bytes():
    # 1x1 PNG
    return bytes.fromhex(
        "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489"
        "0000000D49444154789C62000100000500010D0A2DB40000000049454E44AE426082"
    )


def _upload(nc_id, headers, meta):
    files = {"file": ("photo.jpg", io.BytesIO(_png_bytes()), "image/jpeg")}
    data = {"meta": json.dumps(meta)}
    return requests.post(f"{API}/ncs/{nc_id}/evidence", headers=headers, files=files, data=data, timeout=30)


# --- (a) captured_at preserved, server_received_at separate ---
class TestCapturedAtPreservation:
    def test_captured_at_stored_as_sent(self, tokens, test_project, nc_id):
        past = (datetime.now(timezone.utc) - timedelta(hours=3)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        cid = f"TEST_cid_{uuid.uuid4().hex[:12]}"
        meta = {
            "kind": "photo", "source": "in_app_camera",
            "captured_at": past, "tz": "Europe/London", "tz_offset_min": 0,
            "lat": 51.5, "lng": -0.12, "accuracy_m": 10, "client_id": cid,
        }
        r = _upload(nc_id, tokens["installer"]["h"], meta)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j.get("captured_at") == past, f"captured_at mismatch: {j.get('captured_at')} vs {past}"
        sr = j.get("server_received_at")
        assert sr and sr != past, f"server_received_at should differ from captured_at (got {sr})"
        # stash for other tests
        TestCapturedAtPreservation.file_id = j["id"]
        TestCapturedAtPreservation.client_id = cid


# --- (b) retry with same client_id returns same id ---
class TestClientIdIdempotency:
    def test_duplicate_client_id_returns_same_file(self, tokens, nc_id):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        cid = f"TEST_dup_{uuid.uuid4().hex[:12]}"
        meta = {
            "kind": "photo", "source": "in_app_camera", "captured_at": past,
            "tz": "Europe/London", "tz_offset_min": 0,
            "lat": 51.5, "lng": -0.12, "accuracy_m": 10, "client_id": cid,
        }
        r1 = _upload(nc_id, tokens["installer"]["h"], meta)
        assert r1.status_code == 200, r1.text
        fid1 = r1.json()["id"]
        r2 = _upload(nc_id, tokens["installer"]["h"], meta)
        assert r2.status_code == 200, r2.text
        fid2 = r2.json()["id"]
        assert fid1 == fid2, f"duplicate upload created new file: {fid1} != {fid2}"


# --- (c) installer2 cannot upload to installer1's NC ---
class TestCrossInstallerForbidden:
    def test_other_installer_gets_403(self, tokens, nc_id):
        meta = {"kind": "photo", "source": "in_app_camera", "captured_at": None,
                "tz": "UTC", "tz_offset_min": 0, "lat": None, "lng": None, "accuracy_m": None,
                "client_id": f"TEST_x_{uuid.uuid4().hex[:8]}"}
        r = _upload(nc_id, tokens["installer2"]["h"], meta)
        assert r.status_code == 403, f"expected 403, got {r.status_code}: {r.text}"


# --- (d) soft-deleted project -> 404 then restore ---
class TestSoftDeletedProject:
    def test_upload_to_deleted_project_404(self, tokens):
        # Build a fresh TEST project just for this test, delete it, upload, then restore for cleanup.
        inst_id = tokens["installer"]["user"]["id"]
        ref = f"TEST-P11DEL-{int(time.time())}"
        payload = {"ref": ref, "address": "9 Trash St", "postcode": "TE2 9ST",
                   "installer_id": inst_id,
                   "ncs": [{"number": "NC-01", "description": "to be deleted",
                            "required_work": "x", "safety_critical": False}]}
        r = requests.post(f"{API}/projects", json=payload, headers=tokens["employee"]["h"], timeout=20)
        assert r.status_code in (200, 201), r.text
        proj = r.json()
        pid = proj["id"]
        pr = requests.get(f"{API}/projects/{pid}", headers=tokens["installer"]["h"], timeout=15)
        nid = (pr.json().get("ncs") or [{}])[0].get("id")
        assert nid
        # soft-delete as employee
        dr = requests.delete(f"{API}/projects/{pid}", headers=tokens["employee"]["h"], timeout=15)
        assert dr.status_code in (200, 204), dr.text
        # upload should 404
        meta = {"kind": "photo", "source": "in_app_camera", "captured_at": None,
                "tz": "UTC", "tz_offset_min": 0, "lat": 51.5, "lng": -0.1, "accuracy_m": 10,
                "client_id": f"TEST_del_{uuid.uuid4().hex[:8]}"}
        ur = _upload(nid, tokens["installer"]["h"], meta)
        assert ur.status_code == 404, f"expected 404, got {ur.status_code}: {ur.text}"
        # restore for cleanup
        rr = requests.post(f"{API}/admin/projects/{pid}/restore", headers=tokens["admin"]["h"], timeout=15)
        assert rr.status_code in (200, 204), rr.text
        # redelete so recycle bin isn't polluted
        requests.delete(f"{API}/projects/{pid}", headers=tokens["employee"]["h"], timeout=10)


# --- (e) approved NC -> 409 ---
class TestApprovedNcConflict:
    def test_approved_nc_rejects_upload(self, tokens):
        # Easiest: create a project, upload, submit, approve as employee, then try to re-upload.
        inst_id = tokens["installer"]["user"]["id"]
        ref = f"TEST-P11APR-{int(time.time())}"
        payload = {"ref": ref, "address": "2 Approved Rd", "postcode": "TE3 3AP",
                   "installer_id": inst_id,
                   "ncs": [{"number": "NC-01", "description": "to approve",
                            "required_work": "x", "safety_critical": False}]}
        r = requests.post(f"{API}/projects", json=payload, headers=tokens["employee"]["h"], timeout=20)
        assert r.status_code in (200, 201), r.text
        pid = r.json()["id"]
        pr = requests.get(f"{API}/projects/{pid}", headers=tokens["installer"]["h"], timeout=15)
        nid = (pr.json().get("ncs") or [{}])[0].get("id")
        assert nid
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        meta = {"kind": "photo", "source": "in_app_camera", "captured_at": past,
                "tz": "UTC", "tz_offset_min": 0, "lat": 51.5, "lng": -0.1, "accuracy_m": 10,
                "client_id": f"TEST_apr_{uuid.uuid4().hex[:8]}"}
        up = _upload(nid, tokens["installer"]["h"], meta)
        assert up.status_code == 200, up.text
        # submit as installer (NC moves to assessing)
        sub = requests.post(f"{API}/ncs/{nid}/submit", headers=tokens["installer"]["h"], timeout=20)
        assert sub.status_code in (200, 202), sub.text
        # Wait for AI assessment to settle (so it doesn't overwrite the approval)
        final_status = None
        for _ in range(60):
            time.sleep(2)
            pr2 = requests.get(f"{API}/projects/{pid}", headers=tokens["employee"]["h"], timeout=15)
            n = (pr2.json().get("ncs") or [{}])[0]
            final_status = n.get("status")
            if final_status and final_status != "assessing":
                break
        assert final_status and final_status != "assessing", f"AI assessment did not settle, status={final_status}"
        # approve as employee
        appr = requests.post(f"{API}/ncs/{nid}/review", headers=tokens["employee"]["h"],
                             json={"action": "approve", "note": "ok"}, timeout=15)
        assert appr.status_code in (200, 202, 204), f"approval failed: {appr.status_code} {appr.text}"
        assert appr.json().get("status") == "approved"
        # now a new upload should be 409
        meta2 = {**meta, "client_id": f"TEST_apr2_{uuid.uuid4().hex[:8]}"}
        r2 = _upload(nid, tokens["installer"]["h"], meta2)
        assert r2.status_code == 409, f"expected 409 on approved NC, got {r2.status_code}: {r2.text}"
        # cleanup
        requests.delete(f"{API}/projects/{pid}", headers=tokens["employee"]["h"], timeout=10)


# --- (f) DELETE evidence rules + audit log ---
class TestEvidenceDelete:
    def test_draft_delete_ok_and_submitted_blocked(self, tokens, nc_id):
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        cid = f"TEST_del_{uuid.uuid4().hex[:8]}"
        meta = {"kind": "photo", "source": "in_app_camera", "captured_at": past,
                "tz": "UTC", "tz_offset_min": 0, "lat": 51.5, "lng": -0.1, "accuracy_m": 10, "client_id": cid}
        up = _upload(nc_id, tokens["installer"]["h"], meta)
        assert up.status_code == 200, up.text
        fid = up.json()["id"]
        # delete draft -> OK
        dr = requests.delete(f"{API}/evidence/{fid}", headers=tokens["installer"]["h"], timeout=15)
        assert dr.status_code == 200, f"draft delete failed: {dr.status_code} {dr.text}"
        # delete again -> 400 (no longer draft)
        dr2 = requests.delete(f"{API}/evidence/{fid}", headers=tokens["installer"]["h"], timeout=15)
        assert dr2.status_code == 400, f"re-delete should 400, got {dr2.status_code}"

    def test_audit_log_contains_discarded(self, tokens, nc_id):
        # Upload + delete a fresh file so audit entry is fresh.
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        meta = {"kind": "photo", "source": "in_app_camera", "captured_at": past,
                "tz": "UTC", "tz_offset_min": 0, "lat": 51.5, "lng": -0.1, "accuracy_m": 10,
                "client_id": f"TEST_adel_{uuid.uuid4().hex[:8]}"}
        up = _upload(nc_id, tokens["installer"]["h"], meta)
        assert up.status_code == 200, up.text
        fid = up.json()["id"]
        dr = requests.delete(f"{API}/evidence/{fid}", headers=tokens["installer"]["h"], timeout=15)
        assert dr.status_code == 200
        # audit log
        ar = requests.get(f"{API}/admin/activity", params={"kind": "deletion", "limit": 200},
                          headers=tokens["admin"]["h"], timeout=15)
        assert ar.status_code == 200, ar.text
        body = ar.json()
        rows = body.get("items") if isinstance(body, dict) else body
        assert rows is not None, f"unexpected activity shape: {body}"
        has = any(("evidence.discarded" in str(row.get("action", "")) or
                   "evidence.discarded" in str(row.get("kind", "")) or
                   fid in json.dumps(row)) for row in rows)
        assert has, f"evidence.discarded audit entry not found for {fid}"


# --- (g) employee / admin permissions unchanged ---
class TestStaffPermissions:
    def test_employee_can_upload_to_any_nc(self, tokens, nc_id):
        meta = {"kind": "photo", "source": "employee_upload", "captured_at": None,
                "tz": "UTC", "tz_offset_min": 0, "lat": None, "lng": None, "accuracy_m": None,
                "client_id": f"TEST_emp_{uuid.uuid4().hex[:8]}"}
        r = _upload(nc_id, tokens["employee"]["h"], meta)
        assert r.status_code == 200, f"employee upload failed: {r.status_code} {r.text}"

    def test_admin_activity_access(self, tokens):
        r = requests.get(f"{API}/admin/activity", params={"limit": 1}, headers=tokens["admin"]["h"], timeout=10)
        assert r.status_code == 200
        r2 = requests.get(f"{API}/admin/activity", params={"limit": 1}, headers=tokens["installer"]["h"], timeout=10)
        assert r2.status_code in (401, 403)
