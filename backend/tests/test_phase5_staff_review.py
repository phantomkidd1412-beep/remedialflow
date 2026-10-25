"""Phase 5 - staff review controls. Permissions, flag gating, corrections persisting over AI, on-demand classification
(AI + storage mocked in-process), scope confirmation, pack versioning. QA data only; no Dropbox, no real AI."""
import io
import os
import sys

import pytest
import requests
from bson import ObjectId

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")
from _loop import LOOP  # noqa: E402

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "").rstrip("/")
W8 = "End caps missing to 8 no. external window sills (W1-W8)."
FLAGS = ("staff_review_enabled", "smart_pdf_enabled", "coverage_check_enabled", "photo_classification_enabled", "location_scope_enabled")


def run(c):
    return LOOP.run_until_complete(c)


def jpeg(c):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (64, 64), c).save(b, "JPEG")
    return b.getvalue()


@pytest.fixture(scope="module")
def qa():
    from auth import ph
    from core import db, now_iso
    pw = "QaP5-" + str(ObjectId())[-8:]
    orig = run(db.settings.find_one({"_id": "global"})) or {}

    async def setup():
        await db.users.delete_many({"email": {"$regex": r"^qa\.p5\."}})
        u = {}
        for role in ("admin", "employee", "installer"):
            u[role] = str((await db.users.insert_one({"email": f"qa.p5.{role}@example.com", "name": f"QA5 {role}", "role": role, "active": True,
                                                       "password_hash": ph.hash(pw), "token_version": 0, "created_at": now_iso()})).inserted_id)
        p = str((await db.projects.insert_one({"ref": "QA-P5", "address": "QA TEST 1 Example Road", "postcode": "QA1 1AA", "installer_id": u["installer"],
                                               "rate": 0, "status": "active", "deleted": None, "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)
        n = str((await db.ncs.insert_one({"project_id": p, "number": "NC-01", "description": W8, "original_text": W8, "required_work": "", "status": "approved",
                                          "assigned_installer_id": u["installer"], "updated_at": now_iso()})).inserted_id)
        f = []
        for k in range(3):
            f.append(str((await db.files.insert_one({"project_id": p, "nc_id": n, "kind": "photo", "status": "submitted", "pdf_include": None,
                                                     "source": "in_app_camera", "sha256": f"qa{k}", "size": 1, "mime": "image/jpeg", "uploaded_by": u["installer"],
                                                     "paths": {"display": f"qa-mem:{k}"}, "server_received_at": f"2026-01-01T00:0{k}:00"})).inserted_id))
        return u, p, n, f
    u, p, n, f = run(setup())
    yield {"pw": pw, "pid": p, "nid": n, "fids": f, "db": db}

    async def teardown():
        await db.settings.update_one({"_id": "global"}, {"$set": {k: orig.get(k, False) for k in FLAGS}})
        for c in ("files", "ncs"):
            await db[c].delete_many({"project_id": p})
        await db.projects.delete_one({"_id": ObjectId(p)})
        await db.sessions.delete_many({"user_id": {"$in": list(u.values())}})
        await db.users.delete_many({"email": {"$regex": r"^qa\.p5\."}})
    run(teardown())


def flags(qa, **kw):
    run(qa["db"].settings.update_one({"_id": "global"}, {"$set": {k: kw.get(k, False) for k in FLAGS}}))


def login(qa, role):
    r = requests.post(f"{BASE}/api/auth/login", json={"email": f"qa.p5.{role}@example.com", "password": qa["pw"]})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


pytestmark = pytest.mark.skipif(not BASE, reason="public URL not set")


def test_flag_off_preserves_behaviour(qa):
    flags(qa)
    e = login(qa, "employee")
    assert requests.get(f"{BASE}/api/ncs/{qa['nid']}/evidence-review", headers=e).json() == {"enabled": False}
    assert requests.post(f"{BASE}/api/ncs/{qa['nid']}/evidence-review/classify", headers=e, json={}).status_code == 409
    assert requests.put(f"{BASE}/api/ncs/{qa['nid']}/scope", headers=e, json={"expected_count": 8, "basis": "NC wording"}).status_code == 409
    nc = run(qa["db"].ncs.find_one({"_id": ObjectId(qa["nid"])}))
    assert nc["status"] == "approved" and "scope" not in nc


def test_installer_forbidden(qa):
    flags(qa, staff_review_enabled=True)
    i = login(qa, "installer")
    assert requests.get(f"{BASE}/api/ncs/{qa['nid']}/evidence-review", headers=i).status_code == 403
    assert requests.post(f"{BASE}/api/ncs/{qa['nid']}/evidence-review/classify", headers=i, json={}).status_code == 403
    assert requests.put(f"{BASE}/api/ncs/{qa['nid']}/scope", headers=i, json={"expected_count": 2, "basis": "NC wording"}).status_code == 403
    assert requests.put(f"{BASE}/api/evidence/{qa['fids'][0]}/classification", headers=i, json={"stage": "after"}).status_code == 403
    d = requests.get(f"{BASE}/api/projects/{qa['pid']}", headers=i).json()
    assert all("scope" not in x and "coverage" not in x for x in d["ncs"])


def test_review_preview_without_ai(qa):
    flags(qa, staff_review_enabled=True)
    r = requests.get(f"{BASE}/api/ncs/{qa['nid']}/evidence-review", headers=login(qa, "employee")).json()
    assert r["enabled"] and r["unclassified"] == 3 and r["scope"]["expected_count"] == 8 and r["scope"]["method"] == "rules_preview"
    assert r["coverage"]["status"] == "unverified"
    assert "scope" not in run(qa["db"].ncs.find_one({"_id": ObjectId(qa["nid"])}))  # preview only, nothing stored


def test_on_demand_classify_mocked_and_skips_repeat(qa, monkeypatch):
    import photo_class
    import staff_review
    from core import User
    flags(qa, staff_review_enabled=True)
    calls = []

    async def fake_ai(nc_text, note, labels, images, refs=None):
        calls.append(len(images))
        return [{"i": 0, "location_label": "Window 1", "location_confidence": 0.9, "component": "end cap", "stage": "after"},
                {"i": 1, "location_label": "Window 2", "location_confidence": 0.9, "component": "end cap", "stage": "after"},
                {"i": 2, "location_label": None, "component": "end cap", "stage": "before"}]

    async def fake_load(f):
        return jpeg((int(f["paths"]["display"][-1]) * 90, 0, 0))
    monkeypatch.setattr(photo_class, "ai_classify", fake_ai)
    monkeypatch.setattr(photo_class, "_load", fake_load)
    u = User.from_mongo(run(qa["db"].users.find_one({"email": "qa.p5.employee@example.com"})))
    r = run(staff_review.classify_on_demand(qa["nid"], staff_review.ClassifyIn(), u))
    assert r["ran"] and r["unclassified"] == 0 and calls == [3]
    assert [p["location_label"] for p in r["photos"]] == ["Window 1", "Window 2", None]
    r = run(staff_review.classify_on_demand(qa["nid"], staff_review.ClassifyIn(), u))
    assert not r["ran"] and calls == [3]  # nothing new -> no second AI call
    assert run(qa["db"].ncs.find_one({"_id": ObjectId(qa["nid"])}))["status"] == "approved"  # decisions untouched
    assert all(f["status"] == "submitted" for f in run(qa["db"].files.find({"nc_id": qa["nid"]}).to_list(10)))  # source evidence intact


def test_staff_corrections_override_ai_and_persist(qa, monkeypatch):
    import photo_class
    flags(qa, staff_review_enabled=True)
    e = login(qa, "employee")
    f3 = qa["fids"][2]
    assert requests.put(f"{BASE}/api/evidence/{f3}/classification", headers=e, json={"location_id": "loc-3", "stage": "after"}).status_code == 200
    r = requests.get(f"{BASE}/api/ncs/{qa['nid']}/evidence-review", headers=e).json()
    p3 = r["photos"][2]
    assert p3["location_label"] == "Window 3" and p3["stage"] == "after" and "location_id" in p3["staff"]

    async def ai_disagrees(nc_text, note, labels, images, refs=None):
        return [{"i": k, "location_label": "Window 8", "location_confidence": 0.95, "component": "end cap", "stage": "before"} for k in range(len(images))]

    async def fake_load(f):
        return jpeg((10, 10, 10))
    run(photo_class.classify_nc(qa["nid"], loader=fake_load, ai=ai_disagrees))  # e.g. a later assessment re-run
    c = run(qa["db"].files.find_one({"_id": ObjectId(f3)}))["classification"]
    assert c["location_id"] == "loc-3" and c["stage"] == "after" and c["source"] == "mixed"


def test_staff_scope_needs_basis_and_is_kept(qa):
    from scope import ensure_scope
    flags(qa, staff_review_enabled=True)
    e = login(qa, "employee")
    u = f"{BASE}/api/ncs/{qa['nid']}/scope"
    for fid, loc in ((qa["fids"][0], "loc-1"), (qa["fids"][1], "loc-2")):  # the AI re-run above re-tagged these (no staff fields): staff confirm them
        assert requests.put(f"{BASE}/api/evidence/{fid}/classification", headers=e, json={"location_id": loc, "stage": "after"}).status_code == 200
    assert requests.put(u, headers=e, json={"expected_count": 3}).status_code == 422  # basis required
    assert requests.put(u, headers=e, json={"expected_count": 3, "labels": ["A", "B"], "basis": "TrustMark report p.4"}).status_code == 400
    r = requests.put(u, headers=e, json={"expected_count": 3, "basis": "TrustMark report p.4 lists W1-W3 only"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["scope"]["method"] == "staff" and j["scope"]["expected_count"] == 3 and [l["label"] for l in j["scope"]["locations"]] == ["Window 1", "Window 2", "Window 3"]
    assert j["coverage"]["status"] == "complete"  # W1, W2, W3 confirmed by staff - all completed photos
    assert run(ensure_scope(qa["nid"], force=True))["method"] == "staff"  # later extraction keeps the staff scope
    nc = run(qa["db"].ncs.find_one({"_id": ObjectId(qa["nid"])}))
    assert nc["status"] == "approved" and len(nc["scope"]["history"]) == 1 and nc["scope"]["history"][0]["expected_count"] == 8  # replaced scope kept


def test_not_duplicate_and_pack_versions(qa):
    import server
    db = qa["db"]
    f1, f2 = qa["fids"][0], qa["fids"][1]
    run(db.files.update_one({"_id": ObjectId(f2)}, {"$set": {"classification.dup_of": f1, "classification.dup_group": "dup-1"}}))
    p = run(db.projects.find_one({"_id": ObjectId(qa["pid"])}))
    flags(qa, staff_review_enabled=True)
    off1 = run(server.pack_fingerprint(qa["pid"], p))
    e = login(qa, "employee")
    assert requests.put(f"{BASE}/api/evidence/{f2}/classification", headers=e, json={"not_duplicate": True}).status_code == 200
    assert run(server.pack_fingerprint(qa["pid"], p)) == off1  # smart PDF off: pack fingerprint unchanged (legacy behaviour)
    flags(qa, staff_review_enabled=True, smart_pdf_enabled=True)
    on1 = run(server.pack_fingerprint(qa["pid"], p))
    assert requests.put(f"{BASE}/api/evidence/{f2}/classification", headers=e, json={"stage": "verification"}).status_code == 200
    on2 = run(server.pack_fingerprint(qa["pid"], p))
    assert on1 != on2  # a correction makes a NEW pack version (old cached/exported versions are left as they are)
    assert run(db.files.find_one({"_id": ObjectId(f2)}))["classification"]["dup_of"] is None


def test_pdf_regenerates_from_corrections(qa):
    import server
    from scope import merge, rule_extract  # noqa: F401
    db = qa["db"]
    flags(qa, staff_review_enabled=True, smart_pdf_enabled=True)
    n = run(db.ncs.find_one({"_id": ObjectId(qa["nid"])}))
    files = run(db.files.find({"nc_id": qa["nid"]}).sort("server_received_at", 1).to_list(10))

    async def get(f, *v):
        return jpeg((int(f["paths"]["display"][-1]) * 90, 0, 0))
    g = run(server.pdf_photo_groups(n, files, get))
    heads = [h for h, _ in g]
    assert heads == ["WINDOW 1 - COMPLETED WORK", "WINDOW 2 - MEASUREMENT / TEST EVIDENCE", "WINDOW 3 - COMPLETED WORK"], heads
    assert n["coverage_note"] == "Evidence shown for all 3 windows."
