import asyncio, sys
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv; load_dotenv("/app/backend/.env")
from core import db, now_iso
from auth import ph
FLAGS = ("location_scope_enabled", "photo_classification_enabled", "coverage_check_enabled", "smart_pdf_enabled", "staff_review_enabled")
GAS = "There are non-compliances regarding a minimum of 250mm clearance above the gas pipe entry tee at the side elevation."


async def clean():
    p = await db.projects.find_one({"ref": "QA-KBUI"})
    if p:
        for c in ("files", "ncs"):
            await db[c].delete_many({"project_id": str(p["_id"])})
        await db.projects.delete_one({"_id": p["_id"]})
    us = [str(u["_id"]) async for u in db.users.find({"email": {"$regex": r"^qa\.kbui\."}})]
    for c in ("sessions", "notifications"):
        await db[c].delete_many({"user_id": {"$in": us}})
    await db.learning_examples.delete_many({"by": {"$in": us}})
    await db.nc_profiles.delete_many({"created_by": {"$in": us}})
    await db.login_attempts.delete_many({"email": {"$regex": r"^qa\.kbui\."}})
    await db.users.delete_many({"email": {"$regex": r"^qa\.kbui\."}})
    for d in await db.tech_docs.find({"key": "qakbui"}).to_list(10):
        await db.tech_doc_pages.delete_many({"doc_id": str(d["_id"])})
    await db.tech_docs.delete_many({"key": "qakbui"})
    await db.settings.update_one({"_id": "global"}, {"$set": {k: False for k in FLAGS}})


async def seed():
    await clean()
    u = {r: str((await db.users.insert_one({"email": f"qa.kbui.{r}@example.com", "name": f"QA {r}", "role": r, "active": True, "password_hash": ph.hash("QaCheck-7731x"),
                                            "token_version": 0, "created_at": now_iso()})).inserted_id) for r in ("admin", "installer")}
    pid = str((await db.projects.insert_one({"ref": "QA-KBUI", "address": "QA TEST 1 Example Road", "postcode": "QA1 1AA", "installer_id": u["installer"], "rate": 0,
                                             "status": "active", "require_video": False, "deleted": None, "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)
    nid = str((await db.ncs.insert_one({"project_id": pid, "number": "NC-01", "description": GAS, "original_text": GAS, "required_work": "", "status": "employee_review",
                                        "safety_critical": False, "assigned_installer_id": u["installer"], "updated_at": now_iso(),
                                        "checklist": {"v": 1, "status": "draft", "source": "ai", "profile": None, "match": {"rating": "none", "score": 0}, "history": [],
                                                      "items": [{"id": "it-a", "title": "Gas tee and clearance before work", "stage": "before", "locations": [], "measurement": "", "criterion": "", "sources": []},
                                                                {"id": "it-b", "title": "Tape showing 250mm above tee", "stage": "verification", "locations": [], "measurement": ">=250mm", "criterion": "", "sources": []},
                                                                {"id": "it-c", "title": "Vent cover fitted", "stage": "after", "locations": [], "measurement": "", "criterion": "", "sources": []}]}})).inserted_id)
    for k, st in enumerate(["before", "verification", "during"]):
        await db.files.insert_one({"project_id": pid, "nc_id": nid, "kind": "photo", "status": "submitted", "source": "in_app_camera", "pdf_include": None, "sha256": f"kb{k}",
                                   "size": 1, "mime": "image/jpeg", "uploaded_by": u["installer"], "processing": "ready", "location_verified": True,
                                   "paths": {"display": f"qa-mem:{k}"}, "server_received_at": f"2026-01-01T00:0{k}:00",
                                   "classification": {"v": 1, "location_id": None, "stage": st, "relevant": True, "dup_of": None, "component": "gas tee", "source": "ai"}})
    did = str((await db.tech_docs.insert_one({"key": "qakbui", "rev": 1, "title": "QA PAS Gas Guide", "publisher": "QA Pub", "version": "2024", "pub_date": "2024-01",
                                              "category": "pas", "status": "approved", "file_id": None, "sha256": "qakbui", "size": 1, "pages": 2, "text_pages": 2,
                                              "extract_status": "ok", "replaces": None, "created_by": u["admin"], "created_at": now_iso(), "notes": "",
                                              "history": [{"at": now_iso(), "by": u["admin"], "change": "uploaded"}]})).inserted_id)
    await db.tech_doc_pages.insert_many([{"doc_id": did, "page": 1, "text": "Introduction"},
                                         {"doc_id": did, "page": 2, "text": "4.2.1 Gas entry tee clearance\nA clearance of 250 mm shall be maintained above the gas pipe entry tee\nwhere a vent cover is fitted."}])
    await db.settings.update_one({"_id": "global"}, {"$set": {k: True for k in FLAGS}})
    print(pid, nid)

asyncio.run(seed() if sys.argv[1] == "seed" else clean())
