"""Reassignment edge cases (synthetic records, cleaned): completed -> 409, old installer's notifications hidden, unassign."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bson import ObjectId  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from core import db, now_iso  # noqa: E402
from tests.test_reassignment import env, go  # noqa: E402,F401


def test_completed_project_409(env):  # noqa: F811
    import server
    users, project = env
    pid, _ = project("D", users["Demo"].id, ["approved"])
    go(db.projects.update_one({"_id": ObjectId(pid)}, {"$set": {"status": "completed"}}))
    with pytest.raises(HTTPException) as e:
        go(server.patch_project(pid, server.ProjectPatch(installer_id=users["Sam"].id), u=users["Admin"]))
    assert e.value.status_code == 409
    assert go(db.ncs.find_one({"project_id": pid}))["assigned_installer_id"] == users["Demo"].id
    assert go(db.projects.find_one({"_id": ObjectId(pid)}))["installer_id"] == users["Demo"].id


def test_old_installer_notifications_hidden(env):  # noqa: F811
    import server
    users, project = env
    demo, sam = users["Demo"], users["Sam"]
    pid, _ = project("E", demo.id, ["open", "rejected"])
    go(db.notifications.insert_one({"user_id": demo.id, "project_id": pid, "kind": "assigned", "title": "t", "body": "b",
                                    "summary": "", "read": False, "created_at": now_iso()}))
    go(server.patch_project(pid, server.ProjectPatch(installer_id=sam.id), u=users["Admin"]))
    n = go(db.notifications.find_one({"user_id": demo.id, "project_id": pid}))
    assert n["hidden"] is True
    vis = go(server.notifications(u=demo))["items"]
    assert not [i for i in vis if i.get("project_id") == pid]


def test_unassign_empty_string(env):  # noqa: F811
    import server
    users, project = env
    pid, _ = project("F", users["Demo"].id, ["open"])
    go(server.patch_project(pid, server.ProjectPatch(installer_id=""), u=users["Admin"]))
    assert go(db.ncs.find_one({"project_id": pid}))["assigned_installer_id"] is None
    assert pid not in {i["id"] for i in go(server.list_projects(u=users["Demo"], limit=100))["items"]}
