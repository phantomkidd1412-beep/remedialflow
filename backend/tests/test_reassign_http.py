"""HTTP regression: project reassignment (QA seed: qa_reassign_seed.py). PREVIEW only, QA data only.
Demo -> Sam -> Demo on QA-LUT-1; access control; audit; 400/409/notification hiding. Restores original assignments."""
import os

import pytest
import requests

BASE = os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ["EXPO_BACKEND_URL"]
BASE = BASE.rstrip("/") + "/api"
PW = "PreviewQA!2026"


def login(email):
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": PW}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"login failed for {email}: {r.status_code} {r.text}")
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    s.me = r.json()["user"]
    return s


@pytest.fixture(scope="module")
def ctx():
    admin, demo, sam = login("qa.admin.preview@example.com"), login("qa.demo.preview@example.com"), login("qa.sam.preview@example.com")
    refs = {p["ref"]: p for p in admin.get(f"{BASE}/projects", params={"search": "QA-", "limit": 100}).json()["items"]}
    for r in ("QA-LUT-1", "QA-LUT-2", "QA-BED-1", "QA-STH-1", "QA-BED-2"):
        assert r in refs, f"seed missing {r}"
    c = {"admin": admin, "demo": demo, "sam": sam, "refs": refs}
    yield c
    # restore original assignments
    for r in ("QA-LUT-1", "QA-LUT-2"):
        admin.patch(f"{BASE}/projects/{refs[r]['id']}", json={"installer_id": demo.me["id"]})
    admin.patch(f"{BASE}/projects/{refs['QA-BED-2']['id']}", json={"installer_id": sam.me["id"]})


def ids(s):
    return {p["id"] for p in s.get(f"{BASE}/projects", params={"limit": 100}).json()["items"]}


def test_baseline(ctx):
    pid = ctx["refs"]["QA-LUT-1"]["id"]
    assert pid in ids(ctx["demo"]) and pid not in ids(ctx["sam"])


def test_reassign_to_sam(ctx):
    a, demo, sam = ctx["admin"], ctx["demo"], ctx["sam"]
    pid = ctx["refs"]["QA-LUT-1"]["id"]
    before = a.get(f"{BASE}/projects/{pid}").json()
    before_st = {n["number"]: n["status"] for n in before["ncs"]}
    r = a.patch(f"{BASE}/projects/{pid}", json={"installer_id": sam.me["id"]})
    assert r.status_code == 200, r.text
    assert pid not in ids(demo)
    assert demo.get(f"{BASE}/projects/{pid}").status_code == 403
    nc0 = before["ncs"][0]["id"]
    sub = demo.post(f"{BASE}/ncs/{nc0}/submit", json={})
    assert sub.status_code == 403, sub.text
    assert pid in ids(sam)
    d = sam.get(f"{BASE}/projects/{pid}")
    assert d.status_code == 200
    ncs = d.json()["ncs"]
    assert len(ncs) == 3
    assert {n["number"]: n["status"] for n in ncs} == before_st
    assert all(n["assigned_installer_id"] == sam.me["id"] for n in ncs)
    aud = a.get(f"{BASE}/projects/{pid}/audit").json()
    ev = [x for x in aud if x["event"] == "project.reassigned"]
    assert ev and ev[0]["data"]["from"] == demo.me["id"] and ev[0]["data"]["to"] == sam.me["id"] and ev[0]["data"]["ncs_moved"] == 3
    assert ev[0]["data"]["by"] == a.me["id"] and ev[0]["data"]["at"]


def test_sam_own_project_unaffected(ctx):
    pid = ctx["refs"]["QA-BED-2"]["id"]
    d = ctx["sam"].get(f"{BASE}/projects/{pid}").json()
    assert len(d["ncs"]) == 2 and all(n["assigned_installer_id"] == ctx["sam"].me["id"] for n in d["ncs"])
    assert ctx["demo"].get(f"{BASE}/projects/{pid}").status_code == 403


def test_demo_notifications_hidden(ctx):
    pid = ctx["refs"]["QA-LUT-1"]["id"]
    items = ctx["demo"].get(f"{BASE}/notifications").json()["items"]
    assert not [i for i in items if i.get("project_id") == pid]


def test_non_installer_target_400(ctx):
    pid = ctx["refs"]["QA-LUT-1"]["id"]
    r = ctx["admin"].patch(f"{BASE}/projects/{pid}", json={"installer_id": ctx["admin"].me["id"]})
    assert r.status_code == 400
    r2 = ctx["admin"].patch(f"{BASE}/projects/{pid}", json={"installer_id": "notanid"})
    assert r2.status_code in (400, 404, 422), r2.status_code


def test_installer_cannot_patch(ctx):
    pid = ctx["refs"]["QA-LUT-1"]["id"]
    r = ctx["demo"].patch(f"{BASE}/projects/{pid}", json={"installer_id": ctx["demo"].me["id"]})
    assert r.status_code == 403


def test_reassign_back_to_demo(ctx):
    a, demo, sam = ctx["admin"], ctx["demo"], ctx["sam"]
    pid = ctx["refs"]["QA-LUT-1"]["id"]
    assert a.patch(f"{BASE}/projects/{pid}", json={"installer_id": demo.me["id"]}).status_code == 200
    assert pid in ids(demo) and pid not in ids(sam)
    assert sam.get(f"{BASE}/projects/{pid}").status_code == 403
    d = demo.get(f"{BASE}/projects/{pid}").json()
    assert len(d["ncs"]) == 3 and all(n["assigned_installer_id"] == demo.me["id"] for n in d["ncs"])
    # notification for Demo re-assigned is visible again (new 'assigned')
    items = demo.get(f"{BASE}/notifications").json()["items"]
    assert [i for i in items if i.get("project_id") == pid]
