"""Phase 7 real-report validation using the P419977 TrustMark PDF.

Covers:
- Import extraction (ref/address/postcode/3 NCs, photo_stats 9/5/4)
- Duplicate detection BUG RE-CHECK: neighbouring 283 Dallow Road (P419977Q) must NOT be flagged
- Duplicate-by-reference: create project from the import, re-import, confirm duplicate
- Merge preview + merge-import keeps nc_total stable (3)
- Image hide/restore round-trip on TrustMark files
- Soft-delete + re-import -> duplicate.deleted true + restore-match
- Admin recycle bin restore
- Cleanup: soft-delete + admin purge every project created by this run
"""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://field-evidence-app-1.preview.emergentagent.com").rstrip("/")
API = BASE_URL + "/api"

PDF_PATH = os.environ.get("P419977_PDF", "/tmp/p419977.pdf")

ADMIN = ("admin@remedialflow.dev", "AdminDemo!2026")
EMPLOYEE = ("employee@remedialflow.dev", "EmployeeDemo!2026")


# Shared state across tests in this module
STATE: dict = {"created_pids": [], "test_ref": None}


def _login(email, password):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _bearer(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(scope="module")
def admin_token():
    return _login(*ADMIN)


@pytest.fixture(scope="module")
def employee_token():
    return _login(*EMPLOYEE)


def _poll_ready(token, iid, timeout=180):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        rr = requests.get(f"{API}/imports/{iid}", headers=_bearer(token), timeout=15)
        assert rr.status_code == 200, rr.text
        last = rr.json()
        if last.get("status") in ("ready", "failed"):
            return last
        time.sleep(3)
    pytest.fail(f"import did not become ready in {timeout}s: {last}")


def _upload(token):
    with open(PDF_PATH, "rb") as f:
        data = f.read()
    files = {"file": ("p419977.pdf", data, "application/pdf")}
    r = requests.post(f"{API}/projects/import-pdf", headers=_bearer(token), files=files, timeout=90)
    assert r.status_code == 200, r.text
    return r.json()["id"]


# --------------- A. First import (no P419977 yet) ---------------
class TestFirstImport:
    def test_extraction_basics(self, employee_token):
        iid = _upload(employee_token)
        info = _poll_ready(employee_token, iid)
        STATE["first_iid"] = iid
        STATE["first_info"] = info
        d = info.get("draft") or {}
        assert (d.get("ref") or "").upper() == "P419977", f"ref expected P419977, got {d.get('ref')}"
        addr = (d.get("address") or "").lower()
        assert "280 dallow road" in addr and "luton" in addr, f"unexpected address: {d.get('address')}"
        assert (d.get("postcode") or "").upper().replace(" ", "") == "LU11TD", f"postcode: {d.get('postcode')}"
        ncs = d.get("ncs") or []
        assert len(ncs) == 3, f"expected 3 NCs, got {len(ncs)}: {[n.get('description','')[:60] for n in ncs]}"
        for n in ncs:
            assert n.get("original_text"), f"NC missing original_text: {n}"

    def test_photo_stats_9_5_4(self):
        ps = (STATE["first_info"] or {}).get("photo_stats") or {}
        assert ps.get("found") == 9, f"expected 9 found, got {ps}"
        assert ps.get("kept") == 5, f"expected 5 kept, got {ps}"
        assert ps.get("filtered") == 4, f"expected 4 filtered, got {ps}"
        reasons = ps.get("reasons") or {}
        assert reasons.get("known_logo", 0) >= 1, f"expected known_logo reason, got {reasons}"

    def test_duplicate_is_null_bug_fix(self):
        """BUG RE-CHECK: 281/283 Dallow Road (same postcode) must NOT be flagged as duplicate of 280 Dallow Road."""
        dup = STATE["first_info"].get("duplicate")
        # No active project with ref P419977 should exist at this point; 283 Dallow Road (P419977Q) is a neighbour.
        assert dup is None, f"duplicate must be null before we create a P419977 project; got {dup}"


# --------------- B. Create project from import, confirm dup-by-ref ---------------
class TestDuplicateByReference:
    def test_create_from_import(self, employee_token):
        iid = STATE["first_iid"]
        draft = STATE["first_info"]["draft"]
        # Use real ref P419977 if free, else fall back to a unique suffix (still test ref duplication by re-import).
        chk = requests.get(f"{API}/projects", headers=_bearer(employee_token), timeout=15).json()
        items = chk if isinstance(chk, list) else (chk.get("items") or chk.get("projects") or [])
        existing_refs = {(p.get("ref") or "").upper() for p in items}
        ref = "P419977" if "P419977" not in existing_refs else f"P419977-TEST-{uuid.uuid4().hex[:5].upper()}"
        STATE["test_ref"] = ref
        body = {
            "ref": ref,
            "address": draft.get("address") or "280 Dallow Road, Luton, Bedfordshire",
            "postcode": draft.get("postcode") or "LU1 1TD",
            "import_id": iid,
            "ncs": [{"description": n["description"], "required_work": n.get("required_work", ""),
                     "safety_critical": bool(n.get("safety_critical")),
                     "original_text": n.get("original_text"),
                     "pages": n.get("pages") or []} for n in draft["ncs"]],
        }
        r = requests.post(f"{API}/projects", json=body, headers=_bearer(employee_token), timeout=20)
        assert r.status_code == 200, r.text
        pid = r.json()["id"]
        STATE["created_pids"].append(pid)
        STATE["project_pid"] = pid
        # Confirm persisted
        got = requests.get(f"{API}/projects/{pid}", headers=_bearer(employee_token), timeout=15).json()
        assert len(got["ncs"]) == 3

    def test_reimport_sees_same_reference(self, employee_token):
        iid2 = _upload(employee_token)
        info2 = _poll_ready(employee_token, iid2)
        STATE["second_iid"] = iid2
        STATE["second_info"] = info2
        dup = info2.get("duplicate")
        assert dup, f"expected duplicate dict, got {dup}"
        if STATE["test_ref"] == "P419977":
            assert dup.get("reason") == "Same project reference", dup
            assert dup.get("project_id") == STATE["project_pid"], dup
        else:
            # When ref P419977 was pre-existing on another project the duplicate hits that one;
            # we still assert reason/shape.
            assert dup.get("reason") in ("Same project reference", "Same postcode and similar address"), dup

    def test_merge_preview_and_apply_keeps_nc_total_3(self, employee_token):
        iid2 = STATE["second_iid"]
        pid = STATE["project_pid"]
        pv = requests.get(f"{API}/imports/{iid2}/merge-preview?project_id={pid}",
                          headers=_bearer(employee_token), timeout=20).json()
        assert isinstance(pv.get("items"), list) and len(pv["items"]) == 3, pv
        # All 3 should be matches
        kinds = [it["kind"] for it in pv["items"]]
        assert all(k == "match" for k in kinds), f"expected all matches on self-reimport, got {kinds}"
        actions = [{"index": it["index"], "action": "update", "nc_id": it["nc"]["id"]} for it in pv["items"]]
        r = requests.post(f"{API}/projects/{pid}/merge-import",
                          json={"import_id": iid2, "items": actions},
                          headers=_bearer(employee_token), timeout=30)
        assert r.status_code == 200, r.text
        after = requests.get(f"{API}/projects/{pid}", headers=_bearer(employee_token), timeout=15).json()
        assert len(after["ncs"]) == 3, f"nc_total should stay 3, got {len(after['ncs'])}"


# --------------- C. Image hide/restore round-trip ---------------
class TestImageRestore:
    def test_hide_and_restore_trustmark(self, employee_token):
        pid = STATE["project_pid"]
        # Wait briefly for background _attach_pdf_photos
        trust_fid = None
        for _ in range(10):
            d = requests.get(f"{API}/projects/{pid}", headers=_bearer(employee_token), timeout=15).json()
            for nc in d.get("ncs", []):
                for f in nc.get("trustmark", []) or []:
                    trust_fid = f["id"]; break
                if trust_fid: break
            if trust_fid: break
            time.sleep(2)
        if not trust_fid:
            pytest.skip("no trustmark images attached yet (background task may still be running)")
        # Hide
        r = requests.post(f"{API}/files/{trust_fid}/exclude", json={"excluded": True},
                          headers=_bearer(employee_token), timeout=15)
        assert r.status_code == 200, r.text
        # Verify hidden listing (staff sees trustmark_hidden)
        d = requests.get(f"{API}/projects/{pid}", headers=_bearer(employee_token), timeout=15).json()
        hidden_ids = {f["id"] for nc in d["ncs"] for f in nc.get("trustmark_hidden", []) or []}
        assert trust_fid in hidden_ids, "excluded file not in trustmark_hidden"
        # Restore
        r = requests.post(f"{API}/files/{trust_fid}/exclude", json={"excluded": False},
                          headers=_bearer(employee_token), timeout=15)
        assert r.status_code == 200, r.text
        d = requests.get(f"{API}/projects/{pid}", headers=_bearer(employee_token), timeout=15).json()
        visible_ids = {f["id"] for nc in d["ncs"] for f in nc.get("trustmark", []) or []}
        assert trust_fid in visible_ids, "restored file not back in trustmark"


# --------------- D. Soft-delete + restore-match ---------------
class TestDeletedRestoreMatch:
    def test_soft_delete_then_reimport_shows_deleted(self, employee_token):
        pid = STATE["project_pid"]
        r = requests.delete(f"{API}/projects/{pid}", headers=_bearer(employee_token), timeout=15)
        assert r.status_code == 200, r.text
        iid3 = _upload(employee_token)
        info3 = _poll_ready(employee_token, iid3)
        STATE["third_iid"] = iid3
        dup = info3.get("duplicate")
        if STATE["test_ref"] != "P419977":
            pytest.skip("ref duplication not deterministic in this environment")
        assert dup and dup.get("deleted") is True, f"expected duplicate.deleted true, got {dup}"
        assert dup.get("project_id") == pid, dup

    def test_restore_match_restores_same_project(self, employee_token):
        if STATE["test_ref"] != "P419977":
            pytest.skip("ref duplication not deterministic in this environment")
        pid = STATE["project_pid"]
        iid3 = STATE["third_iid"]
        r = requests.post(f"{API}/projects/restore-match", json={"import_id": iid3},
                          headers=_bearer(employee_token), timeout=20)
        assert r.status_code == 200, r.text
        assert r.json()["id"] == pid
        # NCs still 3 and images intact
        d = requests.get(f"{API}/projects/{pid}", headers=_bearer(employee_token), timeout=15).json()
        assert len(d["ncs"]) == 3
        # Can merge again cleanly
        pv = requests.get(f"{API}/imports/{iid3}/merge-preview?project_id={pid}",
                          headers=_bearer(employee_token), timeout=20).json()
        assert len(pv["items"]) == 3


# --------------- E. Admin recycle bin restore (separate soft-delete) ---------------
class TestAdminRecycleBin:
    def test_admin_restore(self, employee_token, admin_token):
        pid = STATE["project_pid"]
        # Soft-delete again
        requests.delete(f"{API}/projects/{pid}", headers=_bearer(employee_token), timeout=15).raise_for_status()
        # Admin sees it in recycle bin
        dj = requests.get(f"{API}/admin/deleted-projects", headers=_bearer(admin_token), timeout=15).json()
        assert any(p["id"] == pid for p in dj), "project not in recycle bin"
        # Admin restores
        r = requests.post(f"{API}/admin/projects/{pid}/restore", headers=_bearer(admin_token), timeout=15)
        assert r.status_code == 200, r.text
        d = requests.get(f"{API}/projects/{pid}", headers=_bearer(employee_token), timeout=15).json()
        assert len(d["ncs"]) == 3


# --------------- F. Cleanup -- purge everything we created ---------------
class TestCleanup:
    def test_purge_created_projects(self, employee_token, admin_token):
        for pid in list(STATE["created_pids"]):
            # Soft-delete if still active
            got = requests.get(f"{API}/projects/{pid}", headers=_bearer(employee_token), timeout=15)
            if got.status_code == 200:
                requests.delete(f"{API}/projects/{pid}", headers=_bearer(employee_token), timeout=15)
            # Look up ref from recycle bin for exact confirm
            dj = requests.get(f"{API}/admin/deleted-projects", headers=_bearer(admin_token), timeout=15).json()
            match = next((p for p in dj if p["id"] == pid), None)
            if not match:
                continue
            r = requests.post(f"{API}/admin/projects/{pid}/purge",
                              json={"confirm_ref": match["ref"]},
                              headers=_bearer(admin_token), timeout=20)
            assert r.status_code == 200, r.text
