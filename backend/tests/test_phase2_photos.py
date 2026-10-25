"""Phase 2 photo classification: pure grouping/duplicate rules (mocked AI), in-process classify_nc with mocked loader
(no Dropbox/storage writes), API gating/permissions/staff override, and one real-Gemini smoke on synthetic labelled images.
QA data only (qa.p2.*@example.com, 'QA TEST 1 Example Road'); removed afterwards. Run: pytest tests/test_phase2_photos.py -n 0"""
import asyncio
import io
import os
import sys

import pytest
import requests
from bson import ObjectId
from PIL import Image, ImageDraw

sys.path.insert(0, "/app/backend")
from photo_class import build, dhash  # noqa: E402

BASE = os.environ.get("RF_BASE") or os.environ.get("EXPO_PUBLIC_BACKEND_URL") or "http://localhost:8001"
sys.path.insert(0, "/app/backend/tests")
from _loop import LOOP  # noqa: E402


def img(seed: int, text: str = "", shift: int = 0) -> bytes:
    """Synthetic 'photo': distinct pattern per seed; same seed + small shift = near-identical framing."""
    im = Image.new("RGB", (320, 240), (200, 200, 200))
    d = ImageDraw.Draw(im)
    for k in range(6):
        x = (seed * 37 + k * 53) % 280
        d.rectangle([x + shift, 20 + k * 30, x + 40 + shift, 40 + k * 30], fill=((seed * 50) % 255, (k * 40) % 255, 120))
    if text:
        d.rectangle([10, 200, 310, 235], fill=(255, 255, 255))
        d.text((16, 208), text, fill=(0, 0, 0))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=90)
    return b.getvalue()


W8 = [{"id": f"loc-{i}", "label": f"Window {i}"} for i in range(1, 9)]


def ids(n):
    return [f"f{i}" for i in range(n)]


def test_eight_similar_windows_not_merged():
    hs = [dhash(img(1)) for _ in range(8)]  # identical-looking installations
    items = [{"i": i, "location_label": None, "location_confidence": 0.2, "same_location_as": [], "component": "window sill end cap", "stage": "after", "stage_confidence": 0.8} for i in range(8)]
    out = build(items, ids(8), hs, W8, {})
    assert all(c["dup_of"] is None and c["location_group"] is None and c["location_uncertain"] for c in out.values())


def test_duplicate_of_same_window():
    hs = [dhash(img(3)), dhash(img(3, shift=1)), dhash(img(9))]
    items = [{"i": 0, "location_label": "Window 3", "location_confidence": 0.9, "same_location_as": [1], "component": "end cap", "stage": "after"},
             {"i": 1, "location_label": None, "location_confidence": 0.3, "same_location_as": [0], "component": "end cap", "stage": "after"},
             {"i": 2, "location_label": "Window 4", "location_confidence": 0.9, "component": "end cap", "stage": "after"}]
    out = build(items, ids(3), hs, W8, {})
    assert out["f1"]["dup_of"] == "f0" and out["f1"]["location_id"] == "loc-3"  # inherits the group's confirmed location
    assert out["f2"]["dup_of"] is None and out["f2"]["location_id"] == "loc-4"


def test_before_after_and_angles_not_duplicates():
    hs = [dhash(img(5)), dhash(img(5, shift=1)), dhash(img(5, shift=1))]
    items = [{"i": 0, "location_label": "Window 1", "location_confidence": 0.9, "same_location_as": [1, 2], "component": "end cap", "stage": "before"},
             {"i": 1, "location_label": "Window 1", "location_confidence": 0.9, "component": "end cap", "stage": "after"},
             {"i": 2, "location_label": "Window 1", "location_confidence": 0.9, "component": "sealant joint", "stage": "after"}]  # other detail
    out = build(items, ids(3), hs, W8, {})
    assert all(c["dup_of"] is None for c in out.values())


def test_four_rooms_vents_and_two_wet_rooms():
    rooms = [{"id": f"loc-{i + 1}", "label": l} for i, l in enumerate(["Living Room", "Bedroom 1", "Bedroom 2", "Bedroom 3"])]
    hs = [dhash(img(7)) for _ in range(4)]
    items = [{"i": i, "location_label": r["label"], "location_confidence": 0.8, "component": "trickle vent", "stage": "after"} for i, r in enumerate(rooms)]
    out = build(items, ids(4), hs, rooms, {})
    assert {c["location_id"] for c in out.values()} == {"loc-1", "loc-2", "loc-3", "loc-4"} and all(c["dup_of"] is None for c in out.values())
    wet = [{"id": "loc-1", "label": "Bathroom"}, {"id": "loc-2", "label": "Kitchen"}]
    out = build([{"i": 0, "location_label": "Bathroom", "location_confidence": 0.9, "component": "extractor fan", "stage": "after"},
                 {"i": 1, "location_label": "Kitchen", "location_confidence": 0.9, "component": "extractor fan", "stage": "after"}], ids(2), [dhash(img(2))] * 2, wet, {})
    assert out["f0"]["location_id"] != out["f1"]["location_id"] and out["f1"]["dup_of"] is None


def test_elevations_invented_label_and_contradiction():
    el = [{"id": "loc-1", "label": "Front elevation"}, {"id": "loc-2", "label": "Rear elevation"}]
    out = build([{"i": 0, "location_label": "Front elevation", "location_confidence": 0.9, "same_location_as": [1], "stage": "during"},
                 {"i": 1, "location_label": "Rear elevation", "location_confidence": 0.9, "same_location_as": [0], "stage": "during"},
                 {"i": 2, "location_label": "Side elevation", "location_confidence": 0.9, "stage": "after"}], ids(3), [dhash(img(i)) for i in range(3)], el, {})
    assert out["f0"]["location_uncertain"] and out["f1"]["location_uncertain"]  # contradictory group -> uncertain, not merged
    assert out["f2"]["location_id"] is None  # label not in scope -> never invented


def test_staff_overrides_win():
    hs = [dhash(img(3)), dhash(img(3, shift=1))]
    items = [{"i": 0, "location_label": "Window 3", "location_confidence": 0.9, "same_location_as": [1], "component": "end cap", "stage": "after"},
             {"i": 1, "location_label": "Window 3", "location_confidence": 0.9, "component": "end cap", "stage": "after"}]
    existing = {"f1": {"staff": {"location_id": "loc-5", "location_label": "Window 5", "not_duplicate": True}}}
    out = build(items, ids(2), hs, W8, existing)
    assert out["f1"]["location_id"] == "loc-5" and out["f1"]["dup_of"] is None and out["f1"]["source"] == "mixed"


# ---------------- in-process + API ----------------
@pytest.fixture(scope="module")
def qa():
    from auth import ph
    from core import db, now_iso
    pw = "QaP2-" + str(ObjectId())[-8:]

    async def setup():
        await db.users.delete_many({"email": {"$regex": r"^qa\.p2\."}})  # leftovers from an interrupted run
        u = {}
        for role in ("admin", "employee", "installer"):
            u[role] = str((await db.users.insert_one({"email": f"qa.p2.{role}@example.com", "name": f"QA {role}", "role": role, "active": True,
                                                       "password_hash": ph.hash(pw), "token_version": 0, "created_at": now_iso()})).inserted_id)
        p = str((await db.projects.insert_one({"ref": "QA-P2", "address": "QA TEST 1 Example Road", "postcode": "QA1 1AA", "installer_id": u["installer"],
                                               "rate": 0, "status": "active", "deleted": None, "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)
        t = "End caps missing to 8 no. external window sills (W1-W8)."
        n = str((await db.ncs.insert_one({"project_id": p, "number": "NC-01", "description": t, "original_text": t, "required_work": "", "status": "open",
                                          "assigned_installer_id": u["installer"], "updated_at": now_iso()})).inserted_id)
        f = []
        for k in range(4):  # paths are fake: tests inject a loader, nothing is read from or written to Dropbox/storage
            f.append(str((await db.files.insert_one({"project_id": p, "nc_id": n, "kind": "photo", "status": "submitted", "pdf_include": None,
                                                     "source": "camera", "sha256": f"qa{k}", "size": 1, "mime": "image/jpeg", "uploaded_by": u["installer"],
                                                     "paths": {"display": f"qa-mem:{k}"}, "server_received_at": f"2026-01-01T00:0{k}:00"})).inserted_id))
        return u, p, n, f, (await db.settings.find_one({"_id": "global"}) or {}).get("photo_classification_enabled", False)
    u, p, n, f, old = LOOP.run_until_complete(setup())
    yield {"pw": pw, "pid": p, "nid": n, "fids": f}

    async def teardown():
        await db.settings.update_one({"_id": "global"}, {"$set": {"photo_classification_enabled": old}})
        await db.files.delete_many({"project_id": p})
        await db.ncs.delete_many({"project_id": p})
        await db.projects.delete_one({"_id": ObjectId(p)})
        await db.sessions.delete_many({"user_id": {"$in": list(u.values())}})
        await db.users.delete_many({"email": {"$regex": r"^qa\.p2\."}})
    LOOP.run_until_complete(teardown())


IMGS = [img(1, "W1 end cap - before"), img(1, "W1 end cap - fitted"), img(2, "W2 end cap - fitted"), img(2, "W2 end cap - fitted", shift=1)]


async def fake_loader(f):
    return IMGS[int(f["paths"]["display"].split(":")[1])]


def test_classify_nc_writes_metadata_only(qa):
    from core import db
    from photo_class import classify_nc

    async def fake_ai(nc_text, note, labels, images):
        return [{"i": 0, "location_label": "Window 1", "location_confidence": 0.9, "same_location_as": [1], "component": "end cap", "stage": "before"},
                {"i": 1, "location_label": "Window 1", "location_confidence": 0.9, "component": "end cap", "stage": "after"},
                {"i": 2, "location_label": "Window 2", "location_confidence": 0.9, "same_location_as": [3], "component": "end cap", "stage": "after"},
                {"i": 3, "location_label": "Window 2", "location_confidence": 0.9, "component": "end cap", "stage": "after"}]
    LOOP.run_until_complete(classify_nc(qa["nid"], loader=fake_loader, ai=fake_ai))
    fs = LOOP.run_until_complete(db.files.find({"nc_id": qa["nid"]}).sort("server_received_at", 1).to_list(10))
    assert [x["location_id"] for x in fs] == ["loc-1", "loc-1", "loc-2", "loc-2"]
    assert fs[3]["classification"]["dup_of"] == str(fs[2]["_id"]) and fs[1]["classification"]["dup_of"] is None
    assert all(x.get("pdf_include") is None and x["status"] == "submitted" for x in fs)  # nothing excluded or deleted
    nc = LOOP.run_until_complete(db.ncs.find_one({"_id": ObjectId(qa["nid"])}))
    assert nc["status"] == "open" and "scope" not in nc  # no decision change, Phase 1 storage untouched


def test_classify_links_across_batches(qa, monkeypatch):
    """Later batches receive reference photos from earlier batches, so the same location is linked across the batch boundary."""
    import photo_class
    from core import db
    monkeypatch.setattr(photo_class, "BATCH", 2)
    calls = []

    async def fake_ai(nc_text, note, labels, images, refs=None):
        calls.append([(r["component"], r["stage"]) for r in (refs or [])])
        if not refs:
            return [{"i": 0, "component": "end cap", "stage": "before"}, {"i": 1, "component": "end cap", "stage": "after"}]
        return [{"i": 0, "component": "end cap", "stage": "after", "same_location_as": ["R1"]},
                {"i": 1, "component": "end cap", "stage": "after", "same_location_as": ["R9", 0]}]  # unknown ref ignored
    LOOP.run_until_complete(photo_class.classify_nc(qa["nid"], loader=fake_loader, ai=fake_ai))
    assert calls == [[], [("end cap", "before"), ("end cap", "after")]]
    fs = LOOP.run_until_complete(db.files.find({"nc_id": qa["nid"]}).sort("server_received_at", 1).to_list(10))
    g = [x["classification"]["location_group"] for x in fs]
    assert g[1] and g[1] == g[2] == g[3] and g[0] is None
    assert all(x["status"] == "submitted" for x in fs)


def test_real_gemini_smoke(qa):
    from photo_class import ai_classify
    out = LOOP.run_until_complete(ai_classify("End caps missing to 8 no. external window sills (W1-W8).", "", [l["label"] for l in W8], IMGS))
    assert out is not None and len(out) == 4
    print("GEMINI:", [(o.get("i"), o.get("location_label"), o.get("stage"), o.get("same_location_as")) for o in out])
    assert not any(o.get("location_label") in ("Window 3", "Window 4", "Window 5") for o in out)  # no invented numbers


def login(role, pw):
    r = requests.post(f"{BASE}/api/auth/login", json={"email": f"qa.p2.{role}@example.com", "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_api_gating_permissions_override(qa):
    a, e, i = login("admin", qa["pw"]), login("employee", qa["pw"]), login("installer", qa["pw"])
    s = requests.get(f"{BASE}/api/settings", headers=a).json()
    assert s["photo_classification_enabled"] is False and s["location_scope_enabled"] is False
    assert requests.post(f"{BASE}/api/ncs/{qa['nid']}/photos/classify", headers=e).status_code == 409
    assert requests.get(f"{BASE}/api/ncs/{qa['nid']}/photos/classification", headers=i).status_code == 403
    fid = qa["fids"][3]
    assert requests.put(f"{BASE}/api/evidence/{fid}/classification", headers=i, json={"stage": "after"}).status_code == 403
    r = requests.put(f"{BASE}/api/evidence/{fid}/classification", headers=e, json={"location_id": "loc-7", "location_label": "Window 7", "not_duplicate": True})
    assert r.status_code == 200 and r.json()["classification"]["location_id"] == "loc-7" and r.json()["classification"]["dup_of"] is None
    assert requests.put(f"{BASE}/api/evidence/{fid}/classification", headers=e, json={"stage": "finished"}).status_code == 422
    g = requests.get(f"{BASE}/api/ncs/{qa['nid']}/photos/classification", headers=e).json()
    assert g["enabled"] is False and any(p["location_id"] == "loc-7" for p in g["photos"])
    pd = requests.get(f"{BASE}/api/projects/{qa['pid']}", headers=i)
    assert pd.status_code == 200  # installer project view unaffected
