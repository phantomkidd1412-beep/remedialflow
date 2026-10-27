"""Explicit evidence selection: save semantics, PDF selection/order, Auto Select (mocked AI), learning, compatibility.
Synthetic records + in-memory images; no Dropbox, no real AI. Flags restored OFF; all QA records removed."""
import io
import random
import sys

import pytest
from bson import ObjectId
from fastapi import HTTPException
from PIL import Image

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")
from _loop import LOOP  # noqa: E402

FLAGS = ("location_scope_enabled", "photo_classification_enabled", "coverage_check_enabled", "smart_pdf_enabled", "staff_review_enabled")
BLOB = {}
ITEMS = [{"id": "s-before", "title": "Gas tee before work", "stage": "before", "locations": [], "measurement": "", "criterion": "", "sources": []},
         {"id": "s-tape", "title": "Tape showing 250mm", "stage": "verification", "locations": [], "measurement": "250mm", "criterion": "", "sources": []},
         {"id": "s-after", "title": "Vent cover fitted", "stage": "after", "locations": [], "measurement": "", "criterion": "", "sources": []}]


def run(c):
    return LOOP.run_until_complete(c)


def img(seed):
    r = random.Random(seed)
    b = io.BytesIO()
    Image.new("RGB", (320, 240), (r.randint(0, 255), r.randint(0, 255), r.randint(0, 255))).save(b, "JPEG")
    return b.getvalue()


@pytest.fixture(scope="module")
def env():
    import recommend
    from core import User, db, now_iso
    users = {}
    for role in ("admin", "employee", "installer"):
        d = {"email": f"qa.sel.{role}@example.com", "name": f"QA SEL {role}", "role": role, "active": True, "password_hash": "x", "token_version": 0, "created_at": now_iso()}
        d["_id"] = run(db.users.insert_one(d)).inserted_id
        users[role] = User.from_mongo(d)
    pid = str(run(db.projects.insert_one({"ref": "QA-SEL", "address": "QA TEST 5 Example Road", "postcode": "QA1 1AA", "installer_id": users["installer"].id,
                                          "status": "active", "deleted": None, "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)

    def nc(stages, checklist=True):
        doc = {"project_id": pid, "number": "NC-S", "description": "Gas tee clearance", "original_text": "Gas tee clearance 250mm", "required_work": "",
               "status": "employee_review", "safety_critical": True, "assigned_installer_id": users["installer"].id, "updated_at": now_iso()}
        if checklist:
            doc["checklist"] = {"v": 1, "status": "confirmed", "source": "staff", "profile": None, "items": ITEMS, "history": []}
        nid = str(run(db.ncs.insert_one(doc)).inserted_id)
        fids = []
        for k, st in enumerate(stages):
            fid = ObjectId()
            BLOB[f"qa-sel:{fid}"] = img(k + len(BLOB))
            run(db.files.insert_one({"_id": fid, "project_id": pid, "nc_id": nid, "kind": "photo", "status": "submitted", "captured_at": f"2026-05-01 10:0{k}",
                                     "source": "in_app_camera", "sha256": str(fid), "size": 1, "mime": "image/jpeg", "uploaded_by": users["installer"].id,
                                     "server_received_at": f"2026-05-01T10:0{k}:00", "paths": {"stamped": f"qa-sel:{fid}"},
                                     "classification": {"stage": st, "location_id": None, "dup_of": None, "relevant": True}}))
            fids.append(str(fid))
        return nid, fids

    calls = []

    async def no_pick(nc_, items, pool, examples, docs):
        calls.append([i["id"] for i in items])
        return {i["id"]: {"index": None, "reason": "", "uncertain": True} for i in items}  # AI finds nothing suitable
    orig = recommend.ai_pick
    recommend.ai_pick = no_pick
    run(db.settings.update_one({"_id": "global"}, {"$set": {k: True for k in FLAGS}}))
    yield {"db": db, "u": users, "nc": nc, "pid": pid, "calls": calls}
    recommend.ai_pick = orig
    run(db.settings.update_one({"_id": "global"}, {"$set": {k: False for k in FLAGS}}))
    nids = [str(n["_id"]) for n in run(db.ncs.find({"project_id": pid}, {"_id": 1}).to_list(50))]
    run(db.learning_examples.delete_many({"nc_id": {"$in": nids}}))
    run(db.files.delete_many({"project_id": pid}))
    run(db.ncs.delete_many({"project_id": pid}))
    run(db.projects.delete_one({"_id": ObjectId(pid)}))
    run(db.users.delete_many({"_id": {"$in": [ObjectId(u.id) for u in users.values()]}}))


async def get(f, *variants):
    return next((BLOB[f["paths"][v]] for v in variants if f.get("paths", {}).get(v) in BLOB), None)


def groups(db, nid):
    import server
    n = run(db.ncs.find_one({"_id": ObjectId(nid)}))
    return run(server.pdf_photo_groups(n, run(db.files.find({"nc_id": nid}).to_list(50)), get))


def test_save_order_empty_stale_and_installer(env):
    import selection
    import server
    db, u = env["db"], env["u"]["employee"]
    nid, f = env["nc"](["before", "after", "during"])
    assert run(db.ncs.find_one({"_id": ObjectId(nid)})).get("evidence_selection") is None  # never selected
    s = run(selection.save_selection(nid, selection.SelectionIn(ids=[f[2], f[0], f[0], "bogus"], seq=5), u))
    assert s["ids"] == [f[2], f[0]]
    assert run(selection.save_selection(nid, selection.SelectionIn(ids=[f[1]], seq=3), u))["stale"]  # late older save ignored
    assert run(db.ncs.find_one({"_id": ObjectId(nid)}))["evidence_selection"]["ids"] == [f[2], f[0]]
    run(selection.save_selection(nid, selection.SelectionIn(ids=[], seq=6), u))
    assert run(db.ncs.find_one({"_id": ObjectId(nid)}))["evidence_selection"]["ids"] == []  # deliberately empty, kept
    proj = run(server.project_detail(env["pid"], env["u"]["installer"]))
    assert all("evidence_selection" not in n for n in proj["ncs"])


def test_pdf_legacy_empty_and_exact_order(env):
    import selection
    db, u = env["db"], env["u"]["employee"]
    nid, f = env["nc"](["before", "after", "during", "verification"], checklist=False)
    run(db.settings.update_one({"_id": "global"}, {"$set": {k: False for k in FLAGS}}))
    run(db.files.update_many({"nc_id": nid}, {"$set": {"pdf_include": True, "stage": "pre", "stage_source": "staff"}}))
    legacy = groups(db, nid)
    assert legacy and legacy[0][0] == "PRE-REMEDIATION PHOTOGRAPH"  # no selection: existing behaviour untouched
    run(db.files.update_many({"nc_id": nid}, {"$set": {"pdf_include": None, "stage": None, "stage_source": None}}))
    run(selection.save_selection(nid, selection.SelectionIn(ids=[], seq=1), u))
    assert groups(db, nid) == []  # explicit empty: never falls back to all photos
    run(selection.save_selection(nid, selection.SelectionIn(ids=[f[3], f[0], f[1]], seq=2), u))
    g = groups(db, nid)
    shown = [b for _, slots in g for b, _ in slots]
    assert shown == [BLOB[f"qa-sel:{f[3]}"], BLOB[f"qa-sel:{f[0]}"], BLOB[f"qa-sel:{f[1]}"]]  # exact staff order, stamped copies
    assert [h for h, _ in g] == ["MEASUREMENT / TEST EVIDENCE", "BEFORE REMEDIATION", "COMPLETED WORK"]
    run(db.settings.update_one({"_id": "global"}, {"$set": {k: True for k in FLAGS}}))


def test_auto_select_cached_no_ai_and_learning(env):
    import recommend
    import selection
    db, u = env["db"], env["u"]["employee"]
    nid, f = env["nc"](["before", "after", "during"])
    env["calls"].clear()
    r = run(selection.auto_select(nid, selection.AutoIn(mode="replace"), u))
    assert r["selection"]["ids"] == [f[0], f[1]] and r["missing"] == ["Tape showing 250mm"]
    assert env["calls"] == [["s-tape"]]  # rules settled the rest; one AI call only for the unsettled requirement
    assert r["ai_calls"] == 1 and r["ai_limit"] == 1 and not r["needs_checklist_confirmation"]
    assert any("Safety-critical" in x for x in r["review_notes"]) and any("Measurement" in x for x in r["review_notes"])
    marks = {str(x["_id"]): x.get("checklist_item") for x in run(db.files.find({"nc_id": nid}).to_list(10))}
    assert marks[f[0]] is None and marks[f[1]] is None  # Auto Select never marks: picks stay AI suggested
    v = run(recommend.view(nid))
    assert {i["item_id"]: i["state"] for i in v["items"]} == {"s-before": "pending", "s-tape": "pending", "s-after": "pending"}
    assert sorted(r["suggested"]) == ["Gas tee before work", "Vent cover fitted"]
    assert run(db.ncs.find_one({"_id": ObjectId(nid)}))["status"] == "employee_review"  # never approves
    # staff reorder + swap: remove AI pick f[1], add f[2]
    run(selection.save_selection(nid, selection.SelectionIn(ids=[f[2], f[0]], seq=r["selection"]["seq"] + 1), u))
    acts = sorted((e["action"], e.get("item_id")) for e in run(db.learning_examples.find({"nc_id": nid, "source": "evidence_selection"}).to_list(10)))
    assert acts == [("deselect_ai", "s-after"), ("select_staff", None)]
    assert run(db.files.find_one({"_id": ObjectId(f[1])})).get("checklist_item") is None  # deselected -> no longer evidence
    r2 = run(selection.auto_select(nid, selection.AutoIn(mode="add"), u))
    assert r2["selection"]["ids"] == [f[2], f[0]] and len(env["calls"]) == 1  # removed AI pick not re-added; cached, no new AI call
    assert r2["ai_calls"] == 0


def test_auto_pick_needs_staff_accept_and_keeps_warnings(env):
    import recommend
    import selection
    db, u = env["db"], env["u"]["employee"]
    nid, f = env["nc"](["before", "verification", "after"])
    r = run(selection.auto_select(nid, selection.AutoIn(), u))
    assert set(r["selection"]["ids"]) == set(f)
    tape = next(i for i in run(recommend.view(nid))["items"] if i["item_id"] == "s-tape")
    assert tape["state"] == "pending" and tape["source"] in ("rule", "ai") and any("Measurement" in x for x in tape["flags"])
    assert any(x.startswith("Tape showing 250mm: Measurement") for x in r["review_notes"])
    v = run(recommend.act(nid, "s-before", recommend.ActIn(action="accept"), u))  # only explicit staff confirmation accepts
    st = {i["item_id"]: (i["state"], i["source"]) for i in v["items"]}
    assert st["s-before"] == ("accepted", "staff") and st["s-tape"][0] == "pending" and st["s-after"][0] == "pending"
    assert run(db.files.find_one({"_id": ObjectId(f[0])}))["checklist_item"] == "s-before"
    assert run(db.ncs.find_one({"_id": ObjectId(nid)}))["status"] == "employee_review"


def test_auto_select_draft_checklist_stops_for_confirmation(env):
    import checklist
    import recommend
    import selection
    db, u = env["db"], env["u"]["employee"]
    nid, f = env["nc"](["before", "after"], checklist=False)
    env["calls"].clear()
    drafts = []

    async def fake_draft(nc_, sc, guides, passages):
        drafts.append(1)
        return [{"title": "Gas tee before work", "stage": "before"}, {"title": "Tape showing clearance", "stage": "verification"}, {"title": "Vent cover fitted", "stage": "after"}]
    orig, orig_m = checklist.ai_draft, checklist.match_profile
    checklist.ai_draft = fake_draft

    async def no_match(*a, **k):
        return None
    checklist.match_profile = no_match
    try:
        r = run(selection.auto_select(nid, selection.AutoIn(), u))
    finally:
        checklist.ai_draft, checklist.match_profile = orig, orig_m
    assert r["needs_checklist_confirmation"] and r["ai_calls"] == 1 and r["ai_limit"] == 1
    assert drafts == [1] and env["calls"] == []  # checklist drafted, no second (photo) AI call
    assert run(db.ncs.find_one({"_id": ObjectId(nid)})).get("evidence_selection") is None  # nothing selected yet
    # limit enforcement in the recommender itself: budget 0 never calls AI
    run(db.ncs.update_one({"_id": ObjectId(nid)}, {"$set": {"checklist.status": "confirmed"}}))
    recs = run(recommend.build(nid, ai_budget=0))
    assert recs["ai_calls"] == 0 and env["calls"] == []
    assert any(i["reason"] == "AI limit reached; choose a photo manually" for i in recs["items"].values())


def test_auto_select_needs_switch(env):
    import selection
    db = env["db"]
    nid, _ = env["nc"](["before"])
    run(db.settings.update_one({"_id": "global"}, {"$set": {"staff_review_enabled": False}}))
    with pytest.raises(HTTPException) as e:
        run(selection.auto_select(nid, selection.AutoIn(), env["u"]["employee"]))
    assert e.value.status_code == 409
    run(db.settings.update_one({"_id": "global"}, {"$set": {"staff_review_enabled": True}}))


def test_removed_ai_photo_never_returns_but_manual_reselect_works(env):
    import recommend
    import selection
    db, u = env["db"], env["u"]["employee"]
    nid, f = env["nc"](["before", "verification", "after"])
    r = run(selection.auto_select(nid, selection.AutoIn(), u))
    assert set(r["selection"]["ids"]) == set(f)
    s = run(selection.save_selection(nid, selection.SelectionIn(ids=[f[0], f[2]], seq=r["selection"]["seq"] + 1), u))  # staff remove AI pick f[1]
    assert s["removed"] == [f[1]]
    # checklist change (new version) rebuilds the cache: the removed photo must not come back
    run(db.ncs.update_one({"_id": ObjectId(nid)}, {"$inc": {"checklist.v": 1}}))
    for mode in ("add", "replace"):
        r = run(selection.auto_select(nid, selection.AutoIn(mode=mode), u))
        assert f[1] not in r["selection"]["ids"]
    tape = next(i for i in run(recommend.view(nid))["items"] if i["item_id"] == "s-tape")
    assert tape["file_id"] != f[1]
    s = run(selection.save_selection(nid, selection.SelectionIn(ids=[f[0], f[1], f[2]], seq=r["selection"]["seq"] + 1), u))  # manual reselect
    assert s["ids"] == [f[0], f[1], f[2]] and f[1] not in s["removed"]


def test_reject_and_replace_sync_selection_and_pdf(env):
    import recommend
    import selection
    db, u = env["db"], env["u"]["employee"]
    nid, f = env["nc"](["before", "verification", "after", "during"])
    r = run(selection.auto_select(nid, selection.AutoIn(), u))
    run(selection.save_selection(nid, selection.SelectionIn(ids=[f[2], f[1], f[0]], seq=r["selection"]["seq"] + 1), u))  # drag order
    # Replace the "after" suggestion (f[2], position 1) with f[3]: replacement takes its place, never "accepted"
    v = run(recommend.act(nid, "s-after", recommend.ActIn(action="replace", file_id=f[3]), u))
    sel = run(db.ncs.find_one({"_id": ObjectId(nid)}))["evidence_selection"]
    assert sel["ids"] == [f[3], f[1], f[0]] and f[2] in sel["removed"]
    after = next(i for i in v["items"] if i["item_id"] == "s-after")
    assert after["state"] == "replaced" and after["state"] != "accepted" and any("confirm" in x for x in after["flags"])
    # Reject the measurement suggestion f[1]: leaves the PDF selection at once
    run(recommend.act(nid, "s-tape", recommend.ActIn(action="reject"), u))
    sel = run(db.ncs.find_one({"_id": ObjectId(nid)}))["evidence_selection"]
    assert sel["ids"] == [f[3], f[0]] and f[1] in sel["removed"]
    shown = [b for _, slots in groups(db, nid) for b, _ in slots]
    assert shown == [BLOB[f"qa-sel:{f[3]}"], BLOB[f"qa-sel:{f[0]}"]]  # final saved selection and order; rejected photos absent
    r = run(selection.auto_select(nid, selection.AutoIn(mode="add"), u))
    assert f[1] not in r["selection"]["ids"] and f[2] not in r["selection"]["ids"]
    # staff confirm the replacement -> accepted (only by explicit confirmation)
    v = run(recommend.act(nid, "s-after", recommend.ActIn(action="accept"), u))
    after = next(i for i in v["items"] if i["item_id"] == "s-after")
    assert after["state"] == "accepted" and after["reason"] == "Replacement confirmed by staff"


def test_legacy_pdf_excludes_rejected_photo(env):
    import recommend
    db, u = env["db"], env["u"]["employee"]
    nid, f = env["nc"](["before", "after"])
    run(db.files.update_many({"nc_id": nid}, {"$set": {"pdf_include": True}}))
    run(db.files.update_one({"_id": ObjectId(f[1])}, {"$set": {"pdf_include": None}}))
    run(recommend.build(nid))
    run(recommend.act(nid, "s-after", recommend.ActIn(action="reject"), u))
    assert run(db.ncs.find_one({"_id": ObjectId(nid)})).get("evidence_selection") is None  # legacy NC: no selection created
    shown = [b for _, slots in groups(db, nid) for b in (slots if isinstance(slots[0], bytes) else [x for x, _ in slots])]
    assert BLOB[f"qa-sel:{f[1]}"] not in shown


def test_hand_ticked_suggestion_removed_is_remembered(env):
    import recommend
    import selection
    db, u = env["db"], env["u"]["employee"]
    nid, f = env["nc"](["before", "verification", "after"])
    run(recommend.build(nid))  # suggestions exist, nothing selected yet
    run(selection.save_selection(nid, selection.SelectionIn(ids=[f[0], f[2]], seq=1), u))  # staff tick suggestions by hand
    s = run(selection.save_selection(nid, selection.SelectionIn(ids=[f[0]], seq=2), u))  # then untick f[2]
    assert s["removed"] == [f[2]]
    acts = [e["action"] for e in run(db.learning_examples.find({"nc_id": nid, "rejected_file_id": f[2]}).to_list(5))]
    assert acts == ["deselect_ai"]
    r = run(selection.auto_select(nid, selection.AutoIn(mode="add"), u))
    assert f[2] not in r["selection"]["ids"] and "Vent cover fitted" in r["missing"]
    # replace with the same position semantics: same photo kept in place
    run(selection.save_selection(nid, selection.SelectionIn(ids=[f[1], f[0]], seq=r["selection"]["seq"] + 1), u))
    run(recommend.act(nid, "s-before", recommend.ActIn(action="replace", file_id=f[0]), u))
    assert run(db.ncs.find_one({"_id": ObjectId(nid)}))["evidence_selection"]["ids"] == [f[1], f[0]]


def test_photo_shared_by_two_requirements_is_not_dropped(env):
    import recommend
    import selection
    db, u = env["db"], env["u"]["employee"]
    nid, f = env["nc"](["before", "verification", "after"])
    r = run(selection.auto_select(nid, selection.AutoIn(), u))  # f[2] = pending suggestion for s-after
    run(recommend.act(nid, "s-before", recommend.ActIn(action="replace", file_id=f[2]), u))  # staff use f[2] for s-before
    after = next(i for i in run(recommend.view(nid))["items"] if i["item_id"] == "s-after")
    assert after["file_id"] is None  # the other pending suggestion of that photo is cleared
    sel = run(db.ncs.find_one({"_id": ObjectId(nid)}))["evidence_selection"]
    assert f[2] in sel["ids"] and f[2] not in sel["removed"]
    # rejecting a suggestion whose photo is still staff evidence elsewhere keeps it selected
    run(db.ncs.update_one({"_id": ObjectId(nid)}, {"$set": {"photo_recs.items.s-tape.file_id": f[2], "photo_recs.items.s-tape.state": "pending", "photo_recs.items.s-tape.source": "ai"}}))
    run(recommend.act(nid, "s-tape", recommend.ActIn(action="reject"), u))
    sel = run(db.ncs.find_one({"_id": ObjectId(nid)}))["evidence_selection"]
    assert f[2] in sel["ids"] and f[2] not in sel["removed"]
    assert run(db.files.find_one({"_id": ObjectId(f[2])}))["checklist_item"] == "s-before"
