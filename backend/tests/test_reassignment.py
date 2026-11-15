"""Project reassignment Demo -> Sam -> Demo: NC transfer, access control, evidence/history preserved, others untouched.
Synthetic preview records only (cleaned up); push mocked; no AI."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bson import ObjectId  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from core import User, db, now_iso  # noqa: E402


def go(c):
    return asyncio.get_event_loop().run_until_complete(c)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("PUSH_MOCK", "true")
    tag = str(ObjectId())
    users = {}
    for name, role in (("Demo", "installer"), ("Sam", "installer"), ("Other", "installer"), ("Admin", "admin")):
        r = go(db.users.insert_one({"email": f"qa-reassign-{name.lower()}-{tag}@example.com", "role": role, "active": True, "name": f"QA {name}", "token_version": 0}))
        users[name] = User(id=str(r.inserted_id), email=f"qa-{name}@example.com", name=f"QA {name}", role=role)
    pids = []

    def project(ref, installer, statuses):
        pid = str(go(db.projects.insert_one({"ref": f"QA-RA-{ref}-{tag}", "address": "1 High St, Luton", "postcode": "LU1 1AA",
                                              "installer_id": installer, "status": "in_progress", "deleted": None, "rate": 150,
                                              "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)
        pids.append(pid)
        nids = []
        for i, st in enumerate(statuses, 1):
            nids.append(str(go(db.ncs.insert_one({"project_id": pid, "number": f"NC-{i:02d}", "description": "x", "required_work": "", "status": st,
                                                    "assigned_installer_id": installer, "comments": [{"by": installer, "text": "c"}],
                                                    "assignment_history": []})).inserted_id))
        go(db.files.insert_one({"project_id": pid, "nc_id": nids[0], "kind": "photo", "status": "submitted", "uploaded_by": installer, "paths": {}, "source": "in_app_camera", "sha256": "x", "size": 1, "mime": "image/jpeg"}))
        return pid, nids

    yield users, project
    for pid in pids:
        go(db.ncs.delete_many({"project_id": pid}))
        go(db.files.delete_many({"project_id": pid}))
        go(db.notifications.delete_many({"project_id": pid}))
        go(db.push_log.delete_many({"key": {"$regex": pid}}))
        go(db.projects.delete_one({"_id": ObjectId(pid)}))
    go(db.users.delete_many({"email": {"$regex": f"qa-reassign-.*-{tag}"}}))


def ids_for(u):
    import server
    return {i["id"] for i in go(server.list_projects(u=u, limit=100))["items"]}


def test_reassign_demo_to_sam_and_back(env):
    import server
    users, project = env
    demo, sam, other, admin = users["Demo"], users["Sam"], users["Other"], users["Admin"]
    pid, nids = project("A", demo.id, ["employee_review", "rejected", "open"])
    other_pid, _ = project("B", other.id, ["open"])
    assert pid in ids_for(demo) and pid not in ids_for(sam)

    go(server.patch_project(pid, server.ProjectPatch(installer_id=sam.id), u=admin))
    assert pid not in ids_for(demo) and pid in ids_for(sam)
    for call in (lambda: server.get_project(pid, demo), lambda: server.get_nc(nids[1], demo), lambda: server.project_detail(pid, u=demo)):
        with pytest.raises(HTTPException) as e:
            go(call())
        assert e.value.status_code == 403
    d = go(server.project_detail(pid, u=sam))
    assert [n["status"] for n in d["ncs"]] == ["employee_review", "rejected", "open"]  # statuses unchanged, outstanding visible
    assert len(d["ncs"][0]["photos"]) == 1  # evidence intact
    ncs = go(db.ncs.find({"project_id": pid}).to_list(10))
    assert all(n["assigned_installer_id"] == sam.id and n["assignment_history"][-1]["from"] == demo.id and n["comments"] for n in ncs)
    p = go(db.projects.find_one({"_id": ObjectId(pid)}))
    assert p["installer_id"] == sam.id
    a = go(db.audit.find_one({"target": f"project:{pid}", "event": "project.reassigned"}))
    assert a and a["data"]["from"] == demo.id and a["data"]["to"] == sam.id and a["actor_id"] == admin.id and a["at"]
    assert other_pid in ids_for(other)  # other installer untouched
    assert go(db.ncs.find_one({"project_id": other_pid}))["assigned_installer_id"] == other.id

    go(server.patch_project(pid, server.ProjectPatch(installer_id=demo.id), u=admin))
    assert pid in ids_for(demo) and pid not in ids_for(sam)
    with pytest.raises(HTTPException):
        go(server.get_nc(nids[1], sam))
    ncs = go(db.ncs.find({"project_id": pid}).to_list(10))
    assert all(n["assigned_installer_id"] == demo.id and len(n["assignment_history"]) == 2 for n in ncs)
    assert go(db.files.count_documents({"project_id": pid})) == 1


def test_unknown_installer_rejected(env):
    import server
    users, project = env
    pid, _ = project("C", users["Demo"].id, ["open"])
    with pytest.raises(HTTPException) as e:
        go(server.patch_project(pid, server.ProjectPatch(installer_id=users["Admin"].id), u=users["Admin"]))
    assert e.value.status_code == 400
    assert go(db.ncs.find_one({"project_id": pid}))["assigned_installer_id"] == users["Demo"].id
