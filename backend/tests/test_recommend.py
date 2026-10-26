"""Knowledge Index Phase 3 - photo recommendations from approved profiles. Synthetic records, AI + storage mocked, no Dropbox, no real AI.
Switch flags restored OFF; all QA records removed."""
import sys

import pytest
from bson import ObjectId
from fastapi import HTTPException

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")
from _loop import LOOP  # noqa: E402

FLAGS = ("location_scope_enabled", "photo_classification_enabled", "coverage_check_enabled", "smart_pdf_enabled", "staff_review_enabled")
GAS = "There are non-compliances regarding a minimum of 250mm clearance above the gas pipe entry tee at the side elevation."
ITEMS = [{"id": "i-before", "title": "Gas tee before work", "stage": "before", "locations": [], "measurement": "", "criterion": "", "sources": []},
         {"id": "i-tape", "title": "Tape showing 250mm above tee", "stage": "verification", "locations": [], "measurement": "250mm", "criterion": "", "sources": []},
         {"id": "i-after", "title": "Vent cover fitted", "stage": "after", "locations": [], "measurement": "", "criterion": "", "sources": []}]


def run(c):
    return LOOP.run_until_complete(c)


@pytest.fixture(scope="module")
def env():
    import recommend
    from core import User, db, now_iso
    users = {}
    for role in ("admin", "employee", "installer"):
        d = {"email": f"qa.recs.{role}@example.com", "name": f"QA RECS {role}", "role": role, "active": True, "password_hash": "x", "token_version": 0, "created_at": now_iso()}
        d["_id"] = run(db.users.insert_one(d)).inserted_id
        users[role] = User.from_mongo(d)
    pid = str(run(db.projects.insert_one({"ref": "QA-RECS", "address": "QA TEST 3 Example Road", "postcode": "QA1 1AA", "installer_id": users["installer"].id,
                                          "status": "active", "deleted": None, "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)
    ex = str(run(db.files.insert_one({"project_id": pid, "nc_id": "qa-example", "kind": "photo", "status": "submitted", "paths": {"display": "qa-mem:ex"}})).inserted_id)
    prof_id = run(db.nc_profiles.insert_one({"key": "qarecs", "version": 1, "status": "approved", "name": "QA gas tee", "items": ITEMS,
                                             "signals": {"component": ["gas pipe"]}, "examples": {"i-tape": [ex]}, "created_by": users["admin"].id})).inserted_id

    def nc(stages, status="confirmed"):
        nid = str(run(db.ncs.insert_one({"project_id": pid, "number": "NC-R", "description": GAS, "original_text": GAS, "required_work": "", "status": "employee_review",
                                         "safety_critical": False, "assigned_installer_id": users["installer"].id, "updated_at": now_iso(),
                                         "checklist": {"v": 1, "status": status, "source": "profile", "profile": {"key": "qarecs", "version": 1, "id": str(prof_id)},
                                                       "items": ITEMS, "history": []}})).inserted_id)
        fids = [str(run(db.files.insert_one({"project_id": pid, "nc_id": nid, "kind": "photo", "status": "submitted", "paths": {"display": f"qa-mem:{k}"},
                                             "server_received_at": f"2026-05-01T09:0{k}:00",
                                             "classification": {"stage": st, "location_id": None, "dup_of": None, "relevant": True}})).inserted_id)
                for k, st in enumerate(stages)]
        return nid, fids
    calls = []

    async def fake_ai(nc_, items, pool, examples, docs):
        calls.append(([i["id"] for i in items], len(examples)))
        return {i["id"]: {"index": 0, "reason": "Shows tape against the tee", "uncertain": True} for i in items}

    async def fake_get(path):
        return b"jpeg", "image/jpeg"
    orig = (recommend.ai_pick, recommend.storage.aget)
    recommend.ai_pick, recommend.storage.aget = fake_ai, fake_get
    run(db.settings.update_one({"_id": "global"}, {"$set": {k: True for k in FLAGS}}))
    yield {"db": db, "u": users, "nc": nc, "calls": calls, "prof_id": prof_id, "ex": ex}
    recommend.ai_pick, recommend.storage.aget = orig
    run(db.settings.update_one({"_id": "global"}, {"$set": {k: False for k in FLAGS}}))
    run(db.files.delete_many({"project_id": pid}))
    run(db.learning_examples.delete_many({"by": {"$in": [u.id for u in users.values()]}}))
    run(db.ncs.delete_many({"project_id": pid}))
    run(db.nc_profiles.delete_many({"key": "qarecs"}))
    run(db.projects.delete_one({"_id": ObjectId(pid)}))
    run(db.users.delete_many({"_id": {"$in": [ObjectId(u.id) for u in users.values()]}}))


def items(v):
    return {i["item_id"]: i for i in v["items"]}


def test_needs_confirmed_checklist_and_switch(env):
    import recommend
    from core import db
    nid, _ = env["nc"](["before"], status="draft")
    with pytest.raises(HTTPException) as e:
        run(recommend.run_recs(nid, recommend.RunIn(), env["u"]["employee"]))
    assert e.value.status_code == 400
    run(db.settings.update_one({"_id": "global"}, {"$set": {"staff_review_enabled": False}}))
    with pytest.raises(HTTPException) as e:
        run(recommend.get_recs(nid, env["u"]["employee"]))
    assert e.value.status_code == 409
    run(db.settings.update_one({"_id": "global"}, {"$set": {"staff_review_enabled": True}}))


def test_rules_first_one_ai_call_cached(env):
    import recommend
    nid, f = env["nc"](["before", "verification", "verification", "during"])
    env["calls"].clear()
    v = items(run(recommend.run_recs(nid, recommend.RunIn(), env["u"]["employee"])))
    assert v["i-before"]["source"] == "rule" and v["i-before"]["file_id"] == f[0]  # unique stage -> no AI
    assert v["i-tape"]["source"] == "ai" and v["i-after"]["source"] == "ai"  # ambiguous / no stage -> AI
    assert env["calls"] == [(["i-tape", "i-after"], 1)]  # ONE call, approved example included
    assert any("Measurement" in x for x in v["i-tape"]["flags"]) and any("unsure" in x for x in v["i-tape"]["flags"])
    assert any("No approved example" in x for x in v["i-before"]["flags"])
    run(recommend.run_recs(nid, recommend.RunIn(), env["u"]["employee"]))
    assert len(env["calls"]) == 1  # cached: same photos/checklist -> no AI
    run(recommend.run_recs(nid, recommend.RunIn(force=True), env["u"]["employee"]))
    assert len(env["calls"]) == 2
    env["nid"], env["f"] = nid, f


def test_staff_marking_wins_and_pending_changes_nothing(env):
    import checklist
    import recommend
    from core import db
    nid, f = env["nid"], env["f"]
    assert run(db.files.count_documents({"nc_id": nid, "checklist_item": {"$ne": None}})) == 0  # suggestions never mark photos
    run(checklist.mark(f[2], checklist.MarkIn(item_id="i-tape"), env["u"]["employee"]))
    v = items(run(recommend.get_recs(nid, env["u"]["employee"])))
    assert v["i-tape"]["source"] == "staff" and v["i-tape"]["file_id"] == f[2] and v["i-tape"]["flags"] == []
    env["calls"].clear()
    run(recommend.run_recs(nid, recommend.RunIn(force=True), env["u"]["employee"]))
    assert env["calls"] == [(["i-after"], 0)]  # staff-marked requirement never sent to AI


def test_accept_reject_replace_and_learning(env):
    import recommend
    from core import db
    nid, f, u = env["nid"], env["f"], env["u"]["employee"]
    v = items(run(recommend.act(nid, "i-before", recommend.ActIn(action="accept"), u)))
    assert v["i-before"]["state"] == "accepted" and run(db.files.find_one({"_id": ObjectId(f[0])}))["checklist_item"] == "i-before"
    sug = items(run(recommend.get_recs(nid, u)))["i-after"]["file_id"]
    v = items(run(recommend.act(nid, "i-after", recommend.ActIn(action="reject"), u)))
    assert v["i-after"]["state"] == "rejected" and v["i-after"]["file_id"] is None
    v = items(run(recommend.act(nid, "i-after", recommend.ActIn(action="replace", file_id=f[3]), u)))
    assert v["i-after"]["source"] == "staff" and run(db.files.find_one({"_id": ObjectId(f[3])}))["checklist_item"] == "i-after"
    with pytest.raises(HTTPException):
        run(recommend.act(nid, "i-after", recommend.ActIn(action="replace", file_id=str(ObjectId())), u))
    acts = [e["action"] for e in run(db.learning_examples.find({"nc_id": nid, "action": {"$regex": "_rec$"}}).to_list(20))]
    assert acts == ["accept_rec", "reject_rec", "replace_rec"]
    assert sug in run(recommend._rejected(nid))["i-after"]


def test_propose_profile_update_needs_admin(env):
    import checklist
    import recommend
    from core import db
    nid, f = env["nid"], env["f"]
    r = run(recommend.propose(nid, recommend.ProposeIn(), env["u"]["employee"]))
    p = run(db.nc_profiles.find_one({"_id": ObjectId(r["id"])}))
    assert p["status"] == "proposed" and p["version"] == 2 and p["key"] == "qarecs" and p["name"] == "QA gas tee"
    assert p["examples"]["i-tape"] == [f[2], env["ex"]] and p["examples"]["i-after"] == [f[3]] and p["bad_examples"]["i-after"]
    old = run(db.nc_profiles.find_one({"_id": env["prof_id"]}))
    assert old["status"] == "approved" and old["examples"] == {"i-tape": [env["ex"]]}  # untouched until approval
    nid2, _ = env["nc"](["before"])
    assert str(run(recommend._profile(run(db.ncs.find_one({"_id": ObjectId(nid2)}))))["_id"]) == str(env["prof_id"])
    run(checklist.profile_status(r["id"], checklist.ProfileStatusIn(status="approved"), env["u"]["admin"]))
    assert str(run(recommend._profile(run(db.ncs.find_one({"_id": ObjectId(nid2)}))))["_id"]) == r["id"]
    r2 = run(recommend.propose(nid, recommend.ProposeIn(), env["u"]["employee"]))
    r3 = run(recommend.propose(nid, recommend.ProposeIn(), env["u"]["employee"]))
    assert r2["id"] == r3["id"] and r2["version"] == 3  # repeat press updates the pending proposal


def test_repeat_propose_without_profile(env):
    import recommend
    from core import db
    nid, f = env["nc"](["before"])
    run(db.ncs.update_one({"_id": ObjectId(nid)}, {"$set": {"checklist.profile": None, "checklist.source": "ai"}}))
    run(db.nc_profiles.update_many({"key": "qarecs"}, {"$set": {"status": "retired"}}))
    run(db.files.update_one({"_id": ObjectId(f[0])}, {"$set": {"checklist_item": "i-before"}}))
    a = run(recommend.propose(nid, recommend.ProposeIn(name="QA new profile"), env["u"]["employee"]))
    b = run(recommend.propose(nid, recommend.ProposeIn(name="QA new profile"), env["u"]["employee"]))
    assert a["id"] == b["id"] and a["key"] == b["key"]
    run(db.nc_profiles.delete_many({"key": a["key"]}))
    run(db.nc_profiles.update_many({"key": "qarecs", "status": "retired"}, {"$set": {"status": "approved"}}))


def test_retag_invalidates_cache(env):
    import recommend
    from core import db
    nid, f = env["nc"](["before", "during"])
    run(recommend.run_recs(nid, recommend.RunIn(), env["u"]["employee"]))
    n = len(env["calls"])
    run(db.files.update_one({"_id": ObjectId(f[1])}, {"$set": {"classification.staff": {"stage": "after"}}}))
    v = items(run(recommend.run_recs(nid, recommend.RunIn(), env["u"]["employee"])))
    assert v["i-after"]["source"] == "rule" and v["i-after"]["file_id"] == f[1] and len(env["calls"]) == n + 1  # rebuilt; tape still needs AI


def test_pdf_uses_only_accepted(env):
    import checklist
    from core import db
    nc = run(db.ncs.find_one({"_id": ObjectId(env["nid"])}))
    photos = run(db.files.find({"nc_id": env["nid"]}).to_list(20))
    st = {c["id"]: c["state"] for c in checklist.coverage(nc["checklist"], photos)}
    assert st == {"i-before": "staff", "i-tape": "staff", "i-after": "staff"}


def test_ai_unavailable(env):
    import recommend
    nid, _ = env["nc"](["during", "during"])

    async def down(*a):
        return None
    keep = recommend.ai_pick
    recommend.ai_pick = down
    v = items(run(recommend.run_recs(nid, recommend.RunIn(), env["u"]["employee"])))
    recommend.ai_pick = keep
    assert all(i["file_id"] is None for i in v.values()) and "AI unavailable" in v["i-tape"]["reason"]
