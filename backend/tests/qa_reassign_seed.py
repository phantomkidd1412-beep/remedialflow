"""PREVIEW-ONLY QA seed for reassignment + town dashboard. Synthetic accounts/projects tagged qa="reassign-ui".
python tests/qa_reassign_seed.py seed | cleanup   (audit entries are hash-chained and are never deleted)"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from auth import ph  # noqa: E402
from core import db, now_iso  # noqa: E402

TAG = "reassign-ui"
PW = "PreviewQA!2026"
USERS = [("qa.admin.preview@example.com", "QA Admin", "admin"), ("qa.demo.preview@example.com", "QA Demo", "installer"),
         ("qa.sam.preview@example.com", "QA Sam", "installer")]
PROJECTS = [  # ref, address, postcode, owner, NC statuses
    ("QA-LUT-1", "12 Station Road, Luton, Bedfordshire", "LU1 2AB", "QA Demo", ["open", "employee_review", "more_evidence"]),
    ("QA-LUT-2", "4 Park Street, luton", "LU1 3CD", "QA Demo", ["approved", "open"]),
    ("QA-BED-1", "8 High Street, Bedford", "MK40 1AA", "QA Demo", ["open"]),
    ("QA-STH-1", "21 King Street, Southall, Greater London", "UB2 4AA", "QA Demo", ["employee_review", "approved"]),
    ("QA-BED-2", "3 Mill Lane, Bedford", "MK40 2BB", "QA Sam", ["open", "open"]),
    # shared project: NC-01 Demo, NC-02 Sam, NC-03 unassigned (lead installer Demo)
    ("QA-LUT-3", "9 Church Street, Luton", "LU1 4EF", "QA Demo", [("open", "QA Demo"), ("more_evidence", "QA Sam"), ("open", None)]),
]


async def seed():
    await cleanup()
    ids = {}
    for email, name, role in USERS:
        ids[name] = str((await db.users.insert_one({"email": email, "name": name, "role": role, "active": True, "password_hash": ph.hash(PW),
                                                    "token_version": 0, "created_at": now_iso(), "qa": TAG})).inserted_id)
    for ref, addr, pc, owner, sts in PROJECTS:
        uid = ids[owner]
        pid = str((await db.projects.insert_one({"ref": ref, "address": addr, "postcode": pc, "installer_id": uid, "status": "in_progress", "rate": 150,
                                                 "created_at": now_iso(), "updated_at": now_iso(), "deleted": None, "qa": TAG})).inserted_id)
        for i, st in enumerate(sts, 1):
            st, who = st if isinstance(st, tuple) else (st, owner)
            await db.ncs.insert_one({"project_id": pid, "number": f"NC-{i:02d}", "description": f"QA defect {i}", "required_work": "Fix and photograph",
                                     "status": st, "assigned_installer_id": ids[who] if who else None, "requires_stages": False, "created_at": now_iso(), "qa": TAG})
        # cached counters (normally set by recompute_project; set directly here so no staff notifications fire)
        await db.projects.update_one({"_id": __import__("bson").ObjectId(pid)}, {"$set": {"nc_total": len(sts), "nc_done": sum(
            1 for x in sts if (x[0] if isinstance(x, tuple) else x) == "approved")}})
    print(json.dumps({"password": PW, "users": ids}))


async def cleanup():
    pids = [str(p["_id"]) async for p in db.projects.find({"qa": TAG})]
    uids = [str(u["_id"]) async for u in db.users.find({"qa": TAG})]
    await db.ncs.delete_many({"project_id": {"$in": pids}})
    await db.files.delete_many({"project_id": {"$in": pids}})
    await db.notifications.delete_many({"$or": [{"project_id": {"$in": pids}}, {"user_id": {"$in": uids}}]})
    await db.push_log.delete_many({"user_id": {"$in": uids}})
    await db.projects.delete_many({"qa": TAG})
    await db.users.delete_many({"qa": TAG})


if __name__ == "__main__":
    asyncio.run(seed() if sys.argv[1:] == ["seed"] else cleanup())
