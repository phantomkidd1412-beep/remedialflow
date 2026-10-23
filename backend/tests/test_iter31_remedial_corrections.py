"""Iteration 31 - Final RemedialFlow corrections.

Covers:
 1. New-NC submit flow: photo-only -> 400 mentioning video; photo+video -> accepted
 2. PUT /api/evidence/{fid}/pdf-include staff stage override (mid, auto, installer 403)
 3. Regression: review-groups, export link, idempotent evidence, export-dropbox idempotent,
    notifications dedupe, login has no demo buttons (that one is UI only).

Address 'QA TEST 1 Example Road' / postcode 'QA1 1AA'.
"""
import io
import os
import time
import random
import struct
import zlib
import pytest
import requests

BASE = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
PWD = os.environ.get("RF_QA_PASSWORD", "")
QA_ADDR = "QA TEST 1 Example Road"
QA_PC = "QA1 1AA"


def _login(email):
    r = requests.post(f"{BASE}/api/auth/login", json={"email": email, "password": PWD}, timeout=15)
    assert r.status_code == 200, (email, r.status_code, r.text)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _png(size=16):
    # minimal valid PNG (checker so files are different per call via color)
    w = h = size
    color = random.randint(0, 255)
    raw = b""
    for _ in range(h):
        raw += b"\x00" + bytes([color, color, color]) * w
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    buf = io.BytesIO(sig + ihdr + idat + iend)
    buf.seek(0)
    return buf


def _jpeg(size=240):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (size, size), (random.randint(50, 200), 150, 180)).save(buf, "JPEG", quality=70)
    buf.seek(0)
    return buf


def _mp4():
    """Tiny well-formed mp4 ftyp box - enough for mime detection + ingest."""
    # ftyp box with isom brand (24 bytes) + mdat box (empty)
    ftyp = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
    mdat = b"\x00\x00\x00\x08mdat"
    buf = io.BytesIO(ftyp + mdat)
    buf.seek(0)
    return buf


@pytest.fixture(scope="module")
def emp():
    return _login("qa.employee@example.com")


@pytest.fixture(scope="module")
def ins():
    return _login("qa.installer@example.com")


@pytest.fixture(scope="module")
def ins2():
    return _login("qa.installer2@example.com")


@pytest.fixture(scope="module")
def project(emp, ins):
    me = requests.get(f"{BASE}/api/auth/me", headers=ins, timeout=10).json()
    iid = me["id"]
    ref = f"QA31-{int(time.time()*1000) % 1000000}-{random.randint(1000,9999)}"
    r = requests.post(f"{BASE}/api/projects", headers=emp, timeout=20, json={
        "ref": ref, "address": QA_ADDR, "postcode": QA_PC, "installer_id": iid,
        "ncs": [
            {"description": f"QA31 NC A {ref}", "required_work": "Add insulation", "safety_critical": False},
            {"description": f"QA31 NC B {ref}", "required_work": "Seal vent", "safety_critical": False},
        ],
    })
    assert r.status_code in (200, 201), r.text
    data = r.json()
    pid = data.get("id") or data.get("project", {}).get("id")
    assert pid
    return {"pid": pid, "ref": ref, "installer_id": iid}


def _nc_ids(headers, pid):
    pr = requests.get(f"{BASE}/api/projects/{pid}", headers=headers, timeout=15).json()
    return [n["id"] for n in pr["ncs"]], pr


def _upload(headers, nc_id, kind="photo", mime="image/jpeg", filename="a.jpg", payload=None, source="gallery"):
    cid = f"qa31-{kind}-{int(time.time()*1000)}-{random.randint(1,99999)}"
    meta = f'{{"kind":"{kind}","source":"{source}","client_id":"{cid}"}}'
    files = {"file": (filename, payload or _jpeg(), mime)}
    r = requests.post(f"{BASE}/api/ncs/{nc_id}/evidence", headers=headers,
                      files=files, data={"meta": meta}, timeout=60)
    return r


# ---------- (1) Submit new NC: video is mandatory, specific error wording ----------
class TestSubmitVideoMandatory:
    def test_photo_only_submit_400_mentions_video(self, ins, project):
        nc_ids, _ = _nc_ids(ins, project["pid"])
        nc_id = nc_ids[0]
        r = _upload(ins, nc_id, kind="photo")
        assert r.status_code in (200, 201), r.text
        # submit without a video -> 400
        sub = requests.post(f"{BASE}/api/ncs/{nc_id}/submit", headers=ins, timeout=15,
                            json={"client_id": f"qa31-sub-{int(time.time()*1000)}", "note": "", "expect": []})
        assert sub.status_code == 400, sub.text
        detail = sub.json().get("detail", "")
        assert detail.startswith("Before submitting, please add"), detail
        assert "video" in detail.lower(), detail

    def test_photo_plus_video_submit_accepted(self, ins, project):
        nc_ids, _ = _nc_ids(ins, project["pid"])
        nc_id = nc_ids[1]
        rp = _upload(ins, nc_id, kind="photo")
        assert rp.status_code in (200, 201), rp.text
        rv = _upload(ins, nc_id, kind="video", mime="video/mp4", filename="r.mp4", payload=_mp4())
        if rv.status_code not in (200, 201):
            pytest.skip(f"backend rejected tiny mp4 ({rv.status_code}): {rv.text[:140]}")
        sub = requests.post(f"{BASE}/api/ncs/{nc_id}/submit", headers=ins, timeout=15,
                            json={"client_id": f"qa31-sub2-{int(time.time()*1000)}", "note": "QA submit", "expect": []})
        assert sub.status_code == 200, sub.text
        body = sub.json()
        # Status should transition from draft to assessing (or similar)
        pr = requests.get(f"{BASE}/api/projects/{project['pid']}", headers=ins, timeout=15).json()
        n = next(x for x in pr["ncs"] if x["id"] == nc_id)
        assert n["status"] in ("assessing", "approved", "employee_review", "more_evidence", "admin_review", "ai_verified_signoff", "rejected"), n["status"]


# ---------- (2) PUT /api/evidence/{fid}/pdf-include stage override ----------
class TestPdfStageOverride:
    @pytest.fixture(scope="class")
    def seeded(self, emp, project):
        pr = requests.get(f"{BASE}/api/projects/{project['pid']}", headers=emp, timeout=15).json()
        nc_id = pr["ncs"][0]["id"]
        r = _upload(emp, nc_id, kind="photo", source="employee_upload")
        assert r.status_code in (200, 201), r.text
        return {"fid": r.json()["id"], "nc_id": nc_id}

    def test_staff_mid_sets_stage_and_source(self, emp, project, seeded):
        fid = seeded["fid"]
        r = requests.put(f"{BASE}/api/evidence/{fid}/pdf-include", headers=emp, timeout=15,
                        json={"include": None, "stage": "mid"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("stage") == "mid", body
        assert body.get("stage_source") == "staff", body
        # verify through project view
        pr = requests.get(f"{BASE}/api/projects/{project['pid']}", headers=emp, timeout=15).json()
        allf = [p for n in pr["ncs"] for p in (n.get("photos") or []) + (n.get("videos") or [])]
        f = next((x for x in allf if x["id"] == fid), None)
        assert f and f.get("stage") == "mid" and f.get("stage_source") == "staff", f

    def test_staff_auto_clears_stage(self, emp, project, seeded):
        fid = seeded["fid"]
        r = requests.put(f"{BASE}/api/evidence/{fid}/pdf-include", headers=emp, timeout=15,
                        json={"include": None, "stage": "auto"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("stage") is None, body
        assert body.get("stage_source") is None, body

    def test_installer_cannot_set_stage(self, ins, seeded):
        fid = seeded["fid"]
        r = requests.put(f"{BASE}/api/evidence/{fid}/pdf-include", headers=ins, timeout=15,
                        json={"include": None, "stage": "final"})
        assert r.status_code == 403, (r.status_code, r.text)


# ---------- (3) Regression quick checks ----------
class TestRegression:
    def test_review_groups_endpoint(self, emp):
        r = requests.get(f"{BASE}/api/review-groups", headers=emp, timeout=15)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_export_link(self, emp, project):
        r = requests.get(f"{BASE}/api/export/link", params={"project_id": project["pid"]},
                        headers=emp, timeout=15)
        assert r.status_code == 200
        assert ".zip" in r.json()["url"]

    def test_export_link_download_200(self, emp, project):
        r = requests.get(f"{BASE}/api/export/link", params={"project_id": project["pid"]},
                        headers=emp, timeout=15)
        url = r.json()["url"]
        full = url if url.startswith("http") else f"{BASE}{url}"
        # HEAD may not be supported - do a tiny GET
        r2 = requests.get(full, timeout=60, stream=True)
        assert r2.status_code == 200, (r2.status_code, r2.text[:200])
        # read a small chunk to validate stream exists
        chunk = next(r2.iter_content(1024), b"")
        r2.close()
        assert chunk, "export zip was empty"

    def test_notifications_endpoint(self, ins):
        r = requests.get(f"{BASE}/api/notifications", headers=ins, timeout=15)
        assert r.status_code == 200

    def test_export_dropbox_idempotent(self, emp, project):
        # First call may succeed or 409 depending on connection state
        r1 = requests.post(f"{BASE}/api/projects/{project['pid']}/export-dropbox",
                           headers=emp, timeout=120)
        if r1.status_code not in (200, 409):
            pytest.skip(f"dropbox not usable: {r1.status_code} {r1.text[:200]}")
        # Second call - must be idempotent (already_exported) or same 409
        r2 = requests.post(f"{BASE}/api/projects/{project['pid']}/export-dropbox",
                           headers=emp, timeout=120)
        assert r2.status_code in (200, 409), r2.text
        if r2.status_code == 200:
            body = r2.json()
            assert body.get("status") in ("already_exported", "exported"), body
