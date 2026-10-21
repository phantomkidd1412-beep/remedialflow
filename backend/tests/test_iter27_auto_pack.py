"""Iteration 27: Automatic Evidence Pack on full-project resolution + PDF Remediation Details heading.

Covers:
  * Partial approval (1 of 2 NCs) -> NO auto_packs row
  * Full approval -> exactly ONE auto_packs row with status='saved', path 'dbx:',
    stored under /remedialflow/{folder}/{Ref}/{Ref}_Evidence_Pack_{stamp}_v{cycle}-{6hex}.zip
    and ZIP contains PDFs for BOTH NCs
  * Concurrency: parallel approve calls still produce one row per cycle
  * Reopen via trustmark_reject + re-approve -> cycle 2 pack, cycle 1 preserved
  * Failure path: subprocess with storage.set_refresh(None) marks a job 'failed';
    retry endpoint with live Dropbox restores 'saved'
  * RBAC: installer -> 403 on list and retry endpoints
  * PDF: REMEDIATION DETAILS - WORK CARRIED OUT heading present (with note / 'No details recorded.');
    no 'REMEDIATION NOTE' heading
  * Regression: manual export unchanged (200 + dropbox_exports grows)
"""
import asyncio
import io
import json
import os
import re
import sys
import time
import uuid
import zipfile
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
from bson import ObjectId
from pymongo import MongoClient

sys.path.insert(0, "/app/backend")

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")
_mongo = MongoClient(MONGO_URL)[DB_NAME]

BASE_URL = (os.environ.get("EXPO_PUBLIC_BACKEND_URL")
            or os.environ.get("EXPO_BACKEND_URL")
            or "https://field-evidence-app-1.preview.emergentagent.com").rstrip("/")
API = BASE_URL + "/api"

ADMIN = {"email": "admin@remedialflow.dev", "password": "AdminDemo!2026"}
EMPLOYEE = {"email": "employee@remedialflow.dev", "password": "EmployeeDemo!2026"}
INSTALLER = {"email": "installer@remedialflow.dev", "password": "InstallerDemo!2026"}
INSTALLER2 = {"email": "installer2@remedialflow.dev", "password": "InstallerDemo!2026"}

INSTALLER_IAN_ID = "6ad6335a4535baee71105ba1"
INSTALLER_SAM_ID = "6ad6335a4535baee71105ba2"


def _login(creds):
    r = requests.post(f"{API}/auth/login", json=creds, timeout=30)
    assert r.status_code == 200, f"login failed for {creds['email']}: {r.status_code} {r.text[:200]}"
    tok = r.json()["access_token"]
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def employee():
    return _login(EMPLOYEE)


@pytest.fixture(scope="module")
def installer():
    return _login(INSTALLER)


# -------- shared state --------
STATE: dict = {}


# -------- Dropbox resolver (read-only) --------
_dbx_cache = {"client": None}


async def _dbx_client():
    if _dbx_cache["client"] is not None:
        return _dbx_cache["client"]
    os.environ.setdefault("MONGO_URL", MONGO_URL)
    os.environ.setdefault("DB_NAME", DB_NAME)
    from storage import storage  # type: ignore
    from dropbox_link import load_dropbox  # type: ignore
    await load_dropbox()
    assert storage.dropbox_ready, "Dropbox not connected"
    _dbx_cache["client"] = storage.client()
    return _dbx_cache["client"]


def dbx_get_path(storage_id: str) -> str:
    sid = storage_id[4:] if storage_id.startswith("dbx:") else storage_id

    async def _run():
        d = await _dbx_client()
        md = d.files_get_metadata(sid)
        return md.path_display
    return asyncio.new_event_loop().run_until_complete(_run()) if not asyncio.get_event_loop().is_running() else asyncio.get_event_loop().run_until_complete(_run())


def dbx_download(storage_id: str) -> bytes:
    sid = storage_id[4:] if storage_id.startswith("dbx:") else storage_id

    async def _run():
        d = await _dbx_client()
        md, resp = d.files_download(sid)
        try:
            return resp.content
        finally:
            resp.close()
    return asyncio.get_event_loop().run_until_complete(_run())


def dbx_file_exists(path: str) -> bool:
    async def _run():
        d = await _dbx_client()
        try:
            d.files_get_metadata(path)
            return True
        except Exception:
            return False
    return asyncio.get_event_loop().run_until_complete(_run())


# -------- project builder --------
def _create_two_nc_project(employee_sess, ian_installer=True) -> dict:
    """Create a project with 2 NCs; NC1 -> Ian (default installer), NC2 -> reassign to Sam."""
    ref = f"TEST-IT27-{uuid.uuid4().hex[:6].upper()}"
    addr = f"Flat 27 Autopack Rise {uuid.uuid4().hex[:4].upper()}"
    postcode = "AP27 1AA"
    body = {"ref": ref, "address": addr, "postcode": postcode,
            "installer_id": INSTALLER_IAN_ID,
            "ncs": [{"description": "Replace faulty cable", "required_work": "Replace to BS7671",
                     "safety_critical": False, "original_text": "Replace cable per BS7671."},
                    {"description": "Reseal junction box", "required_work": "Reseal and verify ingress",
                     "safety_critical": False, "original_text": "Reseal junction box."}]}
    r = employee_sess.post(f"{API}/projects", json=body, timeout=30)
    assert r.status_code == 200, f"create project: {r.status_code} {r.text[:200]}"
    pid = r.json()["id"]
    pr = employee_sess.get(f"{API}/projects/{pid}", timeout=30)
    ncs = pr.json()["ncs"]
    nc1_id, nc2_id = ncs[0]["id"], ncs[1]["id"]
    # Reassign NC2 to Sam
    r = employee_sess.post(f"{API}/ncs/{nc2_id}/assign", json={"installer_id": INSTALLER_SAM_ID}, timeout=30)
    assert r.status_code == 200, f"assign nc2 to sam: {r.status_code} {r.text[:200]}"
    return {"pid": pid, "ref": ref, "nc1": nc1_id, "nc2": nc2_id, "addr": addr, "postcode": postcode}


# ----------------- tests -----------------
class TestA_PartialThenFullApproval:
    def test_partial_approve_no_pack(self, employee):
        proj = _create_two_nc_project(employee)
        STATE["proj"] = proj
        # Approve only NC1
        r = employee.post(f"{API}/ncs/{proj['nc1']}/review", json={"action": "approve"}, timeout=30)
        assert r.status_code == 200, f"approve nc1: {r.status_code} {r.text[:200]}"
        # Wait briefly; no auto_packs should exist
        time.sleep(2)
        rows = list(_mongo.auto_packs.find({"project_id": proj["pid"]}))
        assert rows == [], f"unexpected auto_packs on partial approve: {rows}"

    def test_full_approve_creates_one_saved_pack(self, employee):
        proj = STATE["proj"]
        # Set an export_note on nc1 so PDF has non-empty remediation text
        r = employee.put(f"{API}/ncs/{proj['nc1']}/export-note",
                         json={"note": "Replaced cable with 2.5mm T&E, tested RCD trip 28ms."}, timeout=30)
        assert r.status_code == 200
        # Leave nc2 export_note empty -> PDF should say "No details recorded."
        # Approve NC2 -> project completes -> queue_auto_pack(cycle=1)
        r = employee.post(f"{API}/ncs/{proj['nc2']}/review", json={"action": "approve"}, timeout=30)
        assert r.status_code == 200, f"approve nc2: {r.status_code} {r.text[:200]}"
        # Wait for auto_pack to finish (Dropbox upload)
        row = None
        for _ in range(60):
            row = _mongo.auto_packs.find_one({"_id": f"{proj['pid']}:1"})
            if row and row.get("status") == "saved":
                break
            time.sleep(2)
        assert row is not None, "no auto_packs row created"
        assert row["status"] == "saved", f"auto_pack status={row.get('status')} err={row.get('error')}"
        assert row["cycle"] == 1
        assert row["path"].startswith("dbx:"), row["path"]
        assert row.get("files", 0) >= 2, f"files count: {row.get('files')}"
        STATE["cycle1_path"] = row["path"]
        STATE["cycle1_row"] = row
        # exactly ONE row for this project
        all_rows = list(_mongo.auto_packs.find({"project_id": proj["pid"]}))
        assert len(all_rows) == 1, f"expected 1 pack, got {len(all_rows)}: {[r['_id'] for r in all_rows]}"

    def test_dropbox_path_and_zip_contents(self):
        proj = STATE["proj"]
        p_abs = dbx_get_path(STATE["cycle1_path"])
        STATE["cycle1_abs"] = p_abs
        # /remedialflow/{folder}/{Ref}/{Ref}_Evidence_Pack_{stamp}_v1-{6hex}.zip
        assert p_abs.lower().startswith("/remedialflow/"), p_abs
        assert f"/{proj['ref']}/" in p_abs, f"ref folder missing: {p_abs}"
        fname = p_abs.rsplit("/", 1)[-1]
        assert fname.startswith(f"{proj['ref']}_Evidence_Pack_"), fname
        assert p_abs.lower().endswith(".zip")
        assert re.search(r"_v1-[0-9a-f]{6}\.zip$", p_abs), f"missing v1-6hex suffix: {p_abs}"
        # Download the pack and inspect zip contents - must contain PDFs for BOTH NCs
        data = dbx_download(STATE["cycle1_path"])
        assert len(data) > 2000, f"zip too small: {len(data)}"
        z = zipfile.ZipFile(io.BytesIO(data))
        names = z.namelist()
        pdfs = [n for n in names if n.lower().endswith(".pdf")]
        assert len(pdfs) >= 2, f"expected >=2 NC PDFs, got {len(pdfs)}: {names}"
        STATE["cycle1_zip_bytes"] = data
        STATE["cycle1_pdf_names"] = pdfs


class TestB_PDFRemediationHeading:
    def test_pdf_contains_remediation_details_heading(self):
        """NC with export_note shows note under 'REMEDIATION DETAILS - WORK CARRIED OUT'
        before 'REMEDIATION PHOTOGRAPHS'; NC without note shows 'No details recorded.';
        no 'REMEDIATION NOTE' heading anywhere."""
        import pymupdf
        data = STATE["cycle1_zip_bytes"]
        z = zipfile.ZipFile(io.BytesIO(data))
        pdfs = STATE["cycle1_pdf_names"]
        found_with_note = False
        found_without_note = False
        for name in pdfs:
            pdf_bytes = z.read(name)
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
            text = "".join(page.get_text() for page in doc)
            doc.close()
            # Required heading
            assert "REMEDIATION DETAILS - WORK CARRIED OUT" in text, \
                f"missing new heading in {name}:\n{text[:500]}"
            # Must NOT contain legacy heading
            assert "REMEDIATION NOTE" not in text, f"legacy 'REMEDIATION NOTE' present in {name}"
            # Must contain 'REMEDIATION PHOTOGRAPHS' (either photos heading or placeholder)
            # Heading order: details before photographs
            idx_details = text.find("REMEDIATION DETAILS - WORK CARRIED OUT")
            idx_photos = text.find("REMEDIATION PHOTOGRAPHS")
            assert idx_photos > idx_details > 0, \
                f"heading order wrong in {name}: details@{idx_details}, photos@{idx_photos}"
            if "Replaced cable with 2.5mm T&E" in text:
                found_with_note = True
            if "No details recorded." in text:
                found_without_note = True
        assert found_with_note, "NC1 export_note text not rendered in any PDF"
        assert found_without_note, "'No details recorded.' not present for empty-note NC"


class TestC_ConcurrencyOneRecordPerCycle:
    """Fire parallel review-approve + direct recompute triggers; still one record per cycle."""
    def test_parallel_recomputes_single_row(self, employee):
        proj = _create_two_nc_project(employee)
        STATE["proj_c"] = proj
        # First NC approve to half-complete
        r = employee.post(f"{API}/ncs/{proj['nc1']}/review", json={"action": "approve"}, timeout=30)
        assert r.status_code == 200
        # Now fire the SECOND approve several times in parallel (idempotent approve;
        # each successful call triggers recompute_project -> queue_auto_pack).
        def _approve():
            try:
                return employee.post(f"{API}/ncs/{proj['nc2']}/review",
                                     json={"action": "approve"}, timeout=30).status_code
            except Exception as e:
                return str(e)
        with ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(lambda _: _approve(), range(6)))
        assert all(r == 200 for r in results), f"approve results: {results}"
        # Wait for completion
        for _ in range(60):
            row = _mongo.auto_packs.find_one({"_id": f"{proj['pid']}:1"})
            if row and row.get("status") == "saved":
                break
            time.sleep(2)
        rows = list(_mongo.auto_packs.find({"project_id": proj["pid"]}))
        assert len(rows) == 1, f"expected 1 pack after concurrent approves, got {len(rows)}: {[r['_id'] for r in rows]}"
        assert rows[0]["status"] == "saved", f"status={rows[0].get('status')} err={rows[0].get('error')}"
        assert rows[0]["cycle"] == 1


class TestD_ReopenAndReapprove:
    def test_trustmark_reject_then_reapprove_creates_cycle2(self, employee):
        proj = STATE["proj"]  # already fully resolved cycle 1
        # TrustMark reject NC1 -> project becomes in_progress, resolved cleared
        r = employee.post(f"{API}/ncs/{proj['nc1']}/review",
                          json={"action": "trustmark_reject", "note": "iter27 reopen"}, timeout=30)
        assert r.status_code == 200, f"trustmark_reject: {r.status_code} {r.text[:300]}"
        # Approve NC1 again -> project re-completes -> cycle 2 pack queued
        r = employee.post(f"{API}/ncs/{proj['nc1']}/review", json={"action": "approve"}, timeout=30)
        assert r.status_code == 200
        # Wait for cycle 2 to finish
        row2 = None
        for _ in range(60):
            row2 = _mongo.auto_packs.find_one({"_id": f"{proj['pid']}:2"})
            if row2 and row2.get("status") == "saved":
                break
            time.sleep(2)
        assert row2 is not None, "cycle 2 auto_pack not created"
        assert row2["status"] == "saved", f"cycle2 status={row2.get('status')} err={row2.get('error')}"
        assert row2["cycle"] == 2
        assert row2["path"].startswith("dbx:")
        # Distinct file from cycle 1
        p2_abs = dbx_get_path(row2["path"])
        assert p2_abs != STATE["cycle1_abs"], f"cycle 2 reused cycle 1 path: {p2_abs}"
        assert re.search(r"_v2-[0-9a-f]{6}\.zip$", p2_abs), f"missing v2 suffix: {p2_abs}"
        # Cycle 1 file STILL exists in Dropbox (append-only)
        assert dbx_file_exists(STATE["cycle1_abs"]), f"cycle1 file disappeared: {STATE['cycle1_abs']}"
        # Exactly 2 rows for the project
        rows = list(_mongo.auto_packs.find({"project_id": proj["pid"]}))
        assert len(rows) == 2, f"expected 2 packs after reopen+reapprove, got {len(rows)}"


class TestE_FailurePathAndRetry:
    """Non-destructive failure sim: create a FAKE auto_packs row for a resolved project and run
    run_auto_pack in a subprocess that has set_refresh(None) -> status becomes 'failed'.
    Then call POST /retry (live backend has Dropbox) -> status 'saved'."""
    def test_failure_then_retry(self, employee):
        proj = _create_two_nc_project(employee)
        STATE["proj_e"] = proj
        # Fully resolve
        for nc_key in ("nc1", "nc2"):
            r = employee.post(f"{API}/ncs/{proj[nc_key]}/review", json={"action": "approve"}, timeout=30)
            assert r.status_code == 200
        # Let the live backend create (and succeed on) cycle 1
        for _ in range(60):
            row = _mongo.auto_packs.find_one({"_id": f"{proj['pid']}:1"})
            if row and row.get("status") == "saved":
                break
            time.sleep(2)
        assert row and row["status"] == "saved", f"cycle1 did not save: {row}"
        # Now insert a synthetic 'pending' row cycle=99 that the live backend won't auto-run,
        # then subprocess run_auto_pack with no Dropbox -> fails.
        synth_key = f"{proj['pid']}:99"
        _mongo.auto_packs.delete_one({"_id": synth_key})
        _mongo.auto_packs.insert_one({"_id": synth_key, "project_id": proj["pid"], "cycle": 99,
                                      "status": "pending", "attempts": 0, "created_at": "2026-01-15T00:00:00Z"})
        # Subprocess script: import server, kill dropbox refresh, run_auto_pack
        script = f"""
import asyncio, os, sys
sys.path.insert(0, "/app/backend")
os.environ["MONGO_URL"] = {MONGO_URL!r}
os.environ["DB_NAME"] = {DB_NAME!r}
async def main():
    from storage import storage
    import server
    storage.set_refresh(None)  # local module state only; does NOT touch Mongo
    assert not storage.dropbox_ready
    await server.run_auto_pack({synth_key!r})
asyncio.run(main())
"""
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, f"subprocess failed: {proc.stderr[:800]}"
        row = _mongo.auto_packs.find_one({"_id": synth_key})
        assert row["status"] == "failed", f"expected failed, got {row}"
        assert row.get("error"), "no error recorded on failure"
        assert row.get("path") != "saved"
        assert not row.get("path", "").startswith("dbx:"), f"path should not be set on failure: {row.get('path')}"
        # audit entry
        aud = _mongo.audit.find_one({"event": "export.auto_pack_failed", "target": f"project:{proj['pid']}"},
                                    sort=[("seq", -1)])
        assert aud is not None, "no auto_pack_failed audit entry"
        # Now retry via live endpoint (Dropbox IS available on the backend)
        r = employee.post(f"{API}/projects/{proj['pid']}/evidence-packs/retry", json={}, timeout=120)
        assert r.status_code == 200, f"retry: {r.status_code} {r.text[:300]}"
        # Give it time to upload
        for _ in range(60):
            row = _mongo.auto_packs.find_one({"_id": synth_key})
            if row and row.get("status") == "saved":
                break
            time.sleep(2)
        assert row["status"] == "saved", f"retry did not restore saved: {row}"
        assert row["path"].startswith("dbx:")
        STATE["synth_saved_path"] = row["path"]


class TestF_RBAC_StaffOnly:
    def test_installer_cannot_list_packs(self, installer):
        proj = STATE["proj"]
        r = installer.get(f"{API}/projects/{proj['pid']}/evidence-packs", timeout=30)
        assert r.status_code == 403, f"installer must be 403 on list, got {r.status_code}"

    def test_installer_cannot_retry(self, installer):
        proj = STATE["proj"]
        r = installer.post(f"{API}/projects/{proj['pid']}/evidence-packs/retry", json={}, timeout=30)
        assert r.status_code == 403, f"installer must be 403 on retry, got {r.status_code}"

    def test_staff_can_list_packs(self, employee):
        proj = STATE["proj"]
        r = employee.get(f"{API}/projects/{proj['pid']}/evidence-packs", timeout=30)
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list)
        assert len(items) >= 2, f"expected >=2 packs listed, got {len(items)}"
        cycles = sorted([i["cycle"] for i in items])
        assert cycles == [1, 2], f"cycles: {cycles}"
        for it in items:
            assert "_id" not in it, "_id leaked"
            assert it["status"] == "saved"


class TestG_ManualExportRegression:
    def test_manual_export_still_works(self, employee):
        proj = STATE["proj"]
        # count existing manual copies first
        before = _mongo.dropbox_exports.count_documents({"project_id": proj["pid"]})
        r = employee.get(f"{API}/export/link?project_id={proj['pid']}", timeout=30)
        assert r.status_code == 200
        url = r.json()["url"]
        full = url if url.startswith("http") else f"{BASE_URL}{url}"
        d = requests.get(full, timeout=300)
        assert d.status_code == 200
        assert len(d.content) > 2000
        z = zipfile.ZipFile(io.BytesIO(d.content))
        names = z.namelist()
        assert any(n.lower().endswith(".pdf") for n in names), f"no pdf in manual export: {names[:10]}"
        # dropbox_exports grew
        after = 0
        for _ in range(30):
            after = _mongo.dropbox_exports.count_documents({"project_id": proj["pid"]})
            if after > before:
                break
            time.sleep(2)
        assert after > before, f"manual export did not add a dropbox_exports row (before={before}, after={after})"
