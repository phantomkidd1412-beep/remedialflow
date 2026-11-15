"""Probe (iteration 53): project reassignment moves APPROVED NCs; can a later approval pay the new lead for work already paid?
Synthetic records only; push mocked."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bson import ObjectId  # noqa: E402

from core import User, db, now_iso  # noqa: E402


def go(c):
    return asyncio.get_event_loop().run_until_complete(c)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("PUSH_MOCK", "true")
    tag = str(ObjectId())
    users = {}
    for name, role in (("A", "installer"), ("B", "installer"), ("C", "installer"), ("Admin", "admin")):
        r = go(db.users.insert_one({"email": f"qa-dp-{name.lower()}-{tag}@example.com", "role": role, "active": True, "name": f"QA {name}",
                                    "token_version": 0, "pay_type": "per_job"}))
        users[name] = User(id=str(r.inserted_id), email=f"qa-{name}@example.com", name=f"QA {name}", role=role)
    pid = str(go(db.projects.insert_one({"ref": f"QA-DP-{tag}", "address": "1 High St, Luton", "postcode": "LU1 1AA", "installer_id": users["A"].id,
                                         "status": "in_progress", "deleted": None, "rate": 150, "created_at": now_iso(), "updated_at": now_iso()})).inserted_id)
    nids = []
    for i, who in enumerate(("A", "A", "C"), 1):
        nids.append(str(go(db.ncs.insert_one({"project_id": pid, "number": f"NC-{i:02d}", "description": "x", "required_work": "", "status": "employee_review",
                                                "assigned_installer_id": users[who].id, "comments": [], "assignment_history": []})).inserted_id))
    yield users, pid, nids
    go(db.ncs.delete_many({"project_id": pid}))
    go(db.notifications.delete_many({"project_id": pid}))
    go(db.earnings.delete_many({"project_id": pid}))
    go(db.projects.delete_one({"_id": ObjectId(pid)}))
    go(db.users.delete_many({"email": {"$regex": f"qa-dp-.*-{tag}"}}))


def test_reassign_after_payment_does_not_double_pay(env):
    import server
    users, pid, nids = env
    adm = users["Admin"]
    for n in nids[:2]:
        go(server.review(n, server.ReviewIn(action="approve"), u=adm))
    assert go(db.earnings.count_documents({"project_id": pid, "installer_id": users["A"].id})) == 1  # A paid for NC-01/02
    r = go(server.patch_project(pid, server.ProjectPatch(installer_id=users["B"].id), u=adm))
    print("ncs_moved", r["ncs_moved"])
    go(server.review(nids[2], server.ReviewIn(action="approve"), u=adm))  # C's NC approved -> settle runs
    paid = [(e["installer_id"], e["amount"]) for e in go(db.earnings.find({"project_id": pid}).to_list(10))]
    print("earnings", paid, "A", users["A"].id, "B", users["B"].id)
    assert not any(i == users["B"].id for i, _ in paid), "B earned for NCs A already did and was paid for"
