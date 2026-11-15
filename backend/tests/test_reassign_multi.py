"""Multi-installer projects: project reassignment moves only the previous installer's NCs; others and unassigned stay;
per-NC reassignment; approved NCs locked; reassignment never creates earnings. Synthetic preview records; push mocked."""
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
    for name, role in (("A", "installer"), ("B", "installer"), ("C", "installer"), ("Admin", "admin")):
        r = go(db.users.insert_one({"email": f"qa-multi-{name.lower()}-{tag}@example.com", "role": role, "active": True, "name": f"QA {name}",
                                    "token_version": 0, "pay_type": "per_job"}))
        users[name] = User(id=str(r.inserted_id), email=f"qa-{name}@example.com", name=f"QA {name}", role=role)
    pid = str(go(db.projects.insert_one({"ref": f"QA-MULTI-{tag}", "address": "1 High St, Luton", "postcode": "LU1 1AA", "installer_id": users["A"].id,
                                         "status": "in_progress", "deleted": None, "rate": 150, "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)
    plan = [("A", "approved"), ("A", "open"), ("C", "open"), (None, "open")]
    nids = []
    for i, (who, st) in enumerate(plan, 1):
        nids.append(str(go(db.ncs.insert_one({"project_id": pid, "number": f"NC-{i:02d}", "description": "x", "required_work": "", "status": st,
                                                "assigned_installer_id": users[who].id if who else None, "comments": [{"text": "c"}], "assignment_history": []})).inserted_id))
    yield users, pid, nids
    go(db.ncs.delete_many({"project_id": pid}))
    go(db.notifications.delete_many({"project_id": pid}))
    go(db.earnings.delete_many({"project_id": pid}))
    go(db.projects.delete_one({"_id": ObjectId(pid)}))
    go(db.users.delete_many({"email": {"$regex": f"qa-multi-.*-{tag}"}}))


def owners(pid):
    return [n["assigned_installer_id"] for n in go(db.ncs.find({"project_id": pid}).sort("number", 1).to_list(10))]


def visible(server, pid, u):
    return pid in {i["id"] for i in go(server.list_projects(u=u, limit=100))["items"]}


def test_project_reassign_moves_only_previous_installers_ncs(env):
    import server
    users, pid, nids = env
    A, B, C, adm = users["A"], users["B"], users["C"], users["Admin"]
    r = go(server.patch_project(pid, server.ProjectPatch(installer_id=B.id), u=adm))
    assert r["ncs_moved"] == 1
    assert owners(pid) == [A.id, B.id, C.id, None]  # approved NC stays with A; C's NC and the unassigned NC unchanged
    assert visible(server, pid, A) and visible(server, pid, B) and visible(server, pid, C)
    assert [n["number"] for n in go(server.project_detail(pid, u=A))["ncs"]] == ["NC-01"]  # A keeps only completed work
    with pytest.raises(HTTPException) as e:
        go(server.get_nc(nids[1], A))
    assert e.value.status_code == 403
    assert [n["number"] for n in go(server.project_detail(pid, u=C))["ncs"]] == ["NC-03"]  # C sees only their own NC
    assert [n["number"] for n in go(server.project_detail(pid, u=B))["ncs"]] == ["NC-02"]
    statuses = [n["status"] for n in go(db.ncs.find({"project_id": pid}).sort("number", 1).to_list(10))]
    assert statuses == ["approved", "open", "open", "open"]
    assert go(db.earnings.count_documents({"project_id": pid})) == 0  # reassignment alone pays nobody
    row = next(i for i in go(server.list_projects(u=B, limit=100))["items"] if i["id"] == pid)
    assert (row["nc_total"], row["project_nc_total"]) == (1, 4)  # own NCs vs whole project, one row per project
    a = go(db.audit.find_one({"target": f"project:{pid}", "event": "project.reassigned"}, sort=[("seq", -1)]))
    assert a["data"]["from"] == A.id and a["data"]["to"] == B.id and a["data"]["ncs_moved"] == 1 and a["actor_id"] == adm.id


def test_include_unassigned_only_when_chosen(env):
    import server
    users, pid, _ = env
    go(server.patch_project(pid, server.ProjectPatch(installer_id=users["B"].id, include_unassigned=True), u=users["Admin"]))
    assert owners(pid) == [users["A"].id, users["B"].id, users["C"].id, users["B"].id]


def test_individual_nc_reassign_and_approved_locked(env):
    import server
    users, pid, nids = env
    go(server.assign_nc(nids[1], server.AssignIn(installer_id=users["C"].id), u=users["Admin"]))
    assert owners(pid) == [users["A"].id, users["C"].id, users["C"].id, None]
    p = go(db.projects.find_one({"_id": ObjectId(pid)}))
    assert p["installer_id"] == users["A"].id  # project assignment unchanged
    assert visible(server, pid, users["A"]) and visible(server, pid, users["C"])  # both work on the project
    with pytest.raises(HTTPException) as e:
        go(server.assign_nc(nids[0], server.AssignIn(installer_id=users["B"].id), u=users["Admin"]))
    assert e.value.status_code == 409
    assert go(db.earnings.count_documents({"project_id": pid})) == 0
