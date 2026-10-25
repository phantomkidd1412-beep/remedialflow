"""Iteration 34 - Phase 1 location-aware scope: run_assessment gate (in-process, stubbed assessor) + flag-OFF regression APIs.
Creates only QA data (qa.iter34.*@example.com, address 'QA TEST 1 Example Road') and removes it afterwards.
Run serially: pytest tests/test_iter34_scope_assessment.py -n 0"""
import asyncio
import io
import os
import sys

import pytest
import requests
from bson import ObjectId

sys.path.insert(0, "/app/backend")

BASE = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/") or os.environ["EXPO_BACKEND_URL"].rstrip("/")
W8 = "End caps missing to 8 no. external window sills (W1-W8)."
SINGLE = "Insulation continuity not maintained around the external wall bracket."
ALLROOMS = "Background ventilation not provided in all habitable rooms."
SAFETY = "Gas meter box bracket fixing compromises the gas supply pipe."

sys.path.insert(0, "/app/backend/tests")
from _loop import LOOP  # noqa: E402


def run(coro):
    return LOOP.run_until_complete(coro)


def jpeg():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (64, 64), (120, 120, 120)).save(b, "JPEG")
    return b.getvalue()


@pytest.fixture(scope="module")
def env():
    import server
    from auth import ph
    from core import db, now_iso
    from storage import storage
    pw = "QaIter34-" + str(ObjectId())[-8:]
    orig = run(db.settings.find_one({"_id": "global"}))
    ids = {}

    async def setup():
        for role in ("admin", "employee", "installer"):
            r = await db.users.insert_one({"email": f"qa.iter34.{role}@example.com", "name": f"QA34 {role}", "role": role, "active": True,
                                           "password_hash": ph.hash(pw), "token_version": 0, "created_at": now_iso()})
            ids[role] = str(r.inserted_id)
        p = await db.projects.insert_one({"ref": "QA-ITER34", "address": "QA TEST 1 Example Road", "postcode": "QA1 1AA", "installer_id": ids["installer"],
                                          "rate": 0, "status": "active", "require_video": False, "deleted": None, "created_at": now_iso(), "updated_at": now_iso()})
        pid = str(p.inserted_id)
        img_path = "qa-mem:no-storage-write"  # never write test evidence to real storage/Dropbox; reads are served in-memory below
        _orig_aget = storage.aget

        async def mem_aget(path, *a, **k):
            return (jpeg(), "image/jpeg") if str(path).startswith("qa-mem:") else await _orig_aget(path, *a, **k)
        storage.aget = mem_aget  # in-process only (run_assessment is called in this process)
        return pid, img_path

    pid, img_path = run(setup())
    yield {"pw": pw, "pid": pid, "ids": ids, "img": img_path, "server": server, "db": db, "now": now_iso}

    async def teardown():
        await db.settings.update_one({"_id": "global"}, {"$set": {"location_scope_enabled": False, "auto_verify": orig.get("auto_verify", True)}})
        await db.ncs.delete_many({"project_id": pid})
        await db.files.delete_many({"project_id": pid})
        await db.submissions.delete_many({"project_id": pid})
        await db.notifications.delete_many({"user_id": {"$in": list(ids.values())}})
        await db.projects.delete_one({"_id": ObjectId(pid)})
        await db.sessions.delete_many({"user_id": {"$in": list(ids.values())}})
        await db.users.delete_many({"email": {"$regex": r"^qa\.iter34\."}})
    run(teardown())


def make_nc(env, text, number):
    db, now = env["db"], env["now"]

    async def go():
        n = await db.ncs.insert_one({"project_id": env["pid"], "number": number, "description": text, "required_work": "", "original_text": text,
                                     "status": "assessing", "safety_critical": False, "assigned_installer_id": env["ids"]["installer"], "updated_at": now()})
        nid = str(n.inserted_id)
        s = await db.submissions.insert_one({"project_id": env["pid"], "nc_id": nid, "installer_id": env["ids"]["installer"], "status": "submitted",
                                             "photo_ids": [], "submitted_at": now(), "created_at": now()})
        sid = str(s.inserted_id)
        f = await db.files.insert_one({"project_id": env["pid"], "nc_id": nid, "submission_id": sid, "kind": "photo", "source": "in_app_camera",
                                       "paths": {"original": env["img"], "display": env["img"]}, "sha256": "qa", "size": 10, "mime": "image/jpeg",
                                       "uploaded_by": env["ids"]["installer"], "status": "submitted", "processing": "ready", "location_verified": True,
                                       "server_received_at": now()})
        await db.submissions.update_one({"_id": s.inserted_id}, {"$set": {"photo_ids": [str(f.inserted_id)]}})
        return nid, sid
    return run(go())


class StubAssessor:
    def __init__(self, outcome="ai_verified", risk="low"):
        self.outcome, self.risk = outcome, risk

    async def assess(self, data):
        from ai import AssessmentResult
        return AssessmentResult(outcome=self.outcome, confidence=0.95, feedback="stub", concerns=[], model="stub", risk=self.risk,
                                method_demonstrated=True, requests=["Upload a closer photo"] if self.outcome == "more_evidence_required" else [])


def assess(env, monkeypatch, text, number, flag, outcome="ai_verified", risk="low"):
    srv, db = env["server"], env["db"]
    monkeypatch.setattr(srv, "get_assessor", lambda: StubAssessor(outcome, risk))

    async def noop(*a, **k):
        return None
    monkeypatch.setattr(srv, "notify_reviewers", noop)
    monkeypatch.setattr(srv, "nc_notify", noop)
    run(db.settings.update_one({"_id": "global"}, {"$set": {"location_scope_enabled": flag, "auto_verify": True}}))
    nid, sid = make_nc(env, text, number)
    run(srv.run_assessment(nid, sid))
    return run(db.ncs.find_one({"_id": ObjectId(nid)}))


# ---- run_assessment gate ----
def test_flag_off_multi_location_still_auto_approves(env, monkeypatch):
    nc = assess(env, monkeypatch, W8, "NC-01", False)
    assert nc["status"] == "approved", nc.get("ai")
    assert nc.get("scope") is None


def test_flag_on_multi_location_routes_to_employee_review(env, monkeypatch):
    nc = assess(env, monkeypatch, W8, "NC-02", True)
    assert nc["status"] == "employee_review"
    assert nc["scope"]["expected_count"] == 8 and nc["scope"]["scope_status"] == "confirmed"
    assert any(c.startswith("Location coverage unverified") for c in nc["ai"]["concerns"])


def test_flag_on_multi_location_blocked_reason_visible(env, monkeypatch):
    """review_request: ai.blocked must contain 'Location coverage unverified' (shown to staff as why auto-approval did not happen)."""
    nc = assess(env, monkeypatch, W8, "NC-03", True)
    assert nc["status"] == "employee_review"
    assert any("Location coverage unverified" in b for b in nc["ai"]["blocked"]), nc["ai"]["blocked"]


def test_flag_on_single_location_unaffected(env, monkeypatch):
    nc = assess(env, monkeypatch, SINGLE, "NC-04", True)
    assert nc["scope"]["expected_count"] == 1 and nc["scope"]["scope_status"] == "confirmed"
    assert nc["status"] == "approved", nc["ai"]


def test_flag_on_uncertain_scope_routes_to_review(env, monkeypatch):
    nc = assess(env, monkeypatch, ALLROOMS, "NC-05", True)  # rules give no count -> one real Gemini scope call
    assert nc["scope"]["scope_status"] in ("uncertain", "confirmed")
    if nc["scope"]["scope_status"] == "uncertain":
        assert nc["status"] == "employee_review"


def test_flag_on_safety_unchanged(env, monkeypatch):
    nc = assess(env, monkeypatch, SAFETY, "NC-06", True)
    assert nc["status"] == "employee_review"
    assert any("Safety-critical" in b for b in nc["ai"]["blocked"])
    assert nc["ai"]["status"] == "ai_verified_signoff"


def test_flag_on_more_evidence_not_overridden(env, monkeypatch):
    nc = assess(env, monkeypatch, W8, "NC-07", True, outcome="more_evidence_required")
    assert nc["status"] == "more_evidence"  # gate never requests evidence / never changes non-approve outcomes


def test_flag_on_potential_noncompliance_not_rejected(env, monkeypatch):
    nc = assess(env, monkeypatch, W8, "NC-08", True, outcome="potential_noncompliance")
    assert nc["status"] == "employee_review"


# ---- API regression (public URL) ----
def login(env, role):
    r = requests.post(f"{BASE}/api/auth/login", json={"email": f"qa.iter34.{role}@example.com", "password": env["pw"]})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_settings_roundtrip_flag_default_false(env):
    run(env["db"].settings.update_one({"_id": "global"}, {"$set": {"location_scope_enabled": False}}))
    a = login(env, "admin")
    s = requests.get(f"{BASE}/api/settings", headers=a).json()
    assert s["location_scope_enabled"] is False
    r = requests.put(f"{BASE}/api/settings", headers=a, json=s)
    assert r.status_code == 200 and r.json()["location_scope_enabled"] is False


def test_project_detail_scope_null_when_off(env):
    e = login(env, "employee")
    r = requests.get(f"{BASE}/api/projects/{env['pid']}", headers=e)
    assert r.status_code == 200
    by = {n["number"]: n for n in r.json()["ncs"]}
    assert by["NC-01"].get("scope") is None
    assert by["NC-02"]["scope"]["expected_count"] == 8


def test_scope_endpoints_roles_and_disabled(env):
    a, e, i = login(env, "admin"), login(env, "employee"), login(env, "installer")
    nid = str(run(env["db"].ncs.find_one({"project_id": env["pid"], "number": "NC-01"}))["_id"])
    assert requests.post(f"{BASE}/api/ncs/{nid}/scope/extract", headers=e).status_code == 409
    assert requests.post(f"{BASE}/api/ncs/{nid}/scope/extract", headers=i).status_code == 403
    assert requests.get(f"{BASE}/api/ncs/{nid}/scope", headers=i).status_code == 403
    run(env["db"].settings.update_one({"_id": "global"}, {"$set": {"location_scope_enabled": True}}))
    try:
        r = requests.post(f"{BASE}/api/ncs/{nid}/scope/extract", headers=a)
        assert r.status_code == 200 and r.json()["scope"]["expected_count"] == 8
        assert requests.post(f"{BASE}/api/ncs/{nid}/scope/extract", headers=i).status_code == 403
    finally:
        run(env["db"].settings.update_one({"_id": "global"}, {"$set": {"location_scope_enabled": False}}))


def test_review_and_exports_when_off(env):
    e = login(env, "employee")
    nid = str(run(env["db"].ncs.find_one({"project_id": env["pid"], "number": "NC-03"}))["_id"])
    r = requests.post(f"{BASE}/api/ncs/{nid}/review", headers=e, json={"action": "approve", "note": "QA"})
    assert r.status_code == 200, r.text
    link = requests.get(f"{BASE}/api/export/link", headers=e, params={"project_id": env["pid"]})
    assert link.status_code == 200
    z = requests.get(BASE + link.json()["url"], timeout=120)
    assert z.status_code == 200 and z.content[:2] == b"PK"
    # Export to Dropbox deliberately NOT called: preview is connected to the real business Dropbox.


def test_guidance_scope_type(env):
    a = login(env, "admin")
    body = {"title": "QA iter34 guidance", "measure": "EWI", "keywords": ["qa-iter34-zz"], "defect": "x"}
    r = requests.post(f"{BASE}/api/admin/guidance", headers=a, json=body)
    assert r.status_code in (200, 201), r.text
    gid = r.json().get("id")
    try:
        g = requests.get(f"{BASE}/api/admin/guidance/{gid}", headers=a).json()
        assert g.get("scope_type", "") == ""
        r2 = requests.put(f"{BASE}/api/admin/guidance/{gid}", headers=a, json={**body, "scope_type": "window"})
        assert r2.status_code == 200, r2.text
        assert requests.get(f"{BASE}/api/admin/guidance/{gid}", headers=a).json().get("scope_type") == "window"
        assert requests.put(f"{BASE}/api/admin/guidance/{gid}", headers=a, json={**body, "scope_type": "garage"}).status_code == 422
        assert requests.post(f"{BASE}/api/admin/guidance", headers=a, json={**body, "scope_type": "bogus"}).status_code == 422
    finally:
        requests.delete(f"{BASE}/api/admin/guidance/{gid}", headers=a)
        run(env["db"].guidance.delete_many({"keywords": "qa-iter34-zz"}))


def test_legacy_guidance_unchanged_save_does_not_bump_version(env):
    """Backward compat: an entry created before Phase 1 has no scope_type; saving it unchanged should not create a new version."""
    a = login(env, "admin")
    db, now = env["db"], env["now"]
    body = {"title": "QA iter34 legacy", "measure": "EWI", "keywords": ["qa-iter34-zz"], "defect": "x"}
    r = requests.post(f"{BASE}/api/admin/guidance", headers=a, json=body)
    gid = r.json()["id"]
    try:
        run(db.guidance.update_one({"_id": ObjectId(gid)}, {"$unset": {"scope_type": ""}}))  # simulate pre-Phase-1 doc
        r2 = requests.put(f"{BASE}/api/admin/guidance/{gid}", headers=a, json=body)
        assert r2.status_code == 200 and r2.json()["version"] == 1, r2.json()
    finally:
        run(db.guidance.delete_many({"keywords": "qa-iter34-zz"}))
