"""Idempotent, additive data migrations. Each runs once and is recorded in `migrations`."""
import logging
from core import db, now_iso

log = logging.getLogger("migrations")


async def m001_status_model_and_nc_assignment():
    # NCs inherit the project's installer; legacy statuses map onto the unified model.
    async for p in db.projects.find({}, {"installer_id": 1}):
        await db.ncs.update_many({"project_id": str(p["_id"]), "assigned_installer_id": {"$exists": False}},
                                 {"$set": {"assigned_installer_id": p.get("installer_id")}})
    await db.ncs.update_many({"status": "ai_verified"}, {"$set": {"status": "approved", "approved_by": "ai"}})
    await db.ncs.update_many({"status": "manual_review"}, {"$set": {"status": "employee_review"}})
    async for n in db.ncs.find({"original_text": {"$exists": False}}, {"description": 1}):
        await db.ncs.update_one({"_id": n["_id"]}, {"$set": {"original_text": n.get("description", "")}})
    await db.ncs.update_many({"assignment_history": {"$exists": False}}, {"$set": {"assignment_history": []}})


async def m002_project_lifecycle():
    from server import recompute_project  # local import to avoid cycle
    async for p in db.projects.find({}, {"_id": 1}):
        pid = str(p["_id"])
        e = await db.earnings.find_one({"project_id": pid})
        if e and not (await db.projects.find_one({"_id": p["_id"]}, {"completed_at": 1})).get("completed_at"):
            await db.projects.update_one({"_id": p["_id"]}, {"$set": {"completed_at": e["earned_at"], "completion_history": [e["earned_at"]]}})
        await recompute_project(pid)


async def m003_rates_and_policies():
    s = await db.settings.find_one({"_id": "global"}) or {}
    rate = s.get("default_job_rate", 150.0)
    await db.users.update_many({"pay_type": {"$exists": False}}, {"$set": {"pay_type": "per_job"}})
    await db.users.update_many({"role": "installer", "default_rate": {"$exists": False}}, {"$set": {"default_rate": rate}})
    await db.settings.update_one({"_id": "global"}, {"$set": {"gallery_policy": "accept", "auto_approve_low_risk": True}}, upsert=True)
    async for p in db.projects.find({"rates": {"$exists": False}}, {"installer_id": 1, "rate": 1}):
        ids = set(await db.ncs.distinct("assigned_installer_id", {"project_id": str(p["_id"])})) - {None}
        await db.projects.update_one({"_id": p["_id"]}, {"$set": {"rates": {i: p.get("rate", rate) for i in ids}}})


async def m004_pdf_photo_cleanup():
    # hide logos / repeated decorations on existing imported projects; nothing is deleted
    from report_import import clean_project_report_photos
    for pid in await db.files.distinct("project_id", {"kind": "trustmark", "source": "pdf_import"}):
        n = await clean_project_report_photos(pid)
        if n:
            log.info("project %s: %d decorative PDF images hidden", pid, n)


MIGRATIONS = [("001_status_model_and_nc_assignment", m001_status_model_and_nc_assignment),
              ("002_project_lifecycle", m002_project_lifecycle),
              ("003_rates_and_policies", m003_rates_and_policies),
              ("004_pdf_photo_cleanup", m004_pdf_photo_cleanup)]


async def run_migrations():
    for name, fn in MIGRATIONS:
        if await db.migrations.find_one({"_id": name}):
            continue
        log.info("running migration %s", name)
        await fn()
        await db.migrations.insert_one({"_id": name, "applied_at": now_iso()})
