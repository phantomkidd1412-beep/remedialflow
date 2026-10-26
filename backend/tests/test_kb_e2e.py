"""ONE controlled end-to-end Knowledge Index test (Phases 1-3) on a synthetic gas-tee NC. Mocked AI + in-memory storage, no Dropbox,
no real AI. Writes /app/test_reports/samples/NC-E2E_knowledge_index.pdf. Flags restored OFF; all QA records removed."""
import io
import os
import random
import sys

import pymupdf
import pytest
from bson import ObjectId
from PIL import Image, ImageDraw

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")
from _loop import LOOP  # noqa: E402

FLAGS = ("location_scope_enabled", "photo_classification_enabled", "coverage_check_enabled", "smart_pdf_enabled", "staff_review_enabled")
GAS = "Non-compliance: a minimum of 250mm clearance above the gas pipe entry tee has not been achieved at the side elevation."
DOC = ["Contents", "4.2.1 Gas entry tee clearance\nA clearance of 250 mm shall be maintained above the gas pipe entry tee\nwhen a vent cover or insulation is fitted."]
INTERNAL = ("not been confirmed", "not been assessed", "Evidence shown for all", "Outstanding", "Location not identified", "confidence",
            "uncertain", "review", "Evidence coverage", "No suitable photo", "Measurement/test must", "No approved example", "AI ", "SUGGESTED", "Rejected")
OUT = "/app/test_reports/samples"
BLOB = {}


def run(c):
    return LOOP.run_until_complete(c)


def scene(seed, shift=0, stamp=""):
    r = random.Random(seed)
    im = Image.new("RGB", (640, 480), (r.randint(40, 200), r.randint(40, 200), r.randint(40, 200)))
    d = ImageDraw.Draw(im)
    for _ in range(14):
        x, y = r.randint(0, 560), r.randint(0, 400)
        d.rectangle([x + shift, y, x + shift + r.randint(30, 160), y + r.randint(30, 120)], fill=(r.randint(0, 255), r.randint(0, 255), r.randint(0, 255)))
    if stamp:
        d.rectangle([0, 450, 640, 480], fill=(0, 0, 0))
        d.text((10, 458), stamp, fill="white")
    b = io.BytesIO()
    im.save(b, "JPEG", quality=85)
    return b.getvalue()


@pytest.fixture(scope="module")
def e2e():
    import documents
    import recommend
    from core import User, db, now_iso
    users = {}
    for role in ("admin", "employee", "installer"):
        d = {"email": f"qa.e2e.{role}@example.com", "name": f"QA E2E {role}", "role": role, "active": True, "password_hash": "x", "token_version": 0, "created_at": now_iso()}
        d["_id"] = run(db.users.insert_one(d)).inserted_id
        users[role] = User.from_mongo(d)
    pid = str(run(db.projects.insert_one({"ref": "QA-E2E", "address": "QA TEST 9 Example Road", "postcode": "QA1 1AA", "installer_id": users["installer"].id,
                                          "status": "active", "deleted": None, "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)
    nid = str(run(db.ncs.insert_one({"project_id": pid, "number": "NC-E2E", "description": GAS, "original_text": GAS, "required_work": "Provide 250mm clearance",
                                     "status": "employee_review", "safety_critical": False, "assigned_installer_id": users["installer"].id, "updated_at": now_iso()})).inserted_id)
    # representative photos: 1 before, 2 measurement shots, 1 during, 2 near-identical completed shots
    spec = [("before", 11, 0), ("verification", 14, 0), ("verification", 15, 0), ("during", 12, 0), ("after", 13, 0), ("after", 13, 3)]
    fids = []
    for k, (st, seed, shift) in enumerate(spec):
        ts = f"2026-05-14 09:{k:02d}:00"
        fid = ObjectId()
        BLOB[f"qa-e2e:{fid}:stamped"] = scene(seed, shift, f"Captured {ts}  51.5072N 0.1276W")
        BLOB[f"qa-e2e:{fid}:display"] = scene(seed, shift)
        run(db.files.insert_one({"_id": fid, "project_id": pid, "nc_id": nid, "kind": "photo", "status": "submitted", "captured_at": ts,
                                 "server_received_at": f"2026-05-14T09:{k:02d}:00", "paths": {"stamped": f"qa-e2e:{fid}:stamped", "display": f"qa-e2e:{fid}:display"},
                                 "classification": {"stage": st, "location_id": None, "dup_of": None, "relevant": True}}))
        fids.append(str(fid))
    ex = ObjectId()
    BLOB[f"qa-e2e:{ex}:display"] = scene(77)
    run(db.files.insert_one({"_id": ex, "project_id": pid, "nc_id": "qa-e2e-example", "kind": "photo", "status": "submitted", "paths": {"display": f"qa-e2e:{ex}:display"}}))

    async def put(path, data, ct):
        BLOB[path] = data
        return path

    async def get(path):
        return BLOB[path], "image/jpeg"
    orig = (documents.storage.aput, recommend.storage.aget, recommend.ai_pick)
    documents.storage.aput, recommend.storage.aget = put, get
    calls = []

    async def fake_ai(nc_, items, pool, examples, docs):
        calls.append({"items": [i["id"] for i in items], "examples": len(examples), "docs": docs, "pool": [str(f["_id"]) for f in pool]})
        idx = {str(f["_id"]): i for i, f in enumerate(pool)}
        return {"it-tape": {"index": idx[fids[1]], "reason": "Tape held vertically above the tee", "uncertain": False},
                "it-after": {"index": idx[fids[4]], "reason": "Vent cover fitted beside the tee", "uncertain": False}}
    recommend.ai_pick = fake_ai
    run(db.settings.update_one({"_id": "global"}, {"$set": {k: True for k in FLAGS}}))
    yield {"db": db, "u": users, "nid": nid, "pid": pid, "f": fids, "ex": str(ex), "calls": calls}
    documents.storage.aput, recommend.storage.aget, recommend.ai_pick = orig
    run(db.settings.update_one({"_id": "global"}, {"$set": {k: False for k in FLAGS}}))
    ids = [str(d["_id"]) for d in run(db.tech_docs.find({"created_by": users["admin"].id}, {"_id": 1}).to_list(10))]
    run(db.tech_doc_pages.delete_many({"doc_id": {"$in": ids}}))
    run(db.files.delete_many({"$or": [{"project_id": pid}, {"doc_id": {"$in": ids}}]}))
    run(db.tech_docs.delete_many({"_id": {"$in": [ObjectId(i) for i in ids]}}))
    run(db.nc_profiles.delete_many({"key": "qa-e2e-gas"}))
    run(db.learning_examples.delete_many({"nc_id": nid}))
    run(db.ncs.delete_many({"project_id": pid}))
    run(db.projects.delete_one({"_id": ObjectId(pid)}))
    run(db.users.delete_many({"_id": {"$in": [ObjectId(u.id) for u in users.values()]}}))


def recs(v):
    return {i["item_id"]: i for i in v["items"]}


def test_end_to_end(e2e):
    import checklist
    import documents
    import recommend
    import server
    from evidence_pack import nc_pdf
    db, u, nid, f, calls = e2e["db"], e2e["u"], e2e["nid"], e2e["f"], e2e["calls"]
    staff, admin = u["employee"], u["admin"]

    # 1. approved technical document -> correct guidance with title / version / page / clause
    d = run(documents._store(pymupdf.open().tobytes() if False else _pdf(DOC), documents._meta("QA PAS Gas Entry Guide", "QA", "2024", "2024-03", "pas", ""), admin))
    run(documents.set_status(str(d["_id"]), documents.DocStatusIn(status="approved"), admin))
    refs = run(documents.get_refs(nid, staff))
    p = refs["passages"][0]
    assert (p["title"], p["version"], p["page"], p["clause"]) == ("QA PAS Gas Entry Guide", "2024", 2, "4.2.1") and "250 mm" in p["excerpt"]
    assert not [x for x in refs["flags"] if x["type"] == "conflict"]

    # 2. approved NC profile supplies the checklist (no checklist-drafting AI call)
    items = [{"id": "it-before", "title": "Gas tee before work", "stage": "before", "measurement": "", "criterion": "", "locations": [],
              "sources": [documents.source_of(p)]},
             {"id": "it-tape", "title": "Tape showing 250mm above tee", "stage": "verification", "measurement": "250mm", "criterion": "", "locations": [],
              "sources": [documents.source_of(p)]},
             {"id": "it-after", "title": "Vent cover fitted", "stage": "after", "measurement": "", "criterion": "", "locations": [], "sources": []}]
    nc = run(db.ncs.find_one({"_id": ObjectId(nid)}))
    from smart_pdf import scope_for
    run(db.nc_profiles.insert_one({"key": "qa-e2e-gas", "version": 1, "status": "approved", "name": "QA gas tee clearance", "items": items,
                                   "signals": checklist.signals(nc, scope_for(nc)), "examples": {"it-tape": [e2e["ex"]]}, "created_by": admin.id}))

    async def no_ai(*a):
        raise AssertionError("checklist AI must not run when an approved profile matches")
    cl = run(checklist.ensure_checklist(nid, ai=no_ai))
    assert cl["source"] == "profile" and cl["status"] == "auto" and [i["id"] for i in cl["items"]] == ["it-before", "it-tape", "it-after"]
    assert cl["items"][1]["sources"][0]["clause"] == "4.2.1" and cl["items"][1]["sources"][0]["page"] == 2

    # 3. Recommend photos: rules for the unique stage, ONE mocked AI call for the rest
    v = recs(run(recommend.run_recs(nid, recommend.RunIn(), staff)))
    assert v["it-before"]["source"] == "rule" and v["it-before"]["file_id"] == f[0]
    assert v["it-tape"]["source"] == "ai" and v["it-tape"]["file_id"] == f[1] and v["it-after"]["file_id"] == f[4]
    assert len(calls) == 1 and calls[0]["items"] == ["it-tape", "it-after"] and calls[0]["examples"] == 1 and "clause 4.2.1" in calls[0]["docs"]
    assert any("Measurement" in x for x in v["it-tape"]["flags"])  # measurement flagged for staff even with a suggestion
    assert run(db.files.count_documents({"nc_id": nid, "checklist_item": {"$ne": None}})) == 0  # suggestions mark nothing

    # 4. cache: repeating makes no AI call
    at = run(recommend.get_recs(nid, staff))["at"]
    again = run(recommend.run_recs(nid, recommend.RunIn(), staff))
    assert len(calls) == 1 and again["at"] == at

    # 5. Accept / Replace / Reject
    run(recommend.act(nid, "it-before", recommend.ActIn(action="accept"), staff))
    run(recommend.act(nid, "it-after", recommend.ActIn(action="replace", file_id=f[5]), staff))
    v = recs(run(recommend.act(nid, "it-tape", recommend.ActIn(action="reject"), staff)))
    assert v["it-before"]["state"] == "accepted" and v["it-before"]["source"] == "staff"
    assert v["it-after"]["state"] == "replaced" and v["it-after"]["file_id"] == f[5]
    assert v["it-tape"]["state"] == "rejected" and v["it-tape"]["file_id"] is None
    assert any("No suitable photo" in x for x in v["it-tape"]["flags"]) and any("Measurement" in x for x in v["it-tape"]["flags"])  # missing evidence flagged
    marks = {str(x["_id"]): x.get("checklist_item") for x in run(db.files.find({"nc_id": nid}).to_list(20))}
    assert marks[f[0]] == "it-before" and marks[f[5]] == "it-after" and marks[f[4]] is None and marks[f[1]] is None

    # 6. corrections -> proposed profile update; approved version untouched
    r = run(recommend.propose(nid, recommend.ProposeIn(), staff))
    prop = run(db.nc_profiles.find_one({"_id": ObjectId(r["id"])}))
    assert prop["status"] == "proposed" and prop["version"] == 2 and prop["examples"]["it-after"] == [f[5]] and prop["examples"]["it-before"] == [f[0]]
    assert set(prop["bad_examples"]["it-after"]) == {f[4]} and set(prop["bad_examples"]["it-tape"]) == {f[1]}
    assert run(db.nc_profiles.find_one({"key": "qa-e2e-gas", "version": 1}))["status"] == "approved"
    acts = sorted(x["action"] for x in run(db.learning_examples.find({"nc_id": nid}).to_list(20)))
    assert acts == ["accept_rec", "reject_rec", "replace_rec"]

    # 7. PDF: one photo per requirement (staff choices), stamped copy, no internal wording, rejected photo not used
    n = run(db.ncs.find_one({"_id": ObjectId(nid)}))
    files = run(db.files.find({"nc_id": nid}).to_list(20))

    async def load(fl, *variants):
        return next((BLOB[fl["paths"][v]] for v in variants if v in fl.get("paths", {})), None)
    groups = run(server.pdf_photo_groups(n, files, load))
    shown = [img for _, slots in groups for img, _ in slots]
    assert [len(slots) for _, slots in groups] == [1] * len(groups)
    assert BLOB[f"qa-e2e:{f[0]}:stamped"] in shown and BLOB[f"qa-e2e:{f[5]}:stamped"] in shown
    assert BLOB[f"qa-e2e:{f[4]}:stamped"] not in shown, "replaced suggestion must not appear"
    assert BLOB[f"qa-e2e:{f[1]}:stamped"] not in shown, "photo staff rejected for the measurement must not appear for it"
    pdf = nc_pdf({"address": "QA TEST 9 Example Road", "postcode": "QA1 1AA", "ref": "QA-E2E"}, {**n, "export_note": ""}, [scene(900)], groups)
    os.makedirs(OUT, exist_ok=True)
    open(f"{OUT}/NC-E2E_knowledge_index.pdf", "wb").write(pdf)
    txt = " ".join(pg.get_text() for pg in pymupdf.open(stream=pdf, filetype="pdf"))
    assert not [w for w in INTERNAL if w.lower() in txt.lower()], txt


def _pdf(pages):
    d = pymupdf.open()
    for t in pages:
        d.new_page().insert_text((72, 72), t, fontsize=10)
    return d.tobytes()
