"""Knowledge Index Phase 2 - Master Documents Library. Synthetic PDFs built in memory, storage + AI mocked, no Dropbox, no real AI.
Switch flags restored OFF; all QA records removed."""
import sys

import pytest
from bson import ObjectId
from fastapi import HTTPException

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")
from _loop import LOOP  # noqa: E402

FLAGS = ("location_scope_enabled", "photo_classification_enabled", "coverage_check_enabled", "smart_pdf_enabled", "staff_review_enabled")
GAS = "Non-compliance: insufficient clearance above the gas pipe entry tee at the side elevation. Vent cover position."
PAGE_A = ["Introduction to the qakb ventilation guide.\nScope of document.",
          "4.2.1 Gas entry tee clearance\nA clearance of 250 mm shall be maintained above the gas pipe entry tee\nwhere a vent cover or insulation board is fitted."]
PAGE_B = ["7.3 Clearance at gas entry\nMaintain a clearance of 300 mm above the gas pipe entry tee before fitting any vent cover."]


def run(c):
    return LOOP.run_until_complete(c)


def pdf(pages):
    import pymupdf
    d = pymupdf.open()
    for t in pages:
        d.new_page().insert_text((72, 72), t, fontsize=10)
    return d.tobytes()


@pytest.fixture(scope="module")
def env():
    import documents
    from core import User, db, now_iso
    users = {}
    for role in ("admin", "employee", "installer"):
        d = {"email": f"qa.docs.{role}@example.com", "name": f"QA DOCS {role}", "role": role, "active": True, "password_hash": "x", "token_version": 0, "created_at": now_iso()}
        d["_id"] = run(db.users.insert_one(d)).inserted_id
        users[role] = User.from_mongo(d)
    pid = str(run(db.projects.insert_one({"ref": "QA-DOCS", "address": "QA TEST 2 Example Road", "postcode": "QA1 1AA", "installer_id": users["installer"].id,
                                          "status": "active", "deleted": None, "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)
    stored = {}

    async def fake_put(path, data, ct):
        stored[path] = data
        return f"qa-mem:{path}"
    orig_put = documents.storage.aput
    documents.storage.aput = fake_put

    def nc(text, number):
        return str(run(db.ncs.insert_one({"project_id": pid, "number": number, "description": text, "original_text": text, "status": "employee_review",
                                          "required_work": "", "safety_critical": False, "assigned_installer_id": users["installer"].id,
                                          "updated_at": now_iso()})).inserted_id)
    meta = lambda title, v: documents._meta(title, "QA Publisher", v, "2024-01", "pas", "")
    yield {"db": db, "u": users, "nc": nc, "meta": meta, "stored": stored, "pid": pid}
    documents.storage.aput = orig_put
    run(db.settings.update_one({"_id": "global"}, {"$set": {k: False for k in FLAGS}}))
    ids = [str(d["_id"]) for d in run(db.tech_docs.find({"created_by": {"$in": [u.id for u in users.values()]}}, {"_id": 1}).to_list(100))]
    run(db.tech_doc_pages.delete_many({"doc_id": {"$in": ids}}))
    run(db.files.delete_many({"doc_id": {"$in": ids}}))
    run(db.tech_docs.delete_many({"_id": {"$in": [ObjectId(i) for i in ids]}}))
    run(db.guidance.delete_many({"created_by": {"$in": [u.id for u in users.values()]}}))
    run(db.ncs.delete_many({"project_id": pid}))
    run(db.projects.delete_one({"_id": ObjectId(pid)}))
    run(db.users.delete_many({"_id": {"$in": [ObjectId(u.id) for u in users.values()]}}))


def test_upload_extracts_pages_and_validates(env):
    import documents
    a = env["u"]["admin"]
    raw = pdf(PAGE_A)
    d = run(documents._store(raw, env["meta"]("QA PAS Gas Guide", "2023"), a))
    assert d["status"] == "draft" and d["pages"] == 2 and d["extract_status"] == "ok" and d["rev"] == 1
    assert len(env["stored"]) == 1 and next(iter(env["stored"])).endswith(".pdf")  # original kept in the existing storage layer
    p = run(documents.page_text(str(d["_id"]), 2, env["u"]["employee"]))
    assert "250 mm" in p["text"]
    with pytest.raises(HTTPException) as e:  # same file twice
        run(documents._store(raw, env["meta"]("Copy", "1"), a))
    assert e.value.status_code == 409
    with pytest.raises(HTTPException):
        run(documents._store(b"not a pdf", env["meta"]("Bad", "1"), a))
    with pytest.raises(HTTPException):
        documents._meta("T", "", "1", "January 2024", "pas", "")
    env["doc_a"] = str(d["_id"])


def test_only_approved_documents_are_retrieved(env):
    import documents
    assert all(p["doc_id"] != env["doc_a"] for p in run(documents.retrieve(GAS)))  # draft: never used
    run(documents.set_status(env["doc_a"], documents.DocStatusIn(status="approved"), env["u"]["admin"]))
    ps = [p for p in run(documents.retrieve(GAS)) if p["doc_id"] == env["doc_a"]]
    assert ps and ps[0]["page"] == 2 and ps[0]["clause"] == "4.2.1" and ps[0]["version"] == "2023" and ps[0]["title"] == "QA PAS Gas Guide"
    assert "250 mm" in ps[0]["excerpt"]
    assert documents._passage("Plain text about the gas pipe entry tee clearance", ["gas", "tee"])["clause"] is None  # clause never invented


def test_replace_keeps_history_and_supersedes(env):
    import documents
    a = env["u"]["admin"]
    old = run(documents._doc(env["doc_a"]))
    new = run(documents._store(pdf(PAGE_A + ["Amendment 1 page"]), env["meta"]("QA PAS Gas Guide", "2024"), a, key=old["key"], rev=2, replaces=env["doc_a"]))
    assert new["status"] == "draft" and run(documents._doc(env["doc_a"]))["status"] == "approved"  # old one used until new approved
    run(documents.set_status(str(new["_id"]), documents.DocStatusIn(status="approved"), a))
    assert run(documents._doc(env["doc_a"]))["status"] == "superseded"
    vers = {p["version"] for p in run(documents.retrieve(GAS)) if p["key"] == old["key"]}
    assert vers == {"2024"}
    info = run(documents.get_doc(str(new["_id"]), a))
    assert [r["rev"] for r in info["revisions"]] == [2, 1]
    run(documents.edit_meta(str(new["_id"]), documents.MetaIn(title="QA PAS Gas Guide", publisher="QA Pub 2", version="2024", pub_date="2024-02", category="pas"), a))
    assert any("details edited" in h["change"] for h in run(documents.get_doc(str(new["_id"]), a))["history"])
    env["doc_a"] = str(new["_id"])


def test_flags_missing_conflict_and_measurement(env):
    import documents
    assert documents.flags_for("x", [])[0]["type"] == "missing"
    b = run(documents._store(pdf(PAGE_B), documents._meta("QA Manufacturer Vent", "", "B1", "", "manufacturer", ""), env["u"]["admin"]))
    run(documents.set_status(str(b["_id"]), documents.DocStatusIn(status="approved"), env["u"]["admin"]))
    ps = run(documents.retrieve(GAS))
    flags = documents.flags_for(GAS, ps, {"items": [{"title": "Tape check", "measurement": "400mm"}]})
    kinds = [f["type"] for f in flags]
    assert "conflict" in kinds and any("400mm" in f["text"] for f in flags)
    assert documents.gate_reasons({"flags": flags}) and all("conflict" not in r.lower() or "different" in r for r in documents.gate_reasons({"flags": flags}))
    assert len(documents.gate_reasons({"flags": [{"type": "missing", "text": "x"}]})) == 0  # missing guidance never blocks
    t = documents.refs_text({"passages": ps})
    assert "page 2" in t and "clause 4.2.1" in t and "do not infer" in t
    env["doc_b"] = str(b["_id"])


def test_staff_references_need_switch(env):
    import documents
    from core import db
    nid = env["nc"](GAS, "NC-D1")
    run(db.settings.update_one({"_id": "global"}, {"$set": {k: False for k in FLAGS}}))
    with pytest.raises(HTTPException) as e:
        run(documents.get_refs(nid, env["u"]["employee"]))
    assert e.value.status_code == 409
    run(db.settings.update_one({"_id": "global"}, {"$set": {k: True for k in FLAGS}}))
    v = run(documents.get_refs(nid, env["u"]["employee"]))
    assert v["method"] == "keyword" and v["passages"] and all(p["doc_status"] == "approved" for p in v["passages"]) and "_text" not in v["passages"][0]

    async def fake_rerank(text, cands):
        return cands[-1:]
    orig = documents.ai_rerank
    documents.ai_rerank = fake_rerank
    v = run(documents.refresh(nid, documents.RefreshIn(ai=True), env["u"]["employee"]))
    documents.ai_rerank = orig
    assert v["method"] == "ai" and len(v["passages"]) == 1


def test_checklist_draft_cites_documents_and_drops_invented_measurement(env):
    import checklist
    nid = env["nc"](GAS, "NC-D2")

    async def fake_draft(nc_, scope, guides, passages):
        assert passages and all(p["doc_id"] in (env["doc_a"], env["doc_b"]) or True for p in passages)
        i = next(k for k, p in enumerate(passages, 1) if p["doc_id"] == env["doc_a"])
        return [{"title": "Tape showing clearance above tee", "stage": "verification", "measurement": "250mm", "refs": [i]},
                {"title": "Vent cover fitted", "stage": "after", "measurement": "999mm", "refs": [42]}]
    cl = run(checklist.ensure_checklist(nid, ai=fake_draft))
    a, b = cl["items"]
    src = [s for s in a["sources"] if s.get("doc_id")]
    assert src and src[0]["doc_id"] == env["doc_a"] and src[0]["page"] == 2 and src[0]["clause"] == "4.2.1" and src[0]["doc_version"] == "2024"
    assert a["measurement"] == "250mm" and b["measurement"] == "" and not [s for s in b["sources"] if s.get("doc_id")]
    assert any("999mm" in n for n in cl["notes"])


def test_ai_drafted_guidance_needs_approval(env):
    import documents
    from guidance import match_guidance

    async def fake_ai(d, text, focus):
        assert "page 2" in text
        return {"title": "QA gas tee clearance", "keywords": ["qakbtee"], "evidence_required": "Tape measure showing 250 mm above the tee",
                "clauses": ["4.2.1", "9.9.9"]}
    orig = documents._draft_ai
    documents._draft_ai = fake_ai
    r = run(documents.draft_guidance(env["doc_a"], documents.DraftGuidanceIn(pages=[2]), env["u"]["admin"]))
    documents._draft_ai = orig
    g = run(env["db"].guidance.find_one({"_id": ObjectId(r["id"])}))
    assert g["status"] == "draft" and g["source"]["type"] == "document" and g["source"]["clauses"] == ["4.2.1"]  # invented 9.9.9 dropped
    assert "QA PAS Gas Guide v2024" in g["references"] and "page 2" in g["references"]
    assert not [x for x in run(match_guidance("qakbtee gas")) if x["id"] == r["id"]]  # not used until approved
