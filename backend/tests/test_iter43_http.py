"""Iter43 HTTP (public URL): Auto Select pending/AI-suggested semantics + AI call accounting. Requires qa_iter43_seed.py seed + flags_on.
No real AI: NC-01 rules settle all; NC-02 checklist unconfirmed; NC-03 recommendation cache pre-built with a stub."""
import os

import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ["EXPO_BACKEND_URL"]).rstrip("/")
PW = "QaCheck-7731x"


def login(email):
    r = requests.post(f"{BASE}/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {r.json().get('access_token') or r.json().get('token')}"
    return s


@pytest.fixture(scope="module")
def ctx():
    s = login("qa.i43.admin@example.com")
    pid = os.environ.get("QA_PID") or next(p["id"] for p in s.get(f"{BASE}/api/projects", params={"q": "QA-I43"}).json()["items"] if p.get("ref") == "QA-I43")
    p = s.get(f"{BASE}/api/projects/{pid}").json()
    ncs = {n["number"]: n for n in p["ncs"]}
    return {"s": s, "ncs": ncs, "files": {k: [f["id"] for f in sorted(n["photos"], key=lambda x: x.get("server_received_at") or "")] for k, n in ncs.items()}}


def recs(s, nid):
    r = s.get(f"{BASE}/api/ncs/{nid}/recommendations")
    assert r.status_code == 200
    return {i["title"]: i for i in r.json()["items"]}, r.json()


def auto(s, nid, mode="replace"):
    r = s.post(f"{BASE}/api/ncs/{nid}/evidence-selection/auto", json={"mode": mode})
    assert r.status_code == 200, r.text
    return r.json()


def test_health():
    assert requests.get(f"{BASE}/health").status_code in (200, 404)


# Unconfirmed checklist -> stop, no AI, nothing selected
def test_unconfirmed_checklist_stops(ctx):
    r = auto(ctx["s"], ctx["ncs"]["NC-02"]["id"])
    assert r["needs_checklist_confirmation"] is True and r["ai_calls"] == 0 and r["ai_limit"] == 1 and r["ai_used"] is False
    assert r["checklist_status"] == "draft" and r["suggested"] == [] and r["selection"] is None


# Cached AI recs: pending, flags kept, nothing marked, not approved
def test_cached_ai_pending_and_notes(ctx):
    s, nid = ctx["s"], ctx["ncs"]["NC-03"]["id"]
    r = auto(s, nid)
    assert r["ai_calls"] == 0 and r["ai_used"] is False and r["ai_limit"] == 1
    assert sorted(r["suggested"]) == ["Gas tee before work", "Tape showing 250mm", "Vent cover fitted"]
    assert "Gas tee before work: AI is unsure this photo shows the requirement" in r["review_notes"]
    assert "Tape showing 250mm: Measurement/test must be checked by staff on the photo" in r["review_notes"]
    items, raw = recs(s, nid)
    assert {k: (v["state"], v["source"]) for k, v in items.items()} == {"Gas tee before work": ("pending", "ai"), "Tape showing 250mm": ("pending", "rule"),
                                                                         "Vent cover fitted": ("pending", "rule")}
    assert any("unsure" in x for x in items["Gas tee before work"]["flags"]) and any("Measurement" in x for x in items["Tape showing 250mm"]["flags"])
    assert all(p["checklist_item"] is None for p in raw["photos"])
    n = next(n for n in s.get(f"{BASE}/api/projects/{ctx['ncs']['NC-03']['project_id']}").json()["ncs"] if n["id"] == nid) if ctx["ncs"]["NC-03"].get("project_id") else None
    if n:
        assert n["status"] == "employee_review"


# Gallery deselect of AI pick -> learning deselect_ai + rec rejected; Add does not re-add
def test_deselect_ai_pick(ctx):
    s, nid, f = ctx["s"], ctx["ncs"]["NC-03"]["id"], ctx["files"]["NC-03"]
    items, _ = recs(s, nid)
    ai_pick = items["Gas tee before work"]["file_id"]
    sel = auto(s, nid)["selection"]
    keep = [i for i in sel["ids"] if i != ai_pick]
    r = s.put(f"{BASE}/api/ncs/{nid}/evidence-selection", json={"ids": keep, "seq": sel["seq"] + 1})
    assert r.status_code == 200 and r.json()["ids"] == keep
    items, _ = recs(s, nid)
    assert items["Gas tee before work"]["state"] == "rejected" and items["Gas tee before work"]["file_id"] is None
    r2 = auto(s, nid, "add")
    assert ai_pick not in r2["selection"]["ids"] and r2["ai_calls"] == 0 and "Gas tee before work" in r2["missing"]


# Rules-only NC: safety note; Accept/Reject/Replace
def test_accept_reject_replace(ctx):
    s, nid, f = ctx["s"], ctx["ncs"]["NC-01"]["id"], ctx["files"]["NC-01"]
    r = auto(s, nid)
    assert r["ai_calls"] == 0 and r["selection"]["ids"] == f[:3] and r["missing"] == []
    assert any("Safety-critical" in x for x in r["review_notes"])
    items, _ = recs(s, nid)
    assert all(v["state"] == "pending" for v in items.values())
    ids = {k: v["item_id"] for k, v in items.items()}
    v = s.put(f"{BASE}/api/ncs/{nid}/recommendations/{ids['Gas tee before work']}", json={"action": "accept"}).json()
    st = {i["title"]: (i["state"], i["source"]) for i in v["items"]}
    assert st["Gas tee before work"] == ("accepted", "staff") and st["Tape showing 250mm"][0] == "pending" and st["Vent cover fitted"][0] == "pending"
    v = s.put(f"{BASE}/api/ncs/{nid}/recommendations/{ids['Vent cover fitted']}", json={"action": "reject"}).json()
    assert next(i for i in v["items"] if i["title"] == "Vent cover fitted")["state"] == "rejected"
    v = s.put(f"{BASE}/api/ncs/{nid}/recommendations/{ids['Tape showing 250mm']}", json={"action": "replace", "file_id": f[3]}).json()
    t = next(i for i in v["items"] if i["title"] == "Tape showing 250mm")
    assert t["state"] == "replaced" and t["file_id"] == f[3]
    r2 = auto(s, nid, "replace")
    assert r2["ai_calls"] == 0 and f[2] not in r2["selection"]["ids"] and "Vent cover fitted" in r2["missing"]
    assert r2["suggested"] == []  # nothing left as AI suggested


# Rejected suggestion still in staff selection after Reject (observation)
def test_reject_leaves_photo_in_selection(ctx):
    s, nid, f = ctx["s"], ctx["ncs"]["NC-01"]["id"], ctx["files"]["NC-01"]
    sel = s.get(f"{BASE}/api/projects/{ctx['ncs']['NC-01'].get('project_id', '')}")
    print("observation: selection after reject+replace auto:", auto(s, nid, "add")["selection"]["ids"], f)


# Installer cannot Auto Select
def test_installer_forbidden(ctx):
    s = login("qa.i43.installer@example.com")
    r = s.post(f"{BASE}/api/ncs/{ctx['ncs']['NC-01']['id']}/evidence-selection/auto", json={"mode": "replace"})
    assert r.status_code in (401, 403)


# Regression: manual save (stale seq, empty)
def test_manual_selection_regression(ctx):
    s, nid, f = ctx["s"], ctx["ncs"]["NC-02"]["id"], ctx["files"]["NC-02"]
    r = s.put(f"{BASE}/api/ncs/{nid}/evidence-selection", json={"ids": [f[2], f[0], "bogus"], "seq": 10})
    assert r.json()["ids"] == [f[2], f[0]]
    assert s.put(f"{BASE}/api/ncs/{nid}/evidence-selection", json={"ids": [f[1]], "seq": 5}).json().get("stale") is True
    assert s.put(f"{BASE}/api/ncs/{nid}/evidence-selection", json={"ids": [], "seq": 11}).json()["ids"] == []
