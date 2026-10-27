# Iter40: synthetic QA data for evidence-selection UI retest. Usage: python qa_iter40_seed.py [seed|flags_on|flags_off|cleanup|show]
import asyncio
import json
import sys

sys.path.insert(0, "/app/backend")
from bson import ObjectId  # noqa: E402

from core import db, now_iso  # noqa: E402

PW = "QaCheck-7731x"
EMAILS = {"admin": "qa.kbui.admin@example.com", "installer": "qa.kbui.installer@example.com", "employee": "qa.kbui.staff@example.com"}
REF = "QA-KBUI-40"
FLAGS = ("location_scope_enabled", "photo_classification_enabled", "coverage_check_enabled", "smart_pdf_enabled", "staff_review_enabled")
ITEMS = [{"id": "q-before", "title": "Gas tee before work", "stage": "before", "locations": [], "measurement": "", "criterion": "", "sources": []},
         {"id": "q-tape", "title": "Tape showing 250mm", "stage": "verification", "locations": [], "measurement": "250mm", "criterion": "", "sources": []},
         {"id": "q-after", "title": "Vent cover fitted", "stage": "after", "locations": [], "measurement": "", "criterion": "", "sources": []}]


async def seed():
    from auth import ph
    import recommend
    uid = {}
    for role, email in EMAILS.items():
        await db.users.delete_many({"email": email})
        d = {"email": email, "name": f"QA KBUI {role}", "role": role, "active": True, "password_hash": ph.hash(PW), "token_version": 0, "created_at": now_iso()}
        uid[role] = str((await db.users.insert_one(d)).inserted_id)
    pid = str((await db.projects.insert_one({"ref": REF, "address": "QA TEST 40 Example Road", "postcode": "QA1 1AA", "installer_id": uid["installer"],
                                             "status": "active", "deleted": None, "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)
    nid = str((await db.ncs.insert_one({"project_id": pid, "number": "NC-01", "description": "QA gas tee clearance", "original_text": "Gas tee clearance 250mm",
                                        "required_work": "", "status": "employee_review", "safety_critical": False, "assigned_installer_id": uid["installer"],
                                        "checklist": {"v": 1, "status": "draft", "source": "ai", "profile": None, "items": ITEMS, "history": []},
                                        "updated_at": now_iso()})).inserted_id)
    for k, st in enumerate(["before", "before", "verification", "after"]):
        await db.files.insert_one({"_id": ObjectId(), "project_id": pid, "nc_id": nid, "kind": "photo", "status": "submitted", "captured_at": f"2026-05-01 10:0{k}",
                                   "source": "in_app_camera", "sha256": f"qa40-{k}", "size": 1, "mime": "image/jpeg", "uploaded_by": uid["installer"],
                                   "server_received_at": f"2026-05-01T10:0{k}:00", "paths": {"stamped": f"qa40-missing/{k}.jpg"},
                                   "classification": {"stage": st, "location_id": None, "dup_of": None, "relevant": True}, "qa": "iter40"})

    async def no_ai(*a, **k):
        return None  # stub: no AI call (Gemini never invoked); unresolved items stay manual
    recs = await recommend.build(nid, ai=no_ai, allow_draft=True)  # rule picks only -> cached, ai_used False
    print(json.dumps({"pid": pid, "nid": nid, "users": uid, "ai_used": recs["ai_used"], "picks": {k: v["file_id"] for k, v in recs["items"].items()}}))


async def flags(v):
    await db.settings.update_one({"_id": "global"}, {"$set": {f: v for f in FLAGS}})
    s = await db.settings.find_one({"_id": "global"})
    print({f: s.get(f) for f in FLAGS})


async def cleanup():
    out = {}
    projs = [str(p["_id"]) async for p in db.projects.find({"ref": REF})]
    ncs = [str(n["_id"]) async for n in db.ncs.find({"project_id": {"$in": projs}})]
    users = [str(u["_id"]) async for u in db.users.find({"email": {"$in": list(EMAILS.values())}})]
    out["learning_examples"] = (await db.learning_examples.delete_many({"nc_id": {"$in": ncs}})).deleted_count
    out["files"] = (await db.files.delete_many({"nc_id": {"$in": ncs}, "qa": "iter40"})).deleted_count
    out["ncs(with selections)"] = (await db.ncs.delete_many({"_id": {"$in": [ObjectId(n) for n in ncs]}})).deleted_count
    out["projects"] = (await db.projects.delete_many({"_id": {"$in": [ObjectId(p) for p in projs]}})).deleted_count
    for c in ("sessions", "refresh_tokens"):
        if c in await db.list_collection_names():
            out[c] = (await db[c].delete_many({"user_id": {"$in": users}})).deleted_count
    out["users"] = (await db.users.delete_many({"email": {"$in": list(EMAILS.values())}})).deleted_count
    print(out)


async def show():
    p = await db.projects.find_one({"ref": REF})
    n = await db.ncs.find_one({"project_id": str(p["_id"])})
    print(json.dumps({"pid": str(p["_id"]), "nid": str(n["_id"]), "sel": n.get("evidence_selection"),
                      "photos": [str(f["_id"]) async for f in db.files.find({"nc_id": str(n["_id"])}).sort("server_received_at", 1)]}, default=str))


cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
asyncio.get_event_loop().run_until_complete({"seed": seed, "cleanup": cleanup, "show": show}[cmd]() if cmd in ("seed", "cleanup", "show")
                                            else flags(cmd == "flags_on"))
