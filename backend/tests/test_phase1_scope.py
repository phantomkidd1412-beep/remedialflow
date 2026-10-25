"""Phase 1 location-aware scope: unit tests (rules, merge, coverage, gate) + API tests against the preview backend.
Creates only QA data (qa.scope.*@example.com, address 'QA TEST 1 Example Road') and removes it afterwards."""
import asyncio
import os
import sys

import pytest
import requests
from bson import ObjectId

sys.path.insert(0, "/app/backend")
from scope import coverage, gate_reason, merge, rule_extract  # noqa: E402

BASE = os.environ.get("RF_BASE", "http://localhost:8001")


def sc(t):
    return merge(rule_extract(t), None, t, None)


@pytest.mark.parametrize("text,stype,count,status", [
    ("End caps missing to all external window sills.", "window", None, "uncertain"),
    ("End caps missing to 8 no. external window sills (W1-W8).", "window", 8, "confirmed"),
    ("Window sills W1 to W6 have no end caps fitted.", "window", 6, "confirmed"),
    ("Background ventilation not provided in all habitable rooms.", "habitable_room", None, "uncertain"),
    ("No trickle vents in bedroom 1, bedroom 2 and the living room.", "habitable_room", 3, "confirmed"),
    ("Extractor fans not installed in the bathroom and kitchen.", "wet_room", 2, "confirmed"),
    ("EWI not continuous at the front and rear elevations.", "elevation", 2, "confirmed"),
    ("Gaps at roof and wall junctions at the eaves.", "junction", None, "uncertain"),
    ("Insulation continuity not maintained around the external wall bracket.", "component", 1, "confirmed"),
    ("Trickle vents missing in three bedrooms.", "habitable_room", 3, "confirmed"),
])
def test_rules(text, stype, count, status):
    s = sc(text)
    assert (s["scope_type"], s["expected_count"], s["scope_status"]) == (stype, count, status)


def test_coverage_states():
    s8 = sc("End caps missing to 8 no. external window sills (W1-W8).")
    six = [{"kind": "photo", "status": "submitted", "location_id": f"loc-{i}"} for i in range(1, 7)]
    c = coverage(s8, six)
    assert c["status"] == "incomplete" and c["missing"] == ["Window 7", "Window 8"]
    eight = [{"kind": "photo", "status": "submitted", "location_id": f"loc-{i}"} for i in range(1, 9)]
    assert coverage(s8, eight)["status"] == "complete"
    one = [{"kind": "photo", "status": "submitted"}]  # one representative photo must never complete a multi-location NC
    assert coverage(s8, one)["status"] == "unverified"
    assert coverage(sc("Background ventilation not provided in all habitable rooms."), one)["status"] == "unverified"
    single = sc("Insulation continuity not maintained around the external wall bracket.")
    assert coverage(single, one)["status"] == "complete"


def test_gate_never_rejects_only_blocks_auto():
    s8 = sc("End caps missing to 8 no. external window sills (W1-W8).")
    r = gate_reason(s8, coverage(s8, [{"kind": "photo", "status": "submitted"}]))
    assert r and r.startswith("Location coverage unverified")
    single = sc("Insulation continuity not maintained around the external wall bracket.")
    assert gate_reason(single, coverage(single, [{"kind": "photo", "status": "submitted"}])) is None
    assert gate_reason(None, coverage(None, [])) is None  # feature off / no scope => no effect


def test_ai_quote_must_exist_in_text():
    t = "End caps missing to all external window sills."
    fake_ai = {"scope_type": "window", "expected_count": 8, "locations": [], "basis": ["eight windows on the front"], "confidence": 0.9}
    assert merge(rule_extract(t), fake_ai, t, None)["scope_status"] == "uncertain"  # invented quote => not confirmed
    t2 = t + " Report: windows W1 W2 W3 W4 affected (4 windows)."
    ok_ai = {"scope_type": "window", "expected_count": 4, "locations": ["W1", "W2", "W3", "W4"], "basis": ["4 windows"], "confidence": 0.9}
    m = merge(rule_extract(t), ok_ai, t2, None)
    assert m["scope_status"] == "confirmed" and m["expected_count"] == 4 and m["locations"][0]["source"] == "ai"


# ---------------- API (preview backend) ----------------
@pytest.fixture(scope="module")
def qa():
    from auth import ph
    from core import db, now_iso
    loop = asyncio.new_event_loop()
    pw = "QaScope-" + ObjectId().__str__()[-8:]

    async def setup():
        ids = {}
        for role in ("admin", "employee", "installer"):
            r = await db.users.insert_one({"email": f"qa.scope.{role}@example.com", "name": f"QA {role}", "role": role, "active": True,
                                           "password_hash": ph.hash(pw), "token_version": 0, "created_at": now_iso()})
            ids[role] = str(r.inserted_id)
        p = await db.projects.insert_one({"ref": "QA-SCOPE-1", "address": "QA TEST 1 Example Road", "postcode": "QA1 1AA", "installer_id": ids["installer"],
                                          "rate": 0, "status": "active", "require_video": False, "deleted": None, "created_at": now_iso(), "updated_at": now_iso()})
        n = await db.ncs.insert_one({"project_id": str(p.inserted_id), "number": "NC-01", "description": "End caps missing to 8 no. external window sills (W1-W8).",
                                     "required_work": "", "original_text": "End caps missing to 8 no. external window sills (W1-W8).", "status": "open",
                                     "assigned_installer_id": ids["installer"], "updated_at": now_iso()})
        old = (await db.settings.find_one({"_id": "global"}) or {}).get("location_scope_enabled", False)
        return ids, str(p.inserted_id), str(n.inserted_id), old

    ids, pid, nid, old = loop.run_until_complete(setup())
    yield {"pw": pw, "pid": pid, "nid": nid, "ids": ids}

    async def teardown():
        await db.settings.update_one({"_id": "global"}, {"$set": {"location_scope_enabled": old}})
        await db.ncs.delete_many({"project_id": pid})
        await db.projects.delete_one({"_id": ObjectId(pid)})
        await db.sessions.delete_many({"user_id": {"$in": list(ids.values())}})
        await db.users.delete_many({"email": {"$regex": r"^qa\.scope\."}})
    loop.run_until_complete(teardown())


def login(role, pw):
    r = requests.post(f"{BASE}/api/auth/login", json={"email": f"qa.scope.{role}@example.com", "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def set_flag(h, on):
    s = requests.get(f"{BASE}/api/settings", headers=h).json()
    s["location_scope_enabled"] = on
    assert requests.put(f"{BASE}/api/settings", headers=h, json=s).status_code == 200


def test_api_disabled_by_default_and_gated(qa):
    a, i = login("admin", qa["pw"]), login("installer", qa["pw"])
    set_flag(a, False)
    r = requests.get(f"{BASE}/api/ncs/{qa['nid']}/scope", headers=a).json()
    assert r["enabled"] is False and r["scope"] is None
    assert requests.post(f"{BASE}/api/ncs/{qa['nid']}/scope/extract", headers=a).status_code == 409
    assert requests.get(f"{BASE}/api/ncs/{qa['nid']}/scope", headers=i).status_code == 403  # installers: no access


def test_api_extract_when_enabled(qa):
    a, e = login("admin", qa["pw"]), login("employee", qa["pw"])
    set_flag(a, True)
    r = requests.post(f"{BASE}/api/ncs/{qa['nid']}/scope/extract", headers=e)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["scope"]["expected_count"] == 8 and j["scope"]["scope_status"] == "confirmed" and j["coverage"]["status"] == "unverified"
    pd = requests.get(f"{BASE}/api/projects/{qa['pid']}", headers=e).json()
    assert pd["ncs"][0]["scope"]["expected_count"] == 8
    set_flag(a, False)
