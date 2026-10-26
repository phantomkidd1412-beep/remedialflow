"""Knowledge Index Phase 1 - NC checklist, staff best-photo marking, learning examples, profiles, checklist-driven PDF.
Synthetic QA data, AI draft mocked, in-memory images, no Dropbox. Switch flags restored OFF."""
import io
import random
import sys

import pytest
from bson import ObjectId
from fastapi import HTTPException
from PIL import Image, ImageDraw

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")
from _loop import LOOP  # noqa: E402

GAS = "There are non-compliances regarding a minimum of 250mm clearance above the gas pipe entry tee at the side elevation."
GAS2 = "Clearance of 250mm not achieved above the gas entry tee, rear elevation."
FLAGS = ("location_scope_enabled", "photo_classification_enabled", "coverage_check_enabled", "smart_pdf_enabled", "staff_review_enabled")
DRAFT = [{"title": "Gas tee and clearance before work", "stage": "before"}, {"title": "Tape showing 250mm above tee", "stage": "verification", "measurement": ">=250mm"},
         {"title": "Vent cover fitted", "stage": "after"}, {"title": "Second measurement at flue", "stage": "verification"}]


def run(c):
    return LOOP.run_until_complete(c)


def img(seed):
    r = random.Random(seed)
    im = Image.new("RGB", (320, 240), (r.randint(0, 255), 90, 90))
    d = ImageDraw.Draw(im)
    for _ in range(8):
        x, y = r.randint(0, 280), r.randint(0, 200)
        d.rectangle([x, y, x + 40, y + 30], fill=(r.randint(0, 255), r.randint(0, 255), 0))
    b = io.BytesIO()
    im.save(b, "JPEG")
    return b.getvalue()


@pytest.fixture(scope="module")
def env():
    import checklist
    from core import User, db, now_iso
    users = {}
    for role in ("admin", "employee", "installer"):
        d = {"email": f"qa.kb.{role}@example.com", "name": f"QA KB {role}", "role": role, "active": True, "password_hash": "x", "token_version": 0, "created_at": now_iso()}
        d["_id"] = run(db.users.insert_one(d)).inserted_id
        users[role] = User.from_mongo(d)
    pid = str(run(db.projects.insert_one({"ref": "QA-KB", "address": "QA TEST 1 Example Road", "postcode": "QA1 1AA", "installer_id": users["installer"].id,
                                          "status": "active", "deleted": None, "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)

    def nc(text, number):
        nid = str(run(db.ncs.insert_one({"project_id": pid, "number": number, "description": text, "original_text": text, "status": "employee_review",
                                         "required_work": "", "safety_critical": False,
                                         "assigned_installer_id": users["installer"].id, "updated_at": now_iso()})).inserted_id)
        stages = ["before", "before", "verification", "verification", "after", "after"]
        fids = [str(run(db.files.insert_one({"project_id": pid, "nc_id": nid, "kind": "photo", "status": "submitted", "seed": k,
                                             "paths": {"stamped": f"qa-mem:{k}"}, "server_received_at": f"2026-05-01T09:0{k}:00",
                                             "classification": {"stage": st, "location_id": None, "dup_of": None, "relevant": True}})).inserted_id)
                for k, st in enumerate(stages)]
        return nid, fids
    run(db.settings.update_one({"_id": "global"}, {"$set": {k: True for k in FLAGS}}))
    calls = []

    async def fake_draft(nc_, scope, guides):
        calls.append(nc_["number"])
        return DRAFT
    orig = checklist.ai_draft
    checklist.ai_draft = fake_draft
    yield {"db": db, "u": users, "pid": pid, "nc": nc, "calls": calls}
    checklist.ai_draft = orig
    run(db.settings.update_one({"_id": "global"}, {"$set": {k: False for k in FLAGS}}))
    for c in ("files", "ncs"):
        run(db[c].delete_many({"project_id": pid}))
    run(db.learning_examples.delete_many({"by": {"$in": [u.id for u in users.values()]}}))
    run(db.nc_profiles.delete_many({"created_by": {"$in": [u.id for u in users.values()]}}))
    run(db.projects.delete_one({"_id": ObjectId(pid)}))
    run(db.users.delete_many({"_id": {"$in": [ObjectId(u.id) for u in users.values()]}}))


def test_signals_and_rating_need_component():
    from checklist import rate, signals
    a = signals({"original_text": GAS}, {"scope_type": "elevation"})
    assert "gas pipe" in a["component"] and "gas" in a["measure"] and "250mm" in a["requirement"]
    assert rate(a, signals({"original_text": GAS2}, {"scope_type": "elevation"}))[0] == "confident"
    assert rate(a, signals({"original_text": "Clearance of 250mm missing above the window sill."}, {"scope_type": "window"}))[0] == "none"


def test_flow_draft_mark_confirm_profile(env):
    import checklist
    db, u = env["db"], env["u"]
    nid, fids = env["nc"](GAS, "NC-01")
    v = run(checklist.draft(nid, checklist.DraftIn(), u["employee"]))
    assert v["checklist"]["status"] == "draft" and v["checklist"]["source"] == "ai" and len(v["checklist"]["items"]) == 4 and env["calls"] == ["NC-01"]
    assert run(checklist.gate_reasons(nid)) == []  # an unconfirmed AI draft never judges the NC
    run(checklist.draft(nid, checklist.DraftIn(), u["employee"]))
    assert env["calls"] == ["NC-01"]  # existing checklist reused, no second AI call
    items = v["checklist"]["items"]
    meas1, meas2 = items[1]["id"], items[3]["id"]
    with pytest.raises(HTTPException):
        run(checklist.mark(fids[0], checklist.MarkIn(item_id="nope"), u["employee"]))
    run(checklist.mark(fids[2], checklist.MarkIn(item_id=meas1), u["employee"]))
    v = run(checklist.mark(fids[3], checklist.MarkIn(item_id=meas2), u["employee"]))
    st = {c["id"]: c["state"] for c in v["coverage"]}
    assert st[meas1] == st[meas2] == "staff" and st[items[0]["id"]] == "ai"
    assert run(db.learning_examples.count_documents({"nc_id": nid})) == 2
    f = run(db.files.find_one({"_id": ObjectId(fids[2])}))
    assert f["status"] == "submitted" and f["paths"] == {"stamped": "qa-mem:2"}  # photo untouched, only a reference stored
    with pytest.raises(HTTPException):  # not confirmed yet
        run(checklist.save_profile(nid, checklist.ProfileIn(name="Gas entry tee clearance"), u["employee"]))
    v = run(checklist.confirm(nid, checklist.ChecklistIn(items=[checklist.ItemIn(**i) for i in items]), u["employee"]))
    assert v["checklist"]["status"] == "confirmed"
    assert run(checklist.gate_reasons(nid)) == []  # every item has evidence
    p = run(checklist.save_profile(nid, checklist.ProfileIn(name="Gas entry tee clearance"), u["employee"]))
    assert p["version"] == 1 and p["status"] == "proposed"
    env["profile"] = p


def test_profile_needs_approval_then_prefills(env):
    import checklist
    db, u = env["db"], env["u"]
    nid2, _ = env["nc"](GAS2, "NC-02")
    run(checklist.ensure_checklist(nid2))
    assert run(db.ncs.find_one({"_id": ObjectId(nid2)}))["checklist"]["source"] == "ai"  # proposed profile is not reused
    run(checklist.profile_status(env["profile"]["id"], checklist.ProfileStatusIn(status="approved"), u["admin"]))
    nid3, fids3 = env["nc"](GAS2, "NC-03")
    n = len(env["calls"])
    cl = run(checklist.ensure_checklist(nid3))
    assert cl["source"] == "profile" and cl["status"] == "auto" and cl["match"]["rating"] == "confident" and len(env["calls"]) == n  # no AI
    run(db.files.update_one({"_id": ObjectId(fids3[3])}, {"$set": {"classification.stage": "after"}}))
    run(db.files.update_one({"_id": ObjectId(fids3[2])}, {"$set": {"pdf_include": False}}))
    reasons = run(checklist.gate_reasons(nid3))
    assert any(r.startswith("Required measurement/test evidence not identified") for r in reasons)  # -> staff review, never approval


def test_uncertain_match_needs_confirmation(env):
    import checklist
    nid4, _ = env["nc"]("Gas entry tee: vent cover detached on the side elevation.", "NC-04")
    cl = run(checklist.ensure_checklist(nid4))
    assert cl["match"]["rating"] == "uncertain" and cl["status"] == "draft"
    assert "Evidence checklist needs staff confirmation (uncertain profile match)" in run(checklist.gate_reasons(nid4))


def test_new_version_keeps_old(env):
    import checklist
    db, u = env["db"], env["u"]
    nid3 = str(run(db.ncs.find_one({"number": "NC-03", "project_id": env["pid"]}))["_id"])
    cl = run(db.ncs.find_one({"_id": ObjectId(nid3)}))["checklist"]
    run(checklist.confirm(nid3, checklist.ChecklistIn(items=[checklist.ItemIn(**i) for i in cl["items"][:3]]), u["employee"]))
    p2 = run(checklist.save_profile(nid3, checklist.ProfileIn(name="Gas entry tee clearance"), u["employee"]))
    assert p2["key"] == env["profile"]["key"] and p2["version"] == 2
    assert run(db.nc_profiles.find_one({"_id": ObjectId(env["profile"]["id"])}))["status"] == "approved"  # unchanged until admin approves
    run(checklist.profile_status(p2["id"], checklist.ProfileStatusIn(status="approved"), u["admin"]))
    assert run(db.nc_profiles.find_one({"_id": ObjectId(env["profile"]["id"])}))["status"] == "superseded"


def test_pdf_one_photo_per_requirement(env):
    import pymupdf
    import server
    from evidence_pack import nc_pdf
    db = env["db"]
    n = run(db.ncs.find_one({"number": "NC-01", "project_id": env["pid"]}))
    files = run(db.files.find({"nc_id": str(n["_id"])}).sort("server_received_at", 1).to_list(20))

    async def get(f, *v):
        return img(f["seed"])
    g = run(server.pdf_photo_groups(n, files, get))
    assert [(h, [c for _, c in x]) for h, x in g] == [("BEFORE REMEDIATION", ["Gas tee and clearance before work"]),
                                                      ("COMPLETED WORK", ["Vent cover fitted"]),
                                                      ("MEASUREMENT / TEST EVIDENCE", ["Tape showing 250mm above tee", "Second measurement at flue"])]
    assert g[2][1][0][0] == img(2) and g[2][1][1][0] == img(3)  # staff-marked photos used for the two measurements
    txt = " ".join(p.get_text() for p in pymupdf.open(stream=nc_pdf({"address": "A", "ref": "R"}, {**n, "export_note": "x"}, [], g), filetype="pdf"))
    assert not any(w in txt.lower() for w in ("draft", "confirm", "uncertain", "missing", "ai "))


def test_pdf_without_checklist_or_switch_off_unchanged(env):
    import server
    db = env["db"]
    n = run(db.ncs.find_one({"number": "NC-01", "project_id": env["pid"]}))
    files = run(db.files.find({"nc_id": str(n["_id"])}).sort("server_received_at", 1).to_list(20))

    async def get(f, *v):
        return img(f["seed"])
    with_cl = run(server.pdf_photo_groups(n, files, get))
    without = run(server.pdf_photo_groups({k: v for k, v in n.items() if k != "checklist"}, files, get))
    assert [h for h, _ in without] == ["BEFORE REMEDIATION", "COMPLETED WORK", "MEASUREMENT / TEST EVIDENCE"] and with_cl != without
    run(db.settings.update_one({"_id": "global"}, {"$set": {"smart_pdf_enabled": False}}))
    try:
        import ai

        async def none(*a, **k):
            return None
        old, ai.arrange_photos = ai.arrange_photos, none
        off = run(server.pdf_photo_groups(n, files, get))
        ai.arrange_photos = old
        assert [h for h, _ in off] == ["REMEDIATION PHOTOGRAPHS"]  # switch OFF: checklist ignored, existing behaviour
    finally:
        run(db.settings.update_one({"_id": "global"}, {"$set": {"smart_pdf_enabled": True}}))


def test_permissions_and_switch(env):
    import checklist
    from auth import require
    db, u = env["db"], env["u"]
    with pytest.raises(HTTPException):
        run(require("admin", "employee")(u["installer"]))
    with pytest.raises(HTTPException):
        run(require("admin")(u["employee"]))  # profile approval is admin only
    nid = str(run(db.ncs.find_one({"number": "NC-01", "project_id": env["pid"]}))["_id"])
    run(db.settings.update_one({"_id": "global"}, {"$set": {"staff_review_enabled": False}}))
    with pytest.raises(HTTPException) as e:
        run(checklist.get_checklist(nid, u["employee"]))
    assert e.value.status_code == 409
    run(db.settings.update_one({"_id": "global"}, {"$set": {"staff_review_enabled": True}}))
