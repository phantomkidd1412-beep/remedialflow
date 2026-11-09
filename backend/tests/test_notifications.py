"""Notifications + reminders (mocked push: EMERGENT_PUSH_KEY placeholder -> db.push_log 'mocked'). Synthetic records only."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bson import ObjectId  # noqa: E402
from core import db, now_iso  # noqa: E402

TAG = "qa-notif"
run = asyncio.get_event_loop().run_until_complete if False else None


def go(c):
    return asyncio.get_event_loop().run_until_complete(c)


@pytest.fixture
def env(monkeypatch):
    import server
    import push

    async def unlocked():  # these tests cover delivery rules for all users; the lock has its own test
        return {"enabled": False, "allowed": push.PUSH_TEST_EMAILS}

    monkeypatch.setattr(push, "push_lock_state", unlocked)
    ids = {}
    for role in ("installer", "employee"):
        for i in (1, 2):
            ids[f"{role}{i}"] = str(go(db.users.insert_one({"email": f"{TAG}.{role}{i}@example.com", "name": f"QA {role} {i}", "role": role, "active": True,
                                                        "push_active": True, "qa": TAG, "created_at": now_iso()})).inserted_id)
    pid = str(go(db.projects.insert_one({"ref": "QA-NOTIF-1", "address": "73 Beech Road, Luton", "postcode": "LU1 1AA", "status": "active", "qa": TAG,
                                          "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)
    ncs = [str(go(db.ncs.insert_one({"project_id": pid, "number": f"NC-0{i}", "status": "open", "assigned_installer_id": None, "qa": TAG})).inserted_id) for i in (1, 2, 3)]
    yield server, ids, pid, ncs
    go(db.users.delete_many({"qa": TAG}))
    go(db.projects.delete_many({"qa": TAG}))
    go(db.ncs.delete_many({"qa": TAG}))
    go(db.notifications.delete_many({"project_id": pid}))
    go(db.push_log.delete_many({"user_id": {"$in": list(ids.values())}}))
    go(db.reminder_runs.delete_many({"_id": {"$regex": "^(installer|employee):2099"}}))


def logs(uid):
    go(asyncio.sleep(0.3))  # push_for runs as a task
    return go(db.push_log.find({"user_id": uid}).sort("at", 1).to_list(50))


def test_assignment_reassignment_and_no_duplicates(env):
    server, ids, pid, ncs = env
    i1, i2 = ids["installer1"], ids["installer2"]
    go(db.ncs.update_one({"_id": ObjectId(ncs[0])}, {"$set": {"assigned_installer_id": i1}}))
    go(server.nc_notify(go(db.ncs.find_one({"_id": ObjectId(ncs[0])})), i1, "assigned"))
    go(server.nc_notify(go(db.ncs.find_one({"_id": ObjectId(ncs[0])})), i1, "assigned"))  # repeated request
    l1 = logs(i1)
    assert len(l1) == 1 and l1[0]["status"] == "mocked"
    assert l1[0]["data"]["title"] == "New Project Assigned" and "73 Beech Road, Luton" in l1[0]["data"]["message"]
    assert l1[0]["data"]["action_url"] == f"/job/{pid}"
    # second NC on the same project (unread notification still there) -> pushes again
    go(db.ncs.update_one({"_id": ObjectId(ncs[1])}, {"$set": {"assigned_installer_id": i1}}))
    go(server.nc_notify(go(db.ncs.find_one({"_id": ObjectId(ncs[1])})), i1, "assigned"))
    assert len(logs(i1)) == 2
    # reassignment to installer 2
    go(db.ncs.update_one({"_id": ObjectId(ncs[0])}, {"$set": {"assigned_installer_id": i2}}))
    go(server.nc_notify(go(db.ncs.find_one({"_id": ObjectId(ncs[0])})), i2, "assigned"))
    assert len(logs(i2)) == 1


def test_in_review_pushes_employees_and_skips_unregistered(env):
    server, ids, pid, ncs = env
    go(db.users.update_one({"_id": ObjectId(ids["employee2"])}, {"$set": {"push_active": False}}))
    go(db.ncs.update_one({"_id": ObjectId(ncs[0])}, {"$set": {"status": "employee_review"}}))
    go(server.notify_reviewers(ncs[0]))
    l = logs(ids["employee1"])
    assert l and l[-1]["status"] == "mocked" and l[-1]["data"]["title"] == "Project Awaiting Review"
    assert l[-1]["data"]["message"] == "73 Beech Road, Luton is ready for review." and l[-1]["data"]["action_url"] == f"/project/{pid}"
    assert logs(ids["employee2"])[-1]["status"] == "skipped"
    go(server.notify_reviewers(ncs[0]))  # same state again -> no duplicate
    assert len([x for x in logs(ids["employee1"]) if x["kind"] == "review"]) == 1


def test_reminders_grouped_idempotent(env):
    server, ids, pid, ncs = env
    i1 = ids["installer1"]
    go(db.ncs.update_many({"_id": {"$in": [ObjectId(x) for x in ncs]}}, {"$set": {"assigned_installer_id": i1}}))
    go(db.ncs.update_one({"_id": ObjectId(ncs[2])}, {"$set": {"status": "approved"}}))  # completed work excluded
    go(db.ncs.update_one({"_id": ObjectId(ncs[1])}, {"$set": {"status": "employee_review"}}))  # awaiting review excluded
    r = go(server.run_reminders("installer", "2099-01-01T09"))
    assert r["sent"] >= 1
    mine = [x for x in logs(i1) if x["kind"] == "reminder"]
    assert len(mine) == 1 and mine[0]["data"]["message"] == "You have 1 NC outstanding across 1 project." and mine[0]["data"]["action_url"] == "/jobs"
    assert go(server.run_reminders("installer", "2099-01-01T09")) == {"skipped": "already ran"}
    assert len([x for x in logs(i1) if x["kind"] == "reminder"]) == 1
    go(server.run_reminders("employee", "2099-01-01T09"))
    e = [x for x in logs(ids["employee1"]) if x["kind"] == "reminder"]
    assert len(e) == 1 and e[0]["data"]["title"] == "Projects Awaiting Review" and e[0]["data"]["action_url"] == "/review"


def test_london_slots_gmt_and_bst():
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    winter = datetime(2026, 1, 15, 9, 5, tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/London"))
    summer = datetime(2026, 7, 15, 8, 5, tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/London"))
    assert winter.hour == 9 and summer.hour == 9  # 09:00 UK = 09:00 UTC in winter, 08:00 UTC in summer


def test_unassigned_creation_sends_nothing(env):
    server, ids, pid, ncs = env
    go(server.notify(None, "assigned", pid))
    assert go(db.push_log.count_documents({"key": {"$regex": pid}})) == 0


def test_push_test_lock_blocks_all_but_test_account(monkeypatch):
    """Lock is ON by default: only the test account gets remote pushes; other users are logged as skipped."""
    import asyncio
    import push
    from core import db
    loop = asyncio.get_event_loop()
    s = loop.run_until_complete(db.settings.find_one({"_id": "global"}, {"push_test_lock": 1})) or {}
    loop.run_until_complete(db.settings.update_one({"_id": "global"}, {"$unset": {"push_test_lock": 1}}))
    monkeypatch.setattr(push, "_mocked", lambda: True)
    ids = []
    try:
        for email in ("qa-lock-other@example.com", "demo@networksurveyltd.uk"):
            r = loop.run_until_complete(db.users.insert_one({"email": email + ".qa-lock", "role": "installer", "active": True, "push_active": True}))
            ids.append(str(r.inserted_id))
        loop.run_until_complete(db.users.update_one({"_id": __import__("bson").ObjectId(ids[1])}, {"$set": {"email": "DEMO@networksurveyltd.uk"}}))
        assert loop.run_until_complete(push.push_lock_state())["enabled"] is True
        for i, uid in enumerate(ids):
            loop.run_until_complete(push.push_for(uid, "assigned", f"qa-lock:{i}"))
        logs = {x["user_id"]: x for x in loop.run_until_complete(db.push_log.find({"user_id": {"$in": ids}}).to_list(10))}
        assert logs[ids[0]]["status"] == "skipped" and logs[ids[0]]["reason"] == "push test lock"
        assert logs[ids[1]]["status"] == "mocked"
    finally:
        loop.run_until_complete(db.users.delete_many({"_id": {"$in": [__import__("bson").ObjectId(i) for i in ids]}}))
        loop.run_until_complete(db.push_log.delete_many({"user_id": {"$in": ids}}))
        if "push_test_lock" in s:
            loop.run_until_complete(db.settings.update_one({"_id": "global"}, {"$set": {"push_test_lock": s["push_test_lock"]}}))
