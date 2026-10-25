"""Iteration 35 - Phase 2 API gating/permissions/staff-override persistence + flags-OFF regression via the PUBLIC URL.
NO Dropbox/storage writes: photos use fake paths, classify_nc runs in-process with an injected loader + fake AI,
export-dropbox is never called, and one NC is left open so the project never completes (no auto-pack upload).
QA data only (qa.iter35.*@example.com, 'QA TEST 1 Example Road'), removed afterwards. Run: pytest tests/test_iter35_phase2_api_regression.py -n 0"""
import io
import os
import sys
import time

import pytest
import requests
from bson import ObjectId
from PIL import Image

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")
from _loop import LOOP  # noqa: E402

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ["EXPO_BACKEND_URL"]).rstrip("/")
W8 = "End caps missing to 8 no. external window sills (W1-W8)."


def run(c):
    return LOOP.run_until_complete(c)


def jpg(seed, shift=0):
    im = Image.new("RGB", (320, 240), (180, 180, 180))
    from PIL import ImageDraw
    d = ImageDraw.Draw(im)
    for k in range(5):
        x = (seed * 41 + k * 57) % 260
        d.rectangle([x + shift, 20 + k * 40, x + 50 + shift, 45 + k * 40], fill=((seed * 60) % 255, 90, 140))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=90)
    return b.getvalue()


IM = {0: jpg(1), 1: jpg(1, 1), 2: jpg(4)}


async def loader(f):
    return IM[int(f["paths"]["display"].split(":")[1])]


@pytest.fixture(scope="module")
def env():
    from auth import ph
    from core import db, now_iso
    pw = "QaI35-" + str(ObjectId())[-8:]
    orig = run(db.settings.find_one({"_id": "global"})) or {}

    async def setup():
        await db.users.delete_many({"email": {"$regex": r"^qa\.iter35\."}})
        u = {r: str((await db.users.insert_one({"email": f"qa.iter35.{r}@example.com", "name": f"QA35 {r}", "role": r, "active": True,
                                                 "password_hash": ph.hash(pw), "token_version": 0, "created_at": now_iso()})).inserted_id)
             for r in ("admin", "employee", "installer")}
        pid = str((await db.projects.insert_one({"ref": "QA-ITER35", "address": "QA TEST 1 Example Road", "postcode": "QA1 1AA", "installer_id": u["installer"],
                                                 "rate": 0, "status": "active", "require_video": False, "deleted": None,
                                                 "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)
        nc = {}
        for num, st in (("NC-01", "open"), ("NC-02", "open"), ("NC-03", "employee_review"), ("NC-04", "open")):
            nc[num] = str((await db.ncs.insert_one({"project_id": pid, "number": num, "description": W8, "original_text": W8, "required_work": "",
                                                    "status": st, "safety_critical": False, "assigned_installer_id": u["installer"],
                                                    "updated_at": now_iso()})).inserted_id)
        files = []
        for k in range(3):  # NC-01 photos: fake paths, never read from storage (loader injected)
            files.append(str((await db.files.insert_one({"project_id": pid, "nc_id": nc["NC-01"], "kind": "photo", "status": "submitted", "pdf_include": None,
                                                         "source": "in_app_camera", "sha256": f"qa35-{k}", "size": 1, "mime": "image/jpeg",
                                                         "uploaded_by": u["installer"], "paths": {"display": f"qa-mem:{k}"},
                                                         "server_received_at": f"2026-01-01T00:0{k}:00"})).inserted_id))
        # NC-04 draft submission for submit regression (flag OFF -> no classification must appear)
        sid = str((await db.submissions.insert_one({"project_id": pid, "nc_id": nc["NC-04"], "installer_id": u["installer"], "status": "draft",
                                                    "photo_ids": [], "created_at": now_iso()})).inserted_id)
        dfid = str((await db.files.insert_one({"project_id": pid, "nc_id": nc["NC-04"], "submission_id": sid, "kind": "photo", "status": "draft",
                                               "source": "in_app_camera", "sha256": "qa35-d", "size": 1, "mime": "image/jpeg", "uploaded_by": u["installer"],
                                               "paths": {"display": "qa-mem:2"}, "processing": "ready", "server_received_at": now_iso()})).inserted_id)
        await db.ncs.update_one({"_id": ObjectId(nc["NC-04"])}, {"$set": {"draft_submission_id": sid}})
        return u, pid, nc, files, dfid
    u, pid, nc, files, dfid = run(setup())
    yield {"pw": pw, "u": u, "pid": pid, "nc": nc, "fids": files, "dfid": dfid, "db": db}

    async def teardown():
        await db.settings.update_one({"_id": "global"}, {"$set": {"photo_classification_enabled": False, "location_scope_enabled": False,
                                                                 "auto_verify": orig.get("auto_verify", True)}})
        for c in ("files", "ncs", "submissions", "auto_packs", "dropbox_exports"):
            await db[c].delete_many({"project_id": pid})
        await db.notifications.delete_many({"user_id": {"$in": list(u.values())}})
        await db.projects.delete_one({"_id": ObjectId(pid)})
        await db.sessions.delete_many({"user_id": {"$in": list(u.values())}})
        await db.users.delete_many({"email": {"$regex": r"^qa\.iter35\."}})
    run(teardown())


_tok = {}


def H(env, role):
    if role not in _tok:
        r = requests.post(f"{BASE}/api/auth/login", json={"email": f"qa.iter35.{role}@example.com", "password": env["pw"]})
        assert r.status_code == 200, r.text
        _tok[role] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    return _tok[role]


# --- settings round-trip: both flags stay false
def test_settings_roundtrip_flags_false(env):
    a = H(env, "admin")
    s = requests.get(f"{BASE}/api/settings", headers=a).json()
    assert s["photo_classification_enabled"] is False and s["location_scope_enabled"] is False and s["auto_verify"] is True
    r = requests.put(f"{BASE}/api/settings", headers=a, json=s)
    assert r.status_code == 200, r.text
    s2 = requests.get(f"{BASE}/api/settings", headers=a).json()
    assert s2["photo_classification_enabled"] is False and s2["location_scope_enabled"] is False and s2["auto_verify"] is True


# --- GET classification: staff 200, installer 403; POST classify 409 when disabled
def test_get_and_classify_gating(env):
    n = env["nc"]["NC-01"]
    for role in ("admin", "employee"):
        g = requests.get(f"{BASE}/api/ncs/{n}/photos/classification", headers=H(env, role))
        assert g.status_code == 200 and g.json()["enabled"] is False and len(g.json()["photos"]) == 3
        assert requests.post(f"{BASE}/api/ncs/{n}/photos/classify", headers=H(env, role)).status_code == 409
    assert requests.get(f"{BASE}/api/ncs/{n}/photos/classification", headers=H(env, "installer")).status_code == 403
    assert requests.post(f"{BASE}/api/ncs/{n}/photos/classify", headers=H(env, "installer")).status_code == 403
    assert requests.get(f"{BASE}/api/ncs/{n}/photos/classification").status_code == 401


# --- in-process classify (fake AI) then staff PUT, then re-classify: staff wins, metadata only
def test_staff_override_persists_on_reclassify(env):
    from photo_class import classify_nc
    db, n, f = env["db"], env["nc"]["NC-01"], env["fids"]

    async def ai(nc_text, note, labels, images):
        return [{"i": 0, "location_label": "Window 1", "location_confidence": 0.9, "same_location_as": [1], "component": "end cap", "stage": "after"},
                {"i": 1, "location_label": "Window 1", "location_confidence": 0.9, "component": "end cap", "stage": "after"},
                {"i": 2, "location_label": "Window 2", "location_confidence": 0.9, "component": "end cap", "stage": "before"}]
    run(classify_nc(n, loader=loader, ai=ai))
    c1 = run(db.files.find_one({"_id": ObjectId(f[1])}))["classification"]
    assert c1["dup_of"] == f[0] and c1["location_id"] == "loc-1"
    e = H(env, "employee")
    assert requests.put(f"{BASE}/api/evidence/{f[2]}/classification", headers=H(env, "installer"), json={"stage": "after"}).status_code == 403
    assert requests.put(f"{BASE}/api/evidence/{f[2]}/classification", headers=e, json={"stage": "finished"}).status_code == 422
    assert requests.put(f"{BASE}/api/evidence/{f[2]}/classification", headers=e, json={}).status_code == 400
    assert requests.put(f"{BASE}/api/evidence/{ObjectId()}/classification", headers=e, json={"stage": "after"}).status_code == 404
    r = requests.put(f"{BASE}/api/evidence/{f[2]}/classification", headers=e, json={"stage": "verification", "location_id": "loc-6", "location_label": "Window 6"})
    assert r.status_code == 200 and r.json()["classification"]["stage"] == "verification"
    r = requests.put(f"{BASE}/api/evidence/{f[1]}/classification", headers=H(env, "admin"), json={"not_duplicate": True})
    assert r.status_code == 200 and r.json()["classification"]["dup_of"] is None
    run(classify_nc(n, loader=loader, ai=ai))  # re-run: staff fields must win
    docs = {str(d["_id"]): d for d in run(db.files.find({"nc_id": n}).to_list(10))}
    c2, c1 = docs[f[2]]["classification"], docs[f[1]]["classification"]
    assert c2["stage"] == "verification" and c2["location_id"] == "loc-6" and docs[f[2]]["location_id"] == "loc-6" and c2["source"] == "mixed"
    assert c1["dup_of"] is None
    assert all(d.get("pdf_include") is None and d["status"] == "submitted" for d in docs.values())  # metadata only
    nc = run(db.ncs.find_one({"_id": ObjectId(n)}))
    assert nc["status"] == "open" and "scope" not in nc
    g = requests.get(f"{BASE}/api/ncs/{n}/photos/classification", headers=e).json()
    assert {p["id"]: p["location_id"] for p in g["photos"]}[f[2]] == "loc-6"


# --- suspected gap: staff moves a photo to another location (without not_duplicate) -> should no longer be a duplicate
def test_staff_location_change_clears_cross_location_duplicate(env):
    from photo_class import build, dhash
    hs = [dhash(IM[0]), dhash(IM[1])]
    items = [{"i": 0, "location_label": "Window 1", "location_confidence": 0.9, "same_location_as": [1], "component": "end cap", "stage": "after"},
             {"i": 1, "location_label": "Window 1", "location_confidence": 0.9, "component": "end cap", "stage": "after"}]
    locs = [{"id": f"loc-{i}", "label": f"Window {i}"} for i in range(1, 9)]
    out = build(items, ["a", "b"], hs, locs, {"b": {"staff": {"location_id": "loc-5", "location_label": "Window 5"}}})
    assert out["b"]["location_id"] == "loc-5"
    assert out["b"]["dup_of"] is None, f"photo staff-assigned to Window 5 still flagged duplicate of Window 1 photo: {out['b']['dup_of']}"


# --- suspected gap: transient AI failure must not wipe earlier AI metadata
def test_ai_failure_does_not_wipe_previous_classification(env):
    from photo_class import classify_nc
    db, n, f = env["db"], env["nc"]["NC-01"], env["fids"]

    async def ai_fail(*a):
        return None
    before = run(db.files.find_one({"_id": ObjectId(f[0])}))["classification"]
    run(classify_nc(n, loader=loader, ai=ai_fail))
    after = run(db.files.find_one({"_id": ObjectId(f[0])}))["classification"]
    assert after["location_id"] == before["location_id"] and after["stage"] == before["stage"], \
        f"AI failure overwrote classification: before={before['location_id']},{before['stage']} after={after['location_id']},{after['stage']}"


# --- regression flags OFF: project detail, submit, review, export link + ZIP
def test_project_detail_installer_and_staff(env):
    for role in ("installer", "employee", "admin"):
        r = requests.get(f"{BASE}/api/projects/{env['pid']}", headers=H(env, role))
        assert r.status_code == 200, r.text
        assert "classification" not in r.text  # no Phase 2 metadata leaks into project payload / installer screens


def test_submit_flag_off_no_classification(env, monkeypatch):
    """In-process submit + run_assessment (stub assessor, notifications no-op) so the real preview employee is never notified."""
    import photo_class
    import server
    from core import User
    from fastapi import BackgroundTasks
    n, db = env["nc"]["NC-04"], env["db"]
    called = []

    async def noop(*a, **k):
        return None

    async def spy(nc_id):
        called.append(nc_id)

    class Stub:
        async def assess(self, data):
            from ai import AssessmentResult
            return AssessmentResult(outcome="ai_verified", confidence=0.95, feedback="stub", concerns=[], model="stub", risk="low",
                                    method_demonstrated=True, requests=[])
    for name in ("notify_reviewers", "nc_notify", "notify"):
        monkeypatch.setattr(server, name, noop)
    monkeypatch.setattr(server, "get_assessor", lambda: Stub())
    monkeypatch.setattr(server, "schedule_classification", lambda nc_id: called.append(nc_id))
    monkeypatch.setattr(photo_class, "_load", loader)

    async def fake_aget(path):  # in-memory reads only; nothing touches Emergent storage or Dropbox
        return IM[2], "image/jpeg"
    monkeypatch.setattr(server.storage, "aget", fake_aget)
    monkeypatch.setattr(server.storage, "aput", None)  # any write attempt would raise
    raw = run(db.users.find_one({"_id": ObjectId(env["u"]["installer"])}))
    inst = User.from_mongo(raw) if hasattr(User, "from_mongo") else User(id=str(raw["_id"]), **{k: v for k, v in raw.items() if k != "_id"})
    bg = BackgroundTasks()
    r = run(server.submit(n, bg, None, inst))
    assert r["status"] == "assessing"
    run(server.run_assessment(n, r["submission_id"]))
    d = run(db.files.find_one({"_id": ObjectId(env["dfid"])}))
    assert d["status"] == "submitted" and "classification" not in d and d.get("pdf_include") is None
    assert called == [], "classify_background scheduled although photo_classification_enabled is False"
    st = run(db.ncs.find_one({"_id": ObjectId(n)}))["status"]
    print("NC-04 status after assessment:", st)
    assert st in ("approved", "ai_verified", "employee_review"), st
    # flag ON (briefly, in-process): background tagging scheduled after the decision; decision itself identical
    run(db.settings.update_one({"_id": "global"}, {"$set": {"photo_classification_enabled": True}}))
    try:
        run(server.run_assessment(n, r["submission_id"]))
    finally:
        run(db.settings.update_one({"_id": "global"}, {"$set": {"photo_classification_enabled": False}}))
    run(__import__("asyncio").sleep(0.2))
    assert called == [n], called
    assert run(db.ncs.find_one({"_id": ObjectId(n)}))["status"] == st


def test_review_approve(env):
    n = env["nc"]["NC-03"]
    r = requests.post(f"{BASE}/api/ncs/{n}/review", headers=H(env, "employee"), json={"action": "approve", "note": "QA35"})
    assert r.status_code == 200, r.text
    assert run(env["db"].ncs.find_one({"_id": ObjectId(n)}))["status"] == "approved"
    assert run(env["db"].auto_packs.count_documents({"project_id": env["pid"]})) == 0  # project not completed -> no Dropbox auto-pack


def test_export_link_and_zip(env):
    e = H(env, "employee")
    link = requests.get(f"{BASE}/api/export/link", headers=e, params={"project_id": env["pid"]})
    assert link.status_code == 200 and "url" in link.json()
    z = requests.get(BASE + link.json()["url"], timeout=120)
    assert z.status_code == 200 and z.content[:2] == b"PK", (z.status_code, z.text[:200])
    assert requests.get(f"{BASE}/api/export/link", headers=H(env, "installer"), params={"project_id": env["pid"]}).status_code == 403


# --- suspected gap: AI links two photos but labels disagree (Window 1 vs Window 2) -> must not be flagged duplicates
def test_contradictory_group_not_duplicates():
    from photo_class import build, dhash
    locs = [{"id": f"loc-{i}", "label": f"Window {i}"} for i in range(1, 9)]
    items = [{"i": 0, "location_label": "Window 1", "location_confidence": 0.9, "same_location_as": [1], "component": "end cap", "stage": "after"},
             {"i": 1, "location_label": "Window 2", "location_confidence": 0.9, "component": "end cap", "stage": "after"}]
    out = build(items, ["a", "b"], [dhash(IM[0]), dhash(IM[1])], locs, {})
    assert out["a"]["location_uncertain"] and out["b"]["location_uncertain"]
    assert out["b"]["dup_of"] is None, "photos with contradictory location labels flagged as duplicates via shared location_group"
