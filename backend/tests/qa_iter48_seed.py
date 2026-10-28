# Iter48: synthetic QA data for hidden panels / review-reason / concurrent evidence upload tests.
# Usage: python qa_iter48_seed.py [seed|flags_on|flags_off|cleanup]. No real AI: NC-08 recommendation cache is pre-built with a stub.
import asyncio
import json
import sys

sys.path.insert(0, "/app/backend")
from bson import ObjectId  # noqa: E402

from core import db, now_iso  # noqa: E402

PW = "QaCheck-7731x"
EMAILS = {"admin": "qa.i48.admin@example.com", "installer": "qa.i48.installer@example.com", "employee": "qa.i48.staff@example.com"}
REF = "QA-I48"
FLAGS = ("location_scope_enabled", "photo_classification_enabled", "coverage_check_enabled", "smart_pdf_enabled", "staff_review_enabled")


def items(p):
    return [{"id": f"{p}-before", "title": "Gas tee before work", "stage": "before", "locations": [], "measurement": "", "criterion": "", "sources": []},
            {"id": f"{p}-tape", "title": "Tape showing 250mm", "stage": "verification", "locations": [], "measurement": "250mm", "criterion": "", "sources": []},
            {"id": f"{p}-after", "title": "Vent cover fitted", "stage": "after", "locations": [], "measurement": "", "criterion": "", "sources": []}]


async def seed():
    from auth import ph
    import recommend
    uid = {}
    for role, email in EMAILS.items():
        await db.users.delete_many({"email": email})
        d = {"email": email, "name": f"QA I48 {role}", "role": role, "active": True, "password_hash": ph.hash(PW), "token_version": 0, "created_at": now_iso()}
        uid[role] = str((await db.users.insert_one(d)).inserted_id)
    pid = str((await db.projects.insert_one({"ref": REF, "address": "QA TEST 48 Example Road", "postcode": "QA1 1AA", "installer_id": uid["installer"],
                                             "status": "active", "deleted": None, "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)
    out = {"pid": pid, "users": uid}
    for k in range(1, 10):
        num = f"NC-0{k}"
        nid = str((await db.ncs.insert_one({"project_id": pid, "number": num, "description": f"QA {num} gas tee clearance", "original_text": "Gas tee clearance 250mm",
                                            "required_work": "", "status": "employee_review", "safety_critical": False, "assigned_installer_id": uid["installer"],
                                            "checklist": {"v": 1, "status": "confirmed", "source": "staff", "profile": None, "items": items(f"n{k}"), "history": [],
                                                          } if k == 8 else None,
                                            "review_history": [{"action": "reject", "reason": "QA earlier internal reason", "note": "earlier note", "at": "2026-01-01T00:00:00"}] if k == 9 else [],
                                            "updated_at": now_iso()})).inserted_id)
        out[num] = {"nid": nid}
        if k in (8, 9):  # seeded fake photos (no storage writes) for long-press / Auto Select / review tests
            fids = []
            for j, st in enumerate(["before", "verification", "after"]):
                fid = ObjectId()
                await db.files.insert_one({"_id": fid, "project_id": pid, "nc_id": nid, "kind": "photo", "status": "submitted", "captured_at": f"2026-05-01 10:0{j}",
                                           "source": "in_app_camera", "sha256": f"qa48-{num}-{j}", "size": 1, "mime": "image/jpeg", "uploaded_by": uid["installer"],
                                           "server_received_at": f"2026-05-01T10:0{j}:00", "paths": {"stamped": f"qa48-missing/{j}.jpg"},
                                           "classification": {"stage": st, "location_id": None, "dup_of": None, "relevant": True} if j < 2 else None, "qa": "iter48"})
                fids.append(str(fid))
            out[num]["files"] = fids
        if k == 8:
            async def stub(nc_, its, pool, ex, docs):  # stub, no Gemini
                return {i["id"]: {"index": 1 if len(pool) > 1 else 0, "reason": "Stub pick", "uncertain": True} for i in its}
            r = await recommend.build(nid, ai=stub)
            out[num]["cache_ai_calls_stub"] = r["ai_calls"]
    print(json.dumps(out))


async def flags(v):
    await db.settings.update_one({"_id": "global"}, {"$set": {f: v for f in FLAGS}})
    s = await db.settings.find_one({"_id": "global"})
    print({f: s.get(f) for f in FLAGS})


async def cleanup():
    out = {}
    projs = [str(p["_id"]) async for p in db.projects.find({"ref": REF})]
    ncs = [str(n["_id"]) async for n in db.ncs.find({"project_id": {"$in": projs}})]
    users = [str(u["_id"]) async for u in db.users.find({"email": {"$in": list(EMAILS.values())}})]
    paths = sorted({v for f in await db.files.find({"nc_id": {"$in": ncs}, "qa": {"$ne": "iter48"}}).to_list(5000) for v in (f.get("paths") or {}).values() if v})
    out["storage_paths_written"] = len(paths)
    with open("/app/test_reports/iter48_dropbox_files.txt", "w") as fh:
        fh.write("\n".join(paths))
    out["learning_examples"] = (await db.learning_examples.delete_many({"nc_id": {"$in": ncs}})).deleted_count
    out["files"] = (await db.files.delete_many({"nc_id": {"$in": ncs}})).deleted_count
    out["submissions"] = (await db.submissions.delete_many({"nc_id": {"$in": ncs}})).deleted_count
    out["ncs"] = (await db.ncs.delete_many({"_id": {"$in": [ObjectId(n) for n in ncs]}})).deleted_count
    out["projects"] = (await db.projects.delete_many({"_id": {"$in": [ObjectId(p) for p in projs]}})).deleted_count
    for c in ("sessions", "refresh_tokens", "notifications"):
        if c in await db.list_collection_names():
            out[c] = (await db[c].delete_many({"user_id": {"$in": users}})).deleted_count
    out["users"] = (await db.users.delete_many({"email": {"$in": list(EMAILS.values())}})).deleted_count
    print(out)


cmd = sys.argv[1] if len(sys.argv) > 1 else "seed"
asyncio.get_event_loop().run_until_complete({"seed": seed, "cleanup": cleanup}[cmd]() if cmd in ("seed", "cleanup") else flags(cmd == "flags_on"))
