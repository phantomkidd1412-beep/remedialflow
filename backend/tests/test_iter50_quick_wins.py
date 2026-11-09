# Iter50 quick wins: log-redaction (no 'Logging error'), staff installer-name search, gallery-not-stamped, submit keeps installer_note.
# Requires: python tests/qa_iter50_seed.py seed > /tmp/i50_seed.json. Writes 3 tiny JPEGs to configured (Dropbox) storage.
import hashlib
import io
import json
import os
import random
import sys
import time
import uuid

import pytest
import requests
from bson import ObjectId
from PIL import Image
from pymongo import MongoClient

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")

BASE = open("/app/frontend/.env").read().split("EXPO_PUBLIC_BACKEND_URL=")[1].split()[0].rstrip("/")
SEED = json.load(open("/tmp/i50_seed.json"))
for line in open("/app/backend/.env"):
    k, _, v = line.strip().partition("=")
    if k in ("MONGO_URL", "DB_NAME"):
        os.environ.setdefault(k, v.strip('"'))
DB = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
PW = "QaCheck-7731x"
ERR = "/var/log/supervisor/backend.err.log"
OUT = "/var/log/supervisor/backend.out.log"
STATE = {}


def jpeg(seed, px=160):
    rnd = random.Random(seed)
    b = io.BytesIO()
    Image.new("RGB", (px, px), (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))).save(b, "JPEG", quality=80)
    return b.getvalue()


def login(email):
    r = requests.post(f"{BASE}/api/auth/login", json={"email": email, "password": PW}, timeout=30)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def H():
    return {k: login(e) for k, e in {"admin": "qa.i50.admin@example.com", "employee": "qa.i50.staff@example.com",
                                     "samuel": "qa.i50.samuel@example.com", "priya": "qa.i50.priya@example.com"}.items()}


def refs(h, q):
    r = requests.get(f"{BASE}/api/projects", params={"search": q, "limit": 100}, headers=h, timeout=30)
    assert r.status_code == 200, r.text
    return {i["ref"] for i in r.json()["items"] if i["ref"].startswith("QA-I50")}


# --- Search: staff match installer name (project- and NC-level), legacy fields preserved, installers unchanged
@pytest.mark.parametrize("role", ["admin", "employee"])
@pytest.mark.parametrize("q", ["Samuel", "QaTester", "Samuel QaTester", "samu", "qatest", "SAMUEL"])
def test_staff_search_installer_name(H, role, q):
    assert refs(H[role], q) == {"QA-I50-A", "QA-I50-B"}


@pytest.mark.parametrize("role", ["admin", "employee"])
def test_staff_search_other_installer_and_legacy(H, role):
    h = H[role]
    assert refs(h, "Priya") == {"QA-I50-B", "QA-I50-C"}
    assert refs(h, "Alpha Road") == {"QA-I50-A"}
    assert refs(h, "qa5 0cc") == {"QA-I50-C"}
    assert refs(h, "QA-I50-B") == {"QA-I50-B"}
    assert refs(h, "QA-I50") == {"QA-I50-A", "QA-I50-B", "QA-I50-C"}
    assert refs(h, "zz-no-such-thing-qa50") == set()


def test_installer_search_unchanged(H):
    assert refs(H["samuel"], "QA-I50") == {"QA-I50-A", "QA-I50-B"}  # only own (NC-assigned) projects
    assert refs(H["samuel"], "Samuel") == set()  # installer search does not use installer names
    assert refs(H["samuel"], "Priya") == set()
    assert refs(H["samuel"], "Beta Street") == {"QA-I50-B"}
    assert refs(H["samuel"], "Gamma") == set()  # other installer's project is never visible
    assert refs(H["priya"], "QA-I50") == {"QA-I50-B", "QA-I50-C"}


# --- Stamping: gallery keeps pixels (no stamped variant), in-app camera gets stamped; original sha256 unchanged
def _upload(h, nid, data, meta):
    r = requests.post(f"{BASE}/api/ncs/{nid}/evidence", headers=h, files={"file": ("p.jpg", data, "image/jpeg")},
                      data={"meta": json.dumps(meta)}, timeout=60)
    assert r.status_code == 200, r.text
    return r.json()


def _wait_ready(fid, t=60):
    for _ in range(t):
        d = DB.files.find_one({"_id": ObjectId(fid)})
        if d.get("processing") in ("ready", "failed"):
            return d
        time.sleep(1)
    return d


def test_gallery_vs_camera_stamping(H):
    nid = SEED["B"]["NC-01"]
    g, c = jpeg(5001), jpeg(5002)
    fg = _upload(H["samuel"], nid, g, {"kind": "photo", "source": "gallery", "client_id": f"qa50-{uuid.uuid4()}", "lat": None, "lng": None, "accuracy_m": None})
    fc = _upload(H["samuel"], nid, c, {"kind": "photo", "source": "in_app_camera", "client_id": f"qa50-{uuid.uuid4()}", "lat": 51.5, "lng": -0.12,
                                       "accuracy_m": 8, "captured_at": "2026-01-10 10:00", "tz": "Europe/London"})
    assert fg["source"] == "gallery" and fc["source"] == "in_app_camera"
    dg, dc = _wait_ready(fg["id"]), _wait_ready(fc["id"])
    STATE["files"] = [fg["id"], fc["id"]]
    assert dg["processing"] == "ready" and dc["processing"] == "ready", (dg.get("processing_error"), dc.get("processing_error"))
    assert set(dg["paths"]) == {"original", "display", "thumb"}, dg["paths"]
    assert "stamped" in dc["paths"] and {"original", "display", "thumb"} <= set(dc["paths"]), dc["paths"]
    assert dg["sha256"] == hashlib.sha256(g).hexdigest() and dc["sha256"] == hashlib.sha256(c).hexdigest()
    assert dc["location_verified"] is True and dg["location_verified"] is False
    # original bytes as stored are unchanged (download via signed URL from project detail)
    det = requests.get(f"{BASE}/api/projects/{SEED['B']['pid']}", headers=H["samuel"], timeout=30).json()
    photos = {p["id"]: p for n in det["ncs"] for p in n["photos"]}
    for fid, raw in ((fg["id"], g), (fc["id"], c)):
        u = photos[fid]["urls"]
        assert "stamped" not in u if fid == fg["id"] else "stamped" in u
        r = requests.get(BASE + u["original"] if u["original"].startswith("/") else u["original"], timeout=60)
        assert r.status_code == 200 and hashlib.sha256(r.content).hexdigest() == hashlib.sha256(raw).hexdigest()
    STATE["thumbs"] = [photos[f]["urls"]["thumb"] for f in STATE["files"]]


# --- Logging: signed-URL thumb requests produce no 'Logging error'; access line redacted; timing
def test_signed_url_logging_and_timing():
    assert STATE.get("thumbs"), "needs upload test"
    before = open(ERR, errors="ignore").read().count("Logging error")
    out_size = os.path.getsize(OUT)
    err_size = os.path.getsize(ERR)
    times = []
    for i in range(20):
        u = STATE["thumbs"][i % 2]
        t0 = time.time()
        r = requests.get(BASE + u if u.startswith("/") else u, timeout=30)
        times.append(time.time() - t0)
        assert r.status_code == 200 and r.headers.get("content-type", "").startswith("image/")
    time.sleep(2)
    after = open(ERR, errors="ignore").read().count("Logging error")
    new_out = open(OUT, errors="ignore").read()[out_size:]
    new_err = open(ERR, errors="ignore").read()[err_size:]
    new = new_out + new_err
    print(f"\nTHUMB TIMES avg={sum(times)/len(times):.3f}s min={min(times):.3f}s max={max(times):.3f}s")
    print("ACCESS SAMPLE:", [ln for ln in new.splitlines() if "/thumb" in ln][:2])
    assert after == before, f"new Logging error lines: {after - before}"
    thumb_lines = [ln for ln in new.splitlines() if "/thumb" in ln]
    assert len(thumb_lines) >= 20, len(thumb_lines)
    assert all("sig=[redacted]" in ln for ln in thumb_lines)
    assert not any(ln for ln in thumb_lines if "sig=" in ln and "sig=[redacted]" not in ln)


# --- Submit without note keeps stored installer_note (in-process; AI background task NOT executed)
def test_submit_without_note_keeps_installer_note():
    from _loop import LOOP
    from fastapi import BackgroundTasks
    from core import User
    import server
    nid = SEED["B"]["NC-01"]
    u = User.from_mongo(DB.users.find_one({"email": "qa.i50.samuel@example.com"}))
    bg = BackgroundTasks()
    r = LOOP.run_until_complete(server.submit(nid, bg, server.SubmitIn(client_id=f"qa50-sub-{uuid.uuid4()}"), u))
    assert r["status"] == "assessing"
    nc = DB.ncs.find_one({"_id": ObjectId(nid)})
    assert nc["installer_note"] == "QA existing installer note"
    # empty / whitespace note also leaves it untouched (installer-facing field removed)
    assert DB.files.count_documents({"nc_id": nid, "status": "submitted"}) == 2
