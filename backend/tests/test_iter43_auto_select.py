"""Iter43: Auto Select = AI suggested (pending) only + AI call accounting (checklist draft + photo recs, limit 1).
In-process, mocked Gemini (recommend.ai_pick, checklist.ai_draft, checklist.match_profile). Synthetic records only; cleaned up; flags OFF."""
import sys

import pytest
from bson import ObjectId
from fastapi import HTTPException

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")
from _loop import LOOP  # noqa: E402

FLAGS = ("location_scope_enabled", "photo_classification_enabled", "coverage_check_enabled", "smart_pdf_enabled", "staff_review_enabled")
DRAFT = [{"title": "Gas tee before work", "stage": "before"}, {"title": "Tape showing clearance", "stage": "verification"},
         {"title": "Vent cover fitted", "stage": "after"}]


def run(c):
    return LOOP.run_until_complete(c)


@pytest.fixture(scope="module")
def env():
    import checklist
    import recommend
    from core import User, db, now_iso
    users = {}
    for role in ("admin", "employee", "installer"):
        d = {"email": f"qa.i43.{role}@example.com", "name": f"QA I43 {role}", "role": role, "active": True, "password_hash": "x", "token_version": 0, "created_at": now_iso()}
        d["_id"] = run(db.users.insert_one(d)).inserted_id
        users[role] = User.from_mongo(d)
    pid = str(run(db.projects.insert_one({"ref": "QA-I43", "address": "QA TEST 43 Example Road", "postcode": "QA1 1AA", "installer_id": users["installer"].id,
                                          "status": "active", "deleted": None, "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)

    def nc(stages):
        nid = str(run(db.ncs.insert_one({"project_id": pid, "number": "NC-I43", "description": "Gas tee clearance", "original_text": "Gas tee clearance",
                                         "required_work": "", "status": "employee_review", "safety_critical": False, "assigned_installer_id": users["installer"].id,
                                         "updated_at": now_iso()})).inserted_id)
        fids = []
        for k, st in enumerate(stages):
            fid = ObjectId()
            run(db.files.insert_one({"_id": fid, "project_id": pid, "nc_id": nid, "kind": "photo", "status": "submitted", "captured_at": f"2026-05-01 10:0{k}",
                                     "source": "in_app_camera", "sha256": str(fid), "size": 1, "mime": "image/jpeg", "uploaded_by": users["installer"].id,
                                     "server_received_at": f"2026-05-01T10:0{k}:00", "paths": {"stamped": f"qa-i43:{fid}"},
                                     "classification": {"stage": st, "location_id": None, "dup_of": None, "relevant": True}}))
            fids.append(str(fid))
        return nid, fids

    calls, drafts = [], []

    async def pick_last(nc_, items, pool, examples, docs):  # mocked AI: picks the last pool photo, uncertain
        calls.append([i["id"] for i in items])
        return {i["id"]: {"index": len(pool) - 1, "reason": "mock", "uncertain": True} for i in items}

    async def fake_draft(nc_, sc, guides, passages):
        drafts.append(1)
        return [dict(x) for x in DRAFT]

    async def no_match(*a, **k):
        return None
    orig = (recommend.ai_pick, checklist.ai_draft, checklist.match_profile)
    recommend.ai_pick, checklist.ai_draft, checklist.match_profile = pick_last, fake_draft, no_match
    run(db.settings.update_one({"_id": "global"}, {"$set": {k: True for k in FLAGS}}))
    yield {"db": db, "u": users, "nc": nc, "calls": calls, "drafts": drafts}
    recommend.ai_pick, checklist.ai_draft, checklist.match_profile = orig
    run(db.settings.update_one({"_id": "global"}, {"$set": {k: False for k in FLAGS}}))
    nids = [str(n["_id"]) for n in run(db.ncs.find({"project_id": pid}, {"_id": 1}).to_list(50))]
    run(db.learning_examples.delete_many({"nc_id": {"$in": nids}}))
    run(db.files.delete_many({"project_id": pid}))
    run(db.ncs.delete_many({"project_id": pid}))
    run(db.projects.delete_one({"_id": ObjectId(pid)}))
    run(db.users.delete_many({"_id": {"$in": [ObjectId(u.id) for u in users.values()]}}))


def confirm(nid, u, extra=None):
    import checklist
    items = [checklist.ItemIn(title=x["title"], stage=x["stage"]) for x in DRAFT + (extra or [])]
    return run(checklist.confirm(nid, checklist.ChecklistIn(items=items), u))


# Full sequence: draft (1 AI) -> stop -> confirm -> photo recs (<=1 AI) -> cached (0 AI)
def test_draft_confirm_then_recs_then_cache(env):
    import selection
    db, u = env["db"], env["u"]["employee"]
    nid, f = env["nc"](["before", "verification", "during", "during"])  # no 'after' photo -> AI needed for that requirement
    env["calls"].clear(); env["drafts"].clear()
    r = run(selection.auto_select(nid, selection.AutoIn(), u))
    assert r["needs_checklist_confirmation"] is True and r["ai_calls"] == 1 and r["ai_limit"] == 1 and r["ai_used"] is True
    assert env["drafts"] == [1] and env["calls"] == [] and r["suggested"] == [] and r["checklist_status"] == "draft"
    assert run(db.ncs.find_one({"_id": ObjectId(nid)})).get("evidence_selection") is None
    r1b = run(selection.auto_select(nid, selection.AutoIn(), u))  # pressed again before confirming: no second draft
    assert r1b["needs_checklist_confirmation"] and r1b["ai_calls"] == 0 and env["drafts"] == [1]
    confirm(nid, u)
    r2 = run(selection.auto_select(nid, selection.AutoIn(), u))
    assert not r2["needs_checklist_confirmation"] and r2["ai_calls"] == 1 and len(env["calls"]) == 1 and env["drafts"] == [1]
    assert all(x.get("checklist_item") is None for x in run(db.files.find({"nc_id": nid}).to_list(10)))  # nothing marked
    assert any("unsure" in n for n in r2["review_notes"]) and any("Measurement" in n for n in r2["review_notes"])
    assert len(r2["suggested"]) == 3
    r3 = run(selection.auto_select(nid, selection.AutoIn(mode="add"), u))
    assert r3["ai_calls"] == 0 and r3["ai_used"] is False and len(env["calls"]) == 1  # cached
    assert run(db.ncs.find_one({"_id": ObjectId(nid)}))["status"] == "employee_review"


# AI draft failure: 503, counted, nothing selected
def test_draft_failure_reports_count(env):
    import checklist
    import selection
    nid, _ = env["nc"](["before"])
    orig = checklist.ai_draft

    async def fail(*a):
        return None
    checklist.ai_draft = fail
    try:
        with pytest.raises(HTTPException) as e:
            run(selection.auto_select(nid, selection.AutoIn(), env["u"]["employee"]))
    finally:
        checklist.ai_draft = orig
    assert e.value.status_code == 503 and "1 AI call" in e.value.detail


# Reject / Replace after Auto Select; rejected not re-added by Add
def test_reject_replace_after_auto(env):
    import recommend
    import selection
    db, u = env["db"], env["u"]["employee"]
    nid, f = env["nc"](["before", "verification", "after", "during"])
    confirm(nid, u)
    env["calls"].clear()
    r = run(selection.auto_select(nid, selection.AutoIn(), u))
    assert r["ai_calls"] == 0 and env["calls"] == [] and r["selection"]["ids"] == f[:3]  # rules only
    ids = {i["title"]: i["item_id"] for i in run(recommend.view(nid))["items"]}
    v = run(recommend.act(nid, ids["Vent cover fitted"], recommend.ActIn(action="reject"), u))
    st = {i["title"]: i["state"] for i in v["items"]}
    assert st == {"Gas tee before work": "pending", "Tape showing clearance": "pending", "Vent cover fitted": "rejected"}
    v = run(recommend.act(nid, ids["Tape showing clearance"], recommend.ActIn(action="replace", file_id=f[3]), u))
    tape = next(i for i in v["items"] if i["title"] == "Tape showing clearance")
    assert tape["state"] == "replaced" and tape["file_id"] == f[3] and tape["source"] == "staff"
    r2 = run(selection.auto_select(nid, selection.AutoIn(mode="replace"), u))
    assert f[2] not in r2["selection"]["ids"] and r2["ai_calls"] <= 1 and "Vent cover fitted" in r2["missing"]


# Deselected AI pick must stay rejected even after the recommendation cache is rebuilt (e.g., checklist re-confirmed)
def test_deselect_ai_survives_cache_rebuild(env):
    import recommend
    import selection
    db, u = env["db"], env["u"]["employee"]
    nid, f = env["nc"](["before", "verification", "after"])
    confirm(nid, u)
    r = run(selection.auto_select(nid, selection.AutoIn(), u))
    assert r["selection"]["ids"] == f
    run(selection.save_selection(nid, selection.SelectionIn(ids=[f[0], f[1]], seq=r["selection"]["seq"] + 1), u))
    le = run(db.learning_examples.find_one({"nc_id": nid, "action": "deselect_ai"}))
    after_id = next(i["item_id"] for i in run(recommend.view(nid))["items"] if i["title"] == "Vent cover fitted")
    assert le and le["item_id"] == after_id
    assert next(i for i in run(recommend.view(nid))["items"] if i["item_id"] == after_id)["state"] == "rejected"
    r2 = run(selection.auto_select(nid, selection.AutoIn(mode="add"), u))
    assert f[2] not in r2["selection"]["ids"]  # cached: not re-suggested
    confirm(nid, u)  # staff re-confirm checklist (v bump) -> cache rebuilt
    r3 = run(selection.auto_select(nid, selection.AutoIn(mode="add"), u))
    assert f[2] not in r3["selection"]["ids"], "deselected AI pick re-suggested after cache rebuild"
