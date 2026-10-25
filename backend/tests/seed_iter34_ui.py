"""Seed/cleanup QA data for iteration-34 UI check. usage: python seed_iter34_ui.py seed|clean"""
import asyncio
import sys

sys.path.insert(0, "/app/backend")
from auth import ph  # noqa: E402
from core import db, now_iso  # noqa: E402
from scope import merge, rule_extract  # noqa: E402

PW = "QaUi34-Verify-2026"
W8 = "End caps missing to 8 no. external window sills (W1-W8)."


async def seed():
    ids = {}
    for role in ("employee", "installer"):
        r = await db.users.insert_one({"email": f"qa.ui34.{role}@example.com", "name": f"QA UI34 {role}", "role": role, "active": True,
                                       "password_hash": ph.hash(PW), "token_version": 0, "created_at": now_iso()})
        ids[role] = str(r.inserted_id)
    p = await db.projects.insert_one({"ref": "QA-UI34", "address": "QA TEST 1 Example Road", "postcode": "QA1 1AA", "installer_id": ids["installer"],
                                      "rate": 0, "status": "active", "require_video": False, "deleted": None, "created_at": now_iso(), "updated_at": now_iso()})
    pid = str(p.inserted_id)
    sc = {"v": 1, **merge(rule_extract(W8), None, W8, None), "method": "rules", "extracted_at": now_iso()}
    await db.ncs.insert_one({"project_id": pid, "number": "NC-01", "description": W8, "original_text": W8, "required_work": "Fit end caps",
                             "status": "open", "safety_critical": False, "assigned_installer_id": ids["installer"], "scope": sc, "updated_at": now_iso()})
    await db.ncs.insert_one({"project_id": pid, "number": "NC-02", "description": "Gap at eaves junction.", "original_text": "Gap at eaves junction.",
                             "required_work": "Seal", "status": "open", "safety_critical": False, "assigned_installer_id": ids["installer"], "updated_at": now_iso()})
    print(pid)


async def clean():
    users = [str(u["_id"]) async for u in db.users.find({"email": {"$regex": r"^qa\.ui34\."}})]
    pids = [str(p["_id"]) async for p in db.projects.find({"ref": "QA-UI34"})]
    for pid in pids:
        await db.ncs.delete_many({"project_id": pid})
        await db.files.delete_many({"project_id": pid})
    await db.projects.delete_many({"ref": "QA-UI34"})
    await db.sessions.delete_many({"user_id": {"$in": users}})
    await db.notifications.delete_many({"user_id": {"$in": users}})
    await db.users.delete_many({"email": {"$regex": r"^qa\.ui34\."}})
    print("cleaned", len(users), len(pids))


asyncio.run(seed() if sys.argv[1] == "seed" else clean())
