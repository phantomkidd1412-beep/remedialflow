"""Location-Aware Evidence master switch: admin-only, sets all five flags together, audited, not reset by 'Save settings'.
In-process endpoint calls with temporary QA users (no AI, no Dropbox). Flags restored to OFF."""
import sys

import pytest
from bson import ObjectId
from fastapi import HTTPException

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")
from _loop import LOOP  # noqa: E402


def run(c):
    return LOOP.run_until_complete(c)


@pytest.fixture(scope="module")
def env():
    import server
    from core import User, db, now_iso
    users = {}
    for role in ("admin", "employee"):
        d = {"email": f"qa.le.{role}@example.com", "name": f"QA LE {role}", "role": role, "active": True, "password_hash": "x", "token_version": 0, "created_at": now_iso()}
        d["_id"] = run(db.users.insert_one(d)).inserted_id
        users[role] = User.from_mongo(d)
    yield server, db, users
    run(db.settings.update_one({"_id": "global"}, {"$set": {k: False for k in server.LOCATION_FLAGS}}))
    run(db.users.delete_many({"_id": {"$in": [ObjectId(u.id) for u in users.values()]}}))


def test_default_off_and_admin_only(env):
    server, db, users = env
    run(db.settings.update_one({"_id": "global"}, {"$set": {k: False for k in server.LOCATION_FLAGS}}))
    assert run(server.read_location_evidence(users["admin"]))["state"] == "off"
    from auth import require
    with pytest.raises(HTTPException) as e:  # the route dependency rejects non-admins
        run(require("admin")(users["employee"]))
    assert e.value.status_code == 403


def test_on_off_sets_all_five_and_audits(env):
    server, db, users = env
    r = run(server.write_location_evidence(server.LocationEvidenceIn(enabled=True), users["admin"]))
    assert r["state"] == "on" and all(r["flags"].values())
    s = run(server.get_settings())
    assert all(getattr(s, k) for k in server.LOCATION_FLAGS)
    r = run(server.write_location_evidence(server.LocationEvidenceIn(enabled=False), users["admin"]))
    assert r["state"] == "off" and not any(r["flags"].values())
    ev = [a["event"] for a in run(db.audit.find({"actor_id": users["admin"].id}).sort("_id", 1).to_list(10))]
    assert ev[-2:] == ["settings.location_evidence_enabled", "settings.location_evidence_disabled"]


def test_partial_state_reported(env):
    server, db, users = env
    run(db.settings.update_one({"_id": "global"}, {"$set": {"smart_pdf_enabled": True}}))
    assert run(server.read_location_evidence(users["admin"]))["state"] == "partial"
    run(db.settings.update_one({"_id": "global"}, {"$set": {"smart_pdf_enabled": False}}))


def test_save_settings_does_not_change_switch(env):
    server, db, users = env
    from core import Settings
    run(server.write_location_evidence(server.LocationEvidenceIn(enabled=True), users["admin"]))
    cur = run(server.get_settings()).model_dump()
    stale = Settings(**{**cur, **{k: False for k in server.LOCATION_FLAGS}})  # e.g. a form loaded before the switch was flipped
    run(server.write_settings(stale, users["admin"]))
    assert run(server.read_location_evidence(users["admin"]))["state"] == "on"
    run(server.write_location_evidence(server.LocationEvidenceIn(enabled=False), users["admin"]))
    off = Settings(**{**cur, **{k: True for k in server.LOCATION_FLAGS}})
    run(server.write_settings(off, users["admin"]))
    assert run(server.read_location_evidence(users["admin"]))["state"] == "off"
