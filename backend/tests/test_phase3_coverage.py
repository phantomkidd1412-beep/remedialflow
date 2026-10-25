"""Phase 3 - evidence coverage verification. Pure rules + run_assessment integration (stubbed assessor + stubbed photo tagger).
QA data only (qa.p3.*@example.com, 'QA TEST 1 Example Road'); removed afterwards. Nothing is written to Dropbox/storage."""
import io
import os
import sys

import pytest
import requests
from bson import ObjectId

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")
from _loop import LOOP  # noqa: E402
from coverage import installer_request, needs_verification, verify  # noqa: E402
from scope import merge, rule_extract  # noqa: E402

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "").rstrip("/")
W8 = "End caps missing to 8 no. external window sills (W1-W8)."
R5 = "Trickle vents missing in five bedrooms."
WET = "Extractor fans not installed in the bathroom and kitchen."
ELEV = "EWI not continuous at the front and rear elevations."
TEST_NC = "Extractor fan in the bathroom has not been commissioned; airflow test results required."


def run(c):
    return LOOP.run_until_complete(c)


def sc(t):
    return merge(rule_extract(t), None, t, None)


def ph(i, loc=None, stage="after", grp=None, dup=None, relevant=True):
    return {"_id": f"f{i}", "kind": "photo", "status": "submitted", "pdf_include": None,
            "classification": {"location_id": loc, "stage": stage, "location_group": grp, "dup_of": dup, "relevant": relevant}}


# ---------------- pure rules ----------------
def test_eight_windows_six_evidenced_is_incomplete_with_names():
    c = verify(sc(W8), [ph(i, f"loc-{i}") for i in range(1, 7)])
    assert c["status"] == "incomplete" and c["missing"] == ["Window 7", "Window 8"] and c["covered"] == 6
    msg = installer_request(sc(W8), c)
    assert "2 more windows" in msg and "Window 7, Window 8" in msg


def test_eight_windows_all_evidenced_is_complete():
    assert verify(sc(W8), [ph(i, f"loc-{i}") for i in range(1, 9)])["status"] == "complete"


def test_five_rooms_one_missing():
    s = sc(R5)
    assert s["expected_count"] == 5
    c = verify(s, [ph(i, f"loc-{i}") for i in (1, 2, 3, 5)])
    assert c["status"] == "incomplete" and c["missing"] == ["Habitable room 4"]
    assert "1 more room " in installer_request(s, c)


def test_multiple_wet_rooms():
    s = sc(WET)
    ids = {l["label"]: l["id"] for l in s["locations"]}
    assert verify(s, [ph(1, ids["Bathroom"]), ph(2, ids["Kitchen"])])["status"] == "complete"
    c = verify(s, [ph(1, ids["Bathroom"]), ph(2, ids["Bathroom"])])  # two photos of ONE room are not two rooms
    assert c["status"] == "incomplete" and c["missing"] == ["Kitchen"]


def test_multiple_elevations():
    s = sc(ELEV)
    ids = {l["label"]: l["id"] for l in s["locations"]}
    assert verify(s, [ph(1, ids["Front elevation"]), ph(2, ids["Rear elevation"])])["status"] == "complete"
    assert verify(s, [ph(1, ids["Front elevation"])])["missing"] == ["Rear elevation"]


def test_duplicates_of_one_location_count_once():
    c = verify(sc(W8), [ph(1, "loc-1")] + [ph(i, "loc-1", dup="f1") for i in range(2, 9)])
    assert c["status"] == "incomplete" and c["covered"] == 1 and len(c["missing"]) == 7


def test_same_group_untagged_counts_as_one_location():
    c = verify(sc(W8), [ph(i, None, grp="grp-1") for i in range(8)])  # 8 photos, all of the same (unnamed) window
    assert c["status"] == "incomplete" and c["max_possible"] == 1 and c["missing"] == [] and c["missing_count"] == 7
    assert "remaining 7 windows" in installer_request(sc(W8), c)


def test_uncertain_identity_is_unverified_not_rejected():
    c = verify(sc(W8), [ph(i, None) for i in range(8)])  # 8 untagged similar photos: may or may not be 8 windows
    assert c["status"] == "unverified"
    c = verify(sc(W8), [ph(i, f"loc-{i}") for i in range(1, 7)] + [ph(9, None), ph(10, None)])  # 6 known + 2 unknown
    assert c["status"] == "unverified"


def test_showing_location_is_not_completed_work():
    c = verify(sc(W8), [ph(i, f"loc-{i}") for i in range(1, 7)] + [ph(7, "loc-7", stage="before"), ph(8, "loc-8", stage="during")])
    assert c["status"] == "incomplete" and c["missing"] == ["Window 7", "Window 8"] and c["not_completed"] == ["Window 7", "Window 8"]
    single = sc("Insulation continuity not maintained around the external wall bracket.")
    assert verify(single, [ph(1, None, stage="before")])["status"] == "unverified"  # never auto-rejected on a stage guess


def test_irrelevant_photos_do_not_count():
    c = verify(sc(W8), [ph(i, f"loc-{i}") for i in range(1, 8)] + [ph(8, "loc-8", relevant=False)])
    assert c["status"] == "incomplete" and c["missing"] == ["Window 8"]


def test_unknown_count_and_unclassified_are_unverified():
    assert verify(sc("Background ventilation not provided in all habitable rooms."), [ph(1, None)])["status"] == "unverified"
    assert verify(sc(W8), [{"_id": "x", "kind": "photo", "status": "submitted"}])["status"] == "unverified"
    assert verify(None, [ph(1)])["status"] == "unverified"


def test_testing_or_certification_requirement_preserved():
    nc = {"original_text": TEST_NC}
    assert needs_verification(nc) and not needs_verification({"original_text": W8})
    s = sc(TEST_NC)
    assert s["expected_count"] == 1
    assert verify(s, [ph(1, s["locations"][0]["id"], stage="after")], True)["status"] == "unverified"
    assert verify(s, [ph(1, None, stage="after"), ph(2, None, stage="verification")], True)["status"] == "complete"
    s8 = sc(W8)
    assert verify(s8, [ph(i, f"loc-{i}") for i in range(1, 9)], True)["status"] == "unverified"


# ---------------- run_assessment integration ----------------
def jpeg():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (64, 64), (120, 120, 120)).save(b, "JPEG")
    return b.getvalue()


@pytest.fixture(scope="module")
def env():
    import server
    from auth import ph as hasher
    from core import db, now_iso
    from storage import storage
    pw = "QaP3-" + str(ObjectId())[-8:]
    orig = run(db.settings.find_one({"_id": "global"})) or {}
    ids = {}

    async def setup():
        await db.users.delete_many({"email": {"$regex": r"^qa\.p3\."}})
        for role in ("admin", "employee", "installer"):
            ids[role] = str((await db.users.insert_one({"email": f"qa.p3.{role}@example.com", "name": f"QA3 {role}", "role": role, "active": True,
                                                         "password_hash": hasher.hash(pw), "token_version": 0, "created_at": now_iso()})).inserted_id)
        return str((await db.projects.insert_one({"ref": "QA-P3", "address": "QA TEST 1 Example Road", "postcode": "QA1 1AA", "installer_id": ids["installer"],
                                                  "rate": 0, "status": "active", "require_video": False, "deleted": None,
                                                  "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)
    pid = run(setup())
    orig_aget = storage.aget

    async def mem_aget(path, *a, **k):  # test evidence never touches real storage/Dropbox
        return (jpeg(), "image/jpeg") if str(path).startswith("qa-mem:") else await orig_aget(path, *a, **k)
    storage.aget = mem_aget
    yield {"pw": pw, "pid": pid, "ids": ids, "server": server, "db": db, "now": now_iso}
    storage.aget = orig_aget

    async def teardown():
        await db.settings.update_one({"_id": "global"}, {"$set": {k: orig.get(k, d) for k, d in (
            ("coverage_check_enabled", False), ("location_scope_enabled", False), ("photo_classification_enabled", False), ("auto_verify", True))}})
        for c in ("ncs", "files", "submissions"):
            await db[c].delete_many({"project_id": pid})
        await db.notifications.delete_many({"user_id": {"$in": list(ids.values())}})
        await db.projects.delete_one({"_id": ObjectId(pid)})
        await db.sessions.delete_many({"user_id": {"$in": list(ids.values())}})
        await db.users.delete_many({"email": {"$regex": r"^qa\.p3\."}})
    run(teardown())


class Stub:
    def __init__(self, outcome="ai_verified", risk="low", conf=0.95):
        self.outcome, self.risk, self.conf = outcome, risk, conf

    async def assess(self, data):
        from ai import AssessmentResult
        return AssessmentResult(outcome=self.outcome, confidence=self.conf, feedback="stub", concerns=[], model="stub", risk=self.risk,
                                method_demonstrated=True, requests=["Upload a closer photo"] if self.outcome == "more_evidence_required" else [])


def assess(env, monkeypatch, text, tags, flag=True, number="NC-01", **stub):
    """tags: list of (location_id, stage, group) the stubbed Phase 2 tagger writes, one per photo; None = tagger unavailable."""
    srv, db, now = env["server"], env["db"], env["now"]
    import photo_class
    monkeypatch.setattr(srv, "get_assessor", lambda: Stub(**stub))
    sent = []

    async def noop(*a, **k):
        sent.append(a)
    monkeypatch.setattr(srv, "notify_reviewers", noop)
    monkeypatch.setattr(srv, "nc_notify", noop)

    async def fake_classify(nc_id, *a, **k):
        if tags is None:
            return {}
        fs = await db.files.find({"nc_id": nc_id, "kind": "photo"}).sort("server_received_at", 1).to_list(100)
        for f, (loc, st, grp) in zip(fs, tags):
            await db.files.update_one({"_id": f["_id"]}, {"$set": {"classification": {"location_id": loc, "stage": st, "location_group": grp,
                                                                                       "relevant": True, "dup_of": None, "source": "ai"}}})
        return {}
    monkeypatch.setattr(photo_class, "classify_nc", fake_classify)
    run(db.settings.update_one({"_id": "global"}, {"$set": {"coverage_check_enabled": flag, "location_scope_enabled": False,
                                                            "photo_classification_enabled": False, "auto_verify": True}}))

    async def go():
        nid = str((await db.ncs.insert_one({"project_id": env["pid"], "number": number, "description": text, "required_work": "", "original_text": text,
                                            "status": "assessing", "safety_critical": False, "assigned_installer_id": env["ids"]["installer"],
                                            "updated_at": now()})).inserted_id)
        sid = str((await db.submissions.insert_one({"project_id": env["pid"], "nc_id": nid, "installer_id": env["ids"]["installer"], "status": "submitted",
                                                    "photo_ids": [], "submitted_at": now(), "created_at": now()})).inserted_id)
        fids = []
        for k in range(max(1, len(tags or [None]))):
            fids.append(str((await db.files.insert_one({"project_id": env["pid"], "nc_id": nid, "submission_id": sid, "kind": "photo", "source": "in_app_camera",
                                                        "paths": {"original": "qa-mem:x", "display": "qa-mem:x"}, "sha256": f"qa{k}", "size": 10,
                                                        "mime": "image/jpeg", "uploaded_by": env["ids"]["installer"], "status": "submitted",
                                                        "processing": "ready", "location_verified": True,
                                                        "server_received_at": f"2026-01-01T00:{k:02d}:00"})).inserted_id))
        await db.submissions.update_one({"_id": ObjectId(sid)}, {"$set": {"photo_ids": fids}})
        await srv.run_assessment(nid, sid)
        return await db.ncs.find_one({"_id": ObjectId(nid)})
    return run(go()), sent


def W(n):
    return [(f"loc-{i}", "after", None) for i in range(1, n + 1)]


def test_flag_off_unchanged(env, monkeypatch):
    nc, _ = assess(env, monkeypatch, W8, W(6), flag=False)
    assert nc["status"] == "approved" and nc.get("coverage") is None


def test_incomplete_uses_more_evidence_flow(env, monkeypatch):
    nc, sent = assess(env, monkeypatch, W8, W(6))
    assert nc["status"] == "more_evidence" and nc["ai"]["status"] == "more_evidence"
    assert nc["coverage"]["status"] == "incomplete" and nc["coverage"]["missing"] == ["Window 7", "Window 8"]
    assert "Window 7, Window 8" in nc["ai"]["feedback"]
    assert any("more evidence needed" in str(a) for a in sent)  # existing installer notification


def test_complete_still_needs_existing_checks(env, monkeypatch):
    nc, _ = assess(env, monkeypatch, W8, W(8))
    assert nc["status"] == "approved" and nc["coverage"]["status"] == "complete"
    nc, _ = assess(env, monkeypatch, W8, W(8), conf=0.5)  # complete coverage never overrides the confidence threshold
    assert nc["status"] == "employee_review"
    nc, _ = assess(env, monkeypatch, W8, W(8), outcome="manual_review_required")  # nor an uncertain AI verdict
    assert nc["status"] == "employee_review"


def test_uncertain_routes_to_employee_review(env, monkeypatch):
    nc, _ = assess(env, monkeypatch, W8, [(None, "after", None)] * 8)
    assert nc["status"] == "employee_review" and any("Location coverage unverified" in b for b in nc["ai"]["blocked"])
    nc, _ = assess(env, monkeypatch, W8, None)  # tagger unavailable -> unverified, not rejected
    assert nc["status"] == "employee_review" and nc["coverage"]["status"] == "unverified"


def test_five_rooms_and_duplicates_in_assessment(env, monkeypatch):
    nc, _ = assess(env, monkeypatch, R5, [("loc-1", "after", None), ("loc-2", "after", None), ("loc-2", "after", None), ("loc-3", "after", None), ("loc-5", "after", None)])
    assert nc["status"] == "more_evidence" and nc["coverage"]["missing"] == ["Habitable room 4"]


def test_safety_incomplete_stays_with_employee(env, monkeypatch):
    nc, _ = assess(env, monkeypatch, "Gas meter box brackets missing at 2 no. elevations (front and rear elevations).", [("loc-1", "after", None)])
    assert nc["coverage"]["status"] == "incomplete"
    assert nc["status"] == "employee_review" and any("Safety-critical" in b for b in nc["ai"]["blocked"])


def test_more_evidence_ai_request_kept_and_extended(env, monkeypatch):
    nc, _ = assess(env, monkeypatch, W8, W(6), outcome="more_evidence_required")
    assert nc["status"] == "more_evidence" and "Upload a closer photo" in nc["ai"]["feedback"] and "Window 7" in nc["ai"]["feedback"]


def test_potential_noncompliance_not_auto_rejected(env, monkeypatch):
    nc, _ = assess(env, monkeypatch, W8, W(6), outcome="potential_noncompliance")
    assert nc["status"] == "employee_review"


def test_certification_required_routes_to_review(env, monkeypatch):
    nc, _ = assess(env, monkeypatch, TEST_NC, [(None, "after", None)])
    assert nc["coverage"]["status"] == "unverified" and nc["status"] == "employee_review"
    nc, _ = assess(env, monkeypatch, TEST_NC, [(None, "after", None), (None, "verification", None)])
    assert nc["coverage"]["status"] == "complete" and nc["status"] == "approved"


@pytest.mark.skipif(not BASE, reason="public URL not set")
def test_api_staff_only_and_hidden_from_installer(env, monkeypatch):
    nc, _ = assess(env, monkeypatch, W8, W(6), number="NC-API")

    def login(role):
        r = requests.post(f"{BASE}/api/auth/login", json={"email": f"qa.p3.{role}@example.com", "password": env["pw"]})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}
    e, i = login("employee"), login("installer")
    r = requests.get(f"{BASE}/api/ncs/{nc['_id']}/coverage", headers=e)
    assert r.status_code == 200 and r.json()["coverage"]["status"] == "incomplete"
    assert requests.get(f"{BASE}/api/ncs/{nc['_id']}/coverage", headers=i).status_code == 403
    d = requests.get(f"{BASE}/api/projects/{env['pid']}", headers=i).json()
    assert all("coverage" not in x and "scope" not in x for x in d["ncs"])
    s = requests.get(f"{BASE}/api/settings", headers=login("admin")).json()
    assert "coverage_check_enabled" in s
