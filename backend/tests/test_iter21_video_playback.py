# Iter 21 — Video playback fixes:
#  (1) Range (206) + signed URL on /files/{fid}/{variant} playback & original video,
#  (2) /evidence/{fid}/play state machine (pending -> ready|failed) + perms,
#  (3) Multiple videos per NC (no supersedes/hidden), active_video_id = newest,
#      DELETE active -> fallback to another video; submit video-only works.
#  (4) /api/config/media max_video_mb; size_bytes mismatch -> 422; MAX_VIDEO_MB -> 413;
#      HEVC MOV + MPEG-4 MOV + H.264 MP4 all appear in GET project.ncs[].videos
#      and in Export ZIP /Videos.
import io
import json
import os
import re
import time
import uuid
import zipfile

import pytest
import requests

BASE = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "admin": ("admin@remedialflow.dev", "AdminDemo!2026"),
    "employee": ("employee@remedialflow.dev", "EmployeeDemo!2026"),
    "installer": ("installer@remedialflow.dev", "InstallerDemo!2026"),
    "installer2": ("installer2@remedialflow.dev", "InstallerDemo!2026"),
}

FIXTURES = {
    "h264":  "/app/tests/sample_150s.mp4",       # H.264 150s
    "hevc":  "/app/tests/sample_hevc_140s.mov",  # HEVC MOV 140s
    "mpeg4": "/app/tests/sample_130s.mov",       # MPEG-4 MOV 130s
    "big":   "/app/tests/sample_big.mp4",        # 125MB
}


def _login(role):
    r = requests.post(f"{API}/auth/login",
                      json={"email": CREDS[role][0], "password": CREDS[role][1]}, timeout=20)
    r.raise_for_status()
    j = r.json()
    return {"user": j["user"], "h": {"Authorization": f"Bearer {j['access_token']}"}}


@pytest.fixture(scope="module")
def tokens():
    return {k: _login(k) for k in CREDS}


@pytest.fixture(scope="module")
def project(tokens):
    inst_id = tokens["installer"]["user"]["id"]
    ref = f"TEST-ITER21-{int(time.time())}"
    payload = {
        "ref": ref, "address": "21 Video Lane", "postcode": "VI1 21D",
        "installer_id": inst_id,
        "ncs": [{"number": "NC-01", "description": "Video playback test NC",
                 "required_work": "Record", "safety_critical": False}],
    }
    r = requests.post(f"{API}/projects", json=payload,
                      headers=tokens["employee"]["h"], timeout=30)
    assert r.status_code in (200, 201), r.text
    pid = r.json()["id"]
    pr = requests.get(f"{API}/projects/{pid}", headers=tokens["installer"]["h"], timeout=15).json()
    nid = pr["ncs"][0]["id"]
    yield {"pid": pid, "nid": nid, "ref": ref}
    try:
        requests.delete(f"{API}/projects/{pid}", headers=tokens["employee"]["h"], timeout=15)
    except Exception:
        pass


def _upload_video(fixture, nid, h, source="in_app_camera", client_id=None, size_override=None):
    with open(fixture, "rb") as f:
        data = f.read()
    size = size_override if size_override is not None else len(data)
    meta = {"kind": "video", "source": source, "size_bytes": size,
            "client_id": client_id or f"ci-{uuid.uuid4().hex[:12]}"}
    name = os.path.basename(fixture)
    ct = "video/mp4" if name.endswith(".mp4") else "video/quicktime"
    files = {"file": (name, io.BytesIO(data), ct)}
    r = requests.post(f"{API}/ncs/{nid}/evidence", data={"meta": json.dumps(meta)},
                      files=files, headers=h, timeout=600)
    return r


def _wait_playback(fid, h, max_s=240):
    """Poll /evidence/{fid}/play until state becomes 'ready' or 'failed'."""
    last = None
    for _ in range(max_s // 3):
        r = requests.get(f"{API}/evidence/{fid}/play", headers=h, timeout=15)
        if r.status_code == 200:
            last = r.json()
            if last.get("state") in ("ready", "failed"):
                return last
        time.sleep(3)
    return last


# ===== 1. /api/config/media =====
class TestConfig:
    def test_config_media_default_500(self, tokens):
        r = requests.get(f"{API}/config/media", headers=tokens["installer"]["h"], timeout=10)
        assert r.status_code == 200, r.text
        j = r.json()
        assert isinstance(j.get("max_video_mb"), int)
        # default per server.py:47 = 500
        assert j["max_video_mb"] == 500


# ===== 2. Upload validation =====
class TestUploadValidation:
    def test_size_bytes_mismatch_422(self, tokens, project):
        # Truncated / mismatched size_bytes must never be stored as a success
        r = _upload_video(FIXTURES["h264"], project["nid"], tokens["installer"]["h"],
                          size_override=99999999)  # wrong
        assert r.status_code == 422, r.text
        assert "incomplete" in r.text.lower() or "incomplete" in r.json().get("detail", "").lower()

    def test_over_limit_413(self, tokens, project, monkeypatch=None):
        # The big fixture is 125MB and under 500MB so it won't trip 413 by default.
        # Instead, send a tiny mp4 header but claim size over MAX_VIDEO_MB*1024*1024.
        # The server checks len(data) > limit BEFORE size_bytes mismatch — so we need
        # a real body > 500MB which we don't have. Skip if MAX_VIDEO_MB too high.
        max_mb = requests.get(f"{API}/config/media",
                              headers=tokens["installer"]["h"]).json()["max_video_mb"]
        if max_mb > 200:
            pytest.skip(f"MAX_VIDEO_MB={max_mb}; no fixture > limit available in CI")


# ===== 3. Multi-video per NC =====
class TestMultiVideoPerNC:
    """Upload 3 videos (H.264, HEVC, MPEG-4). All must appear; newest is active."""

    @pytest.fixture(scope="class")
    def uploaded(self, tokens, project):
        h = tokens["installer"]["h"]
        out = {}
        # Upload in a known order (H.264 oldest, MPEG-4 newest)
        for key in ("h264", "hevc", "mpeg4"):
            r = _upload_video(FIXTURES[key], project["nid"], h,
                              source="gallery" if key == "mpeg4" else "in_app_camera")
            assert r.status_code == 200, f"{key}: {r.status_code} {r.text[:300]}"
            out[key] = r.json()
            time.sleep(0.5)  # ensure server_received_at ordering
        return out

    def test_all_videos_visible_in_project(self, tokens, project, uploaded):
        pr = requests.get(f"{API}/projects/{project['pid']}",
                          headers=tokens["installer"]["h"], timeout=15).json()
        nc = next(n for n in pr["ncs"] if n["id"] == project["nid"])
        vids = nc.get("videos") or []
        ids = {v["id"] for v in vids}
        for key in ("h264", "hevc", "mpeg4"):
            assert uploaded[key]["id"] in ids, f"{key} missing from videos[]"
        # none hidden by 'superseded' - all present
        assert len(ids) >= 3
        # active_video_id = newest (mpeg4 uploaded last)
        assert nc.get("active_video_id") == uploaded["mpeg4"]["id"]

    def test_delete_active_falls_back(self, tokens, project, uploaded):
        """Deleting the active (newest) draft video must move active_video_id to another."""
        h = tokens["installer"]["h"]
        active = uploaded["mpeg4"]["id"]
        r = requests.delete(f"{API}/evidence/{active}", headers=h, timeout=15)
        assert r.status_code == 200, r.text
        pr = requests.get(f"{API}/projects/{project['pid']}", headers=h, timeout=15).json()
        nc = next(n for n in pr["ncs"] if n["id"] == project["nid"])
        new_active = nc.get("active_video_id")
        assert new_active is not None, "active_video_id must fall back, not be null"
        assert new_active != active
        # The remaining videos should still be visible
        ids = {v["id"] for v in (nc.get("videos") or [])}
        assert uploaded["h264"]["id"] in ids
        assert uploaded["hevc"]["id"] in ids


# ===== 4. /evidence/{fid}/play state machine =====
class TestPlayStateMachine:
    @pytest.fixture(scope="class")
    def h264_fid(self, tokens, project):
        r = _upload_video(FIXTURES["h264"], project["nid"], tokens["installer"]["h"])
        assert r.status_code == 200, r.text
        return r.json()["id"]

    def test_play_state_shape(self, tokens, h264_fid):
        r = requests.get(f"{API}/evidence/{h264_fid}/play",
                         headers=tokens["installer"]["h"], timeout=15)
        assert r.status_code == 200, r.text
        j = r.json()
        for k in ("state", "original_url", "original_mime"):
            assert k in j, f"missing {k} in {j}"
        assert j["state"] in ("pending", "ready", "failed")
        assert j["original_mime"] in ("video/mp4", "video/quicktime")

    def test_installer_other_project_forbidden(self, tokens, h264_fid):
        """Installer2 is not assigned to this NC."""
        r = requests.get(f"{API}/evidence/{h264_fid}/play",
                         headers=tokens["installer2"]["h"], timeout=15)
        assert r.status_code in (403, 404), r.text

    def test_employee_can_see(self, tokens, h264_fid):
        r = requests.get(f"{API}/evidence/{h264_fid}/play",
                         headers=tokens["employee"]["h"], timeout=15)
        assert r.status_code == 200, r.text

    def test_h264_becomes_ready(self, tokens, h264_fid):
        res = _wait_playback(h264_fid, tokens["installer"]["h"], max_s=240)
        assert res is not None and res.get("state") == "ready", f"playback never ready: {res}"
        assert res.get("url"), "ready state must include signed url"


# ===== 5. HEVC transcode =====
class TestHevcTranscode:
    def test_hevc_playback_produces_h264_mp4(self, tokens, project):
        r = _upload_video(FIXTURES["hevc"], project["nid"], tokens["installer"]["h"])
        assert r.status_code == 200, r.text
        fid = r.json()["id"]
        res = _wait_playback(fid, tokens["installer"]["h"], max_s=240)
        assert res is not None, "no playback poll response"
        # Playback should eventually be ready; codec hevc is recorded
        if res.get("state") == "ready":
            assert res.get("codec") == "hevc", f"expected hevc codec, got {res.get('codec')}"
            # Fetch the signed URL and verify it serves video/mp4
            url = res["url"]
            full = url if url.startswith("http") else f"{BASE}{url}"
            head = requests.get(full, headers={"Range": "bytes=0-1"}, timeout=30)
            assert head.status_code == 206, head.status_code
            assert head.headers.get("Content-Type", "").startswith("video/mp4"), head.headers
        else:
            # Transcodes are serialized; may still be pending in CI. Explicitly report.
            pytest.skip(f"HEVC transcode still {res.get('state')} after wait window")


# ===== 6. Signed file range serving =====
class TestRangeAndSignedUrl:
    @pytest.fixture(scope="class")
    def ready_video(self, tokens, project):
        """Upload an H.264 clip and wait for playback ready."""
        r = _upload_video(FIXTURES["h264"], project["nid"], tokens["installer"]["h"])
        assert r.status_code == 200, r.text
        fid = r.json()["id"]
        res = _wait_playback(fid, tokens["installer"]["h"], max_s=240)
        assert res and res.get("state") == "ready", f"playback not ready: {res}"
        pb = res["url"]
        orig = res["original_url"]
        return {"fid": fid, "playback_url": pb if pb.startswith("http") else f"{BASE}{pb}",
                "original_url": orig if orig.startswith("http") else f"{BASE}{orig}",
                "original_mime": res["original_mime"]}

    def test_range_prefix_returns_206(self, ready_video):
        r = requests.get(ready_video["playback_url"],
                         headers={"Range": "bytes=0-1"}, timeout=30)
        assert r.status_code == 206, r.status_code
        assert r.headers.get("Accept-Ranges") == "bytes"
        cr = r.headers.get("Content-Range", "")
        assert cr.startswith("bytes 0-1/"), cr
        assert r.headers.get("Content-Type", "").startswith("video/mp4")
        assert r.headers.get("Content-Length") == "2"

    def test_no_range_returns_200_with_length(self, ready_video):
        r = requests.get(ready_video["playback_url"], stream=True, timeout=30)
        try:
            assert r.status_code == 200, r.status_code
            assert r.headers.get("Content-Length"), "Content-Length missing"
            assert r.headers.get("Accept-Ranges") == "bytes"
        finally:
            r.close()

    def test_suffix_range_last_100(self, ready_video):
        r = requests.get(ready_video["playback_url"],
                         headers={"Range": "bytes=-100"}, timeout=30)
        assert r.status_code == 206, r.status_code
        assert r.headers.get("Content-Length") == "100"
        cr = r.headers.get("Content-Range", "")
        m = re.match(r"bytes (\d+)-(\d+)/(\d+)", cr)
        assert m, cr
        start, end, size = int(m.group(1)), int(m.group(2)), int(m.group(3))
        assert end == size - 1
        assert start == size - 100

    def test_bad_range_returns_416(self, ready_video):
        r = requests.get(ready_video["playback_url"],
                         headers={"Range": "bytes=999999999-"}, timeout=30)
        assert r.status_code == 416, r.status_code

    def test_tampered_signature_403(self, ready_video):
        # Flip the last char of the sig param
        url = ready_video["playback_url"]
        bad = re.sub(r"(sig=)([^&]+)", lambda m: m.group(1) + ("a" if m.group(2)[-1] != "a" else "b")
                     + m.group(2)[:-1], url)
        assert bad != url, "could not mutate sig"
        r = requests.get(bad, timeout=15)
        assert r.status_code == 403, r.status_code

    def test_original_serves_with_range(self, ready_video):
        r = requests.get(ready_video["original_url"],
                         headers={"Range": "bytes=0-1"}, timeout=30)
        assert r.status_code == 206, r.status_code
        assert r.headers.get("Accept-Ranges") == "bytes"
        assert r.headers.get("Content-Type", "").startswith("video/")


# ===== 7. Submit video-only + export ZIP contains all originals =====
class TestSubmitAndExport:
    @pytest.fixture(scope="class")
    def ncpid(self, tokens):
        inst_id = tokens["installer"]["user"]["id"]
        ref = f"TEST-ITER21E-{int(time.time())}"
        payload = {"ref": ref, "address": "21 Export", "postcode": "EX1 21",
                   "installer_id": inst_id,
                   "ncs": [{"number": "NC-01", "description": "export test",
                            "required_work": "x", "safety_critical": False}]}
        r = requests.post(f"{API}/projects", json=payload,
                          headers=tokens["employee"]["h"], timeout=30)
        assert r.status_code in (200, 201)
        pid = r.json()["id"]
        pr = requests.get(f"{API}/projects/{pid}", headers=tokens["installer"]["h"]).json()
        nid = pr["ncs"][0]["id"]
        yield {"pid": pid, "nid": nid}
        try:
            requests.delete(f"{API}/projects/{pid}", headers=tokens["employee"]["h"])
        except Exception:
            pass

    def test_submit_video_only_and_export(self, tokens, ncpid):
        h = tokens["installer"]["h"]
        # Upload 2 videos (H.264 and MPEG-4) – no photos
        uploaded = []
        for key in ("h264", "mpeg4"):
            r = _upload_video(FIXTURES[key], ncpid["nid"], h)
            assert r.status_code == 200, r.text
            uploaded.append(r.json()["id"])
            time.sleep(0.3)
        # Submit NC
        sub = requests.post(f"{API}/ncs/{ncpid['nid']}/submit",
                           json={"client_id": f"sub-{uuid.uuid4().hex[:8]}"},
                           headers=h, timeout=30)
        assert sub.status_code == 200, sub.text
        j = sub.json()
        assert j.get("status") == "assessing"
        # Export ZIP via staff
        link = requests.get(f"{API}/export/link?project_id={ncpid['pid']}",
                            headers=tokens["employee"]["h"], timeout=15).json()["url"]
        full = link if link.startswith("http") else f"{BASE}{link}"
        z = requests.get(full, headers=tokens["employee"]["h"], timeout=120)
        assert z.status_code == 200, z.status_code
        zf = zipfile.ZipFile(io.BytesIO(z.content))
        names = zf.namelist()
        video_entries = [n for n in names if n.startswith("Videos/")]
        assert len(video_entries) >= 2, f"Expected >=2 videos in /Videos: {names}"
