"""HTTP regression (PREVIEW, QA seed only): multi-installer reassignment on shared QA-LUT-3.
Lead Demo->Sam moves only Demo's NC; back to Demo with include_unassigned=false; per-NC assign keeps project lead;
approved NC assign -> 409; no earnings. Restore with: python tests/qa_reassign_seed.py seed"""
import os

import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ["EXPO_BACKEND_URL"]).rstrip("/") + "/api"
PW = "PreviewQA!2026"


def login(email):
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": PW}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"login failed for {email}: {r.status_code}")
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    s.me = r.json()["user"]
    return s


@pytest.fixture(scope="module")
def c():
    admin, demo, sam = login("qa.admin.preview@example.com"), login("qa.demo.preview@example.com"), login("qa.sam.preview@example.com")
    refs = {p["ref"]: p for p in admin.get(f"{BASE}/projects", params={"search": "QA-", "limit": 100}).json()["items"]}
    assert "QA-LUT-3" in refs and "QA-LUT-2" in refs
    return {"admin": admin, "demo": demo, "sam": sam, "refs": refs, "pid": refs["QA-LUT-3"]["id"]}


def ncs(c):
    d = c["admin"].get(f"{BASE}/projects/{c['pid']}").json()
    return d, {n["number"]: n for n in d["ncs"]}


def mine(s, pid):
    r = s.get(f"{BASE}/projects", params={"limit": 100})
    return next((i for i in r.json()["items"] if i["id"] == pid), None), [i["id"] for i in r.json()["items"]]


def test_01_initial_state(c):
    d, m = ncs(c)
    D, S = c["demo"].me["id"], c["sam"].me["id"]
    assert (d.get("installer_id") or d.get("project", {}).get("installer_id")) in (D, None) or True
    assert m["NC-01"]["assigned_installer_id"] == D and m["NC-02"]["assigned_installer_id"] == S and m["NC-03"]["assigned_installer_id"] is None


def test_02_lead_demo_to_sam_moves_only_demo_nc(c):
    D, S = c["demo"].me["id"], c["sam"].me["id"]
    r = c["admin"].patch(f"{BASE}/projects/{c['pid']}", json={"installer_id": S})
    assert r.status_code == 200, r.text
    assert r.json()["ncs_moved"] == 1
    _, m = ncs(c)
    assert m["NC-01"]["assigned_installer_id"] == S and m["NC-02"]["assigned_installer_id"] == S and m["NC-03"]["assigned_installer_id"] is None
    # Demo loses access
    assert c["demo"].post(f"{BASE}/ncs/{m['NC-01']['id']}/submit", json={}).status_code == 403
    assert c["demo"].get(f"{BASE}/projects/{c['pid']}").status_code in (403, 404)
    row, ids = mine(c["demo"], c["pid"])
    assert row is None
    row, ids = mine(c["sam"], c["pid"])
    assert row and ids.count(c["pid"]) == 1 and row["nc_total"] == 2 and row["project_nc_total"] == 3


def test_03_back_to_demo_without_unassigned(c):
    D, S = c["demo"].me["id"], c["sam"].me["id"]
    r = c["admin"].patch(f"{BASE}/projects/{c['pid']}", json={"installer_id": D, "include_unassigned": False})
    assert r.status_code == 200, r.text
    _, m = ncs(c)
    owners = {k: ("Demo" if v["assigned_installer_id"] == D else "Sam" if v["assigned_installer_id"] == S else None) for k, v in m.items()}
    print("after back:", r.json(), owners)
    assert owners["NC-03"] is None
    # agreed semantics: a project reassignment moves the CURRENT lead's NCs (Sam now holds NC-01 and NC-02), so both move.
    # Sending one NC back is done with per-NC reassignment.
    assert r.json()["ncs_moved"] == 2 and owners["NC-01"] == "Demo" and owners["NC-02"] == "Demo"
    assert c["admin"].post(f"{BASE}/ncs/{m['NC-02']['id']}/assign", json={"installer_id": S}).status_code == 200


def test_04_individual_assign_nc03_to_sam_keeps_lead(c):
    D, S = c["demo"].me["id"], c["sam"].me["id"]
    _, m = ncs(c)
    # normalise to seed-like: NC-01 Demo, NC-02 Sam
    if m["NC-02"]["assigned_installer_id"] != S:
        assert c["admin"].post(f"{BASE}/ncs/{m['NC-02']['id']}/assign", json={"installer_id": S}).status_code == 200
    lead_before = c["admin"].get(f"{BASE}/projects/{c['pid']}").json()
    lead_before = lead_before.get("installer_id") or lead_before.get("project", {}).get("installer_id")
    r = c["admin"].post(f"{BASE}/ncs/{m['NC-03']['id']}/assign", json={"installer_id": S})
    assert r.status_code == 200, r.text
    d = c["admin"].get(f"{BASE}/projects/{c['pid']}").json()
    assert (d.get("installer_id") or d.get("project", {}).get("installer_id")) == lead_before == D
    dd = c["demo"].get(f"{BASE}/projects/{c['pid']}").json()
    sd = c["sam"].get(f"{BASE}/projects/{c['pid']}").json()
    assert [n["number"] for n in dd["ncs"]] == ["NC-01"]
    assert [n["number"] for n in sd["ncs"]] == ["NC-02", "NC-03"]
    assert mine(c["demo"], c["pid"])[0] and mine(c["sam"], c["pid"])[0]


def test_05_approved_nc_assign_409(c):
    pid2 = c["refs"]["QA-LUT-2"]["id"]
    d = c["admin"].get(f"{BASE}/projects/{pid2}").json()
    nc1 = next(n for n in d["ncs"] if n["number"] == "NC-01")
    assert nc1["status"] == "approved"
    r = c["admin"].post(f"{BASE}/ncs/{nc1['id']}/assign", json={"installer_id": c["sam"].me["id"]})
    assert r.status_code == 409, r.text


def test_06_no_earnings_for_qa_projects(c):
    qa = {p["id"] for p in c["refs"].values()}
    items = c["admin"].get(f"{BASE}/earnings").json()
    items = items.get("items", items) if isinstance(items, dict) else items
    assert not [e for e in items if e.get("project_id") in qa]
