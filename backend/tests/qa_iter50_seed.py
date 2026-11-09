# Iter50: synthetic QA data for installer-name search, installer NC states, gallery-vs-camera stamping.
# Usage: python qa_iter50_seed.py [seed|flags_off|cleanup]. No AI, no real projects touched.
import asyncio
import json
import sys

sys.path.insert(0, "/app/backend")
from bson import ObjectId  # noqa: E402

from core import db, now_iso  # noqa: E402

PW = "QaCheck-7731x"
USERS = {"admin": ("qa.i50.admin@example.com", "QA I50 Admin", "admin"),
         "employee": ("qa.i50.staff@example.com", "QA I50 Staff", "employee"),
         "samuel": ("qa.i50.samuel@example.com", "Samuel QaTester", "installer"),
         "priya": ("qa.i50.priya@example.com", "Priya QaWorker", "installer")}
EMAILS = [v[0] for v in USERS.values()]
REFS = ["QA-I50-A", "QA-I50-B", "QA-I50-C"]
FLAGS = ("location_scope_enabled", "photo_classification_enabled", "coverage_check_enabled", "smart_pdf_enabled", "staff_review_enabled")


async def seed():
    from auth import ph
    uid = {}
    for k, (email, name, role) in USERS.items():
        await db.users.delete_many({"email": email})
        d = {"email": email, "name": name, "role": role, "active": True, "password_hash": ph.hash(PW), "token_version": 0, "created_at": now_iso()}
        uid[k] = str((await db.users.insert_one(d)).inserted_id)
    out = {"users": uid}

    async def proj(ref, addr, pc, inst):
        return str((await db.projects.insert_one({"ref": ref, "address": addr, "postcode": pc, "installer_id": inst, "status": "active",
                                                  "require_video": False, "deleted": None, "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)

    async def nc(pid, num, status, inst, note=""):
        return str((await db.ncs.insert_one({"project_id": pid, "number": num, "description": f"QA {num} {status}", "original_text": "QA text",
                                             "required_work": "", "status": status, "safety_critical": False, "assigned_installer_id": inst,
                                             "installer_note": note, "review_history": [], "updated_at": now_iso()})).inserted_id)
    # A: project installer Samuel, 5 NCs in each installer state
    pa = await proj("QA-I50-A", "QA TEST 50 Alpha Road", "QA5 0AA", uid["samuel"])
    out["A"] = {"pid": pa}
    for num, st in [("NC-01", "open"), ("NC-02", "open"), ("NC-03", "more_evidence"), ("NC-04", "employee_review"), ("NC-05", "approved")]:
        out["A"][num] = await nc(pa, num, st, uid["samuel"])
    await db.files.insert_one({"_id": ObjectId(), "project_id": pa, "nc_id": out["A"]["NC-02"], "kind": "photo", "status": "submitted",
                               "source": "in_app_camera", "sha256": "qa50-fake", "size": 1, "mime": "image/jpeg", "uploaded_by": uid["samuel"],
                               "server_received_at": "2026-01-10T10:00:00", "paths": {}, "processing": "ready", "qa": "iter50"})
    # B: project installer Priya, NC-01 assigned to Samuel (NC-level match), has an existing installer_note
    pb = await proj("QA-I50-B", "QA TEST 50 Beta Street", "QA5 0BB", uid["priya"])
    out["B"] = {"pid": pb, "NC-01": await nc(pb, "NC-01", "open", uid["samuel"], note="QA existing installer note"),
                "NC-02": await nc(pb, "NC-02", "open", uid["priya"])}
    # C: Priya only - must NOT match Samuel searches
    pc = await proj("QA-I50-C", "QA TEST 50 Gamma Lane", "QA5 0CC", uid["priya"])
    out["C"] = {"pid": pc, "NC-01": await nc(pc, "NC-01", "open", uid["priya"])}
    print(json.dumps(out))


async def flags_off():
    await db.settings.update_one({"_id": "global"}, {"$set": {f: False for f in FLAGS}})
    s = await db.settings.find_one({"_id": "global"})
    print({f: s.get(f) for f in FLAGS})


async def cleanup():
    out = {}
    projs = [str(p["_id"]) async for p in db.projects.find({"ref": {"$in": REFS}})]
    ncs = [str(n["_id"]) async for n in db.ncs.find({"project_id": {"$in": projs}})]
    users = [str(u["_id"]) async for u in db.users.find({"email": {"$in": EMAILS}})]
    paths = sorted({v for f in await db.files.find({"nc_id": {"$in": ncs}, "qa": {"$ne": "iter50"}}).to_list(5000) for v in (f.get("paths") or {}).values() if v})
    out["storage_paths_written"] = len(paths)
    with open("/app/test_reports/iter50_dropbox_files.txt", "w") as fh:
        fh.write("\n".join(paths))
    out["files"] = (await db.files.delete_many({"nc_id": {"$in": ncs}})).deleted_count
    out["submissions"] = (await db.submissions.delete_many({"nc_id": {"$in": ncs}})).deleted_count
    out["learning_examples"] = (await db.learning_examples.delete_many({"nc_id": {"$in": ncs}})).deleted_count
    out["ncs"] = (await db.ncs.delete_many({"_id": {"$in": [ObjectId(n) for n in ncs]}})).deleted_count
    out["projects"] = (await db.projects.delete_many({"_id": {"$in": [ObjectId(p) for p in projs]}})).deleted_count
    for c in ("sessions", "refresh_tokens", "notifications"):
        if c in await db.list_collection_names():
            out[c] = (await db[c].delete_many({"user_id": {"$in": users}})).deleted_count
    out["users"] = (await db.users.delete_many({"email": {"$in": EMAILS}})).deleted_count
    print(out)


cmd = sys.argv[1] if len(sys.argv) > 1 else "seed"
asyncio.get_event_loop().run_until_complete({"seed": seed, "cleanup": cleanup, "flags_off": flags_off}[cmd]())
