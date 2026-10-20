# Iteration 20 — Gallery video upload + simplified NC Evidence PDF + optional export note.
# Only tests the newly specified behaviour; broader regression lives in other test files.
import io
import json
import os
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
import requests

BASE = os.environ.get("EXPO_PUBLIC_BACKEND_URL",
                     "https://field-evidence-app-1.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "admin": ("admin@remedialflow.dev", "AdminDemo!2026"),
    "employee": ("employee@remedialflow.dev", "EmployeeDemo!2026"),
    "installer": ("installer@remedialflow.dev", "InstallerDemo!2026"),
    "installer2": ("installer2@remedialflow.dev", "InstallerDemo!2026"),
}


def _login(role):
    r = requests.post(f"{API}/auth/login",
                      json={"email": CREDS[role][0], "password": CREDS[role][1]}, timeout=15)
    r.raise_for_status()
    j = r.json()
    return {"token": j["access_token"], "user": j["user"],
            "h": {"Authorization": f"Bearer {j['access_token']}"}}


@pytest.fixture(scope="module")
def tokens():
    return {k: _login(k) for k in CREDS}


@pytest.fixture(scope="module")
def project_and_nc(tokens):
    inst_id = tokens["installer"]["user"]["id"]
    ref = f"TEST-ITER20-{int(time.time())}"
    payload = {
        "ref": ref, "address": "20 Iter Lane", "postcode": "IT1 20N",
        "installer_id": inst_id,
        "ncs": [{"number": "NC-01", "description": "Original TrustMark defect wording ABCXYZ-UNIQUE",
                 "required_work": "Fix it", "safety_critical": False}],
    }
    r = requests.post(f"{API}/projects", json=payload,
                      headers=tokens["employee"]["h"], timeout=20)
    assert r.status_code in (200, 201), r.text
    pid = r.json()["id"]
    pr = requests.get(f"{API}/projects/{pid}",
                      headers=tokens["installer"]["h"], timeout=15).json()
    nid = pr["ncs"][0]["id"]
    yield {"pid": pid, "nid": nid, "ref": ref, "nc_number": pr["ncs"][0]["number"]}
    try:
        requests.delete(f"{API}/projects/{pid}",
                        headers=tokens["employee"]["h"], timeout=10)
    except Exception:
        pass


# ---------- helpers for crafting bytes with a specific `ftyp` brand ----------
def _mp4_bytes():
    # ftyp box: size(4) + 'ftyp' + major_brand 'mp42' + minor + compat; then mdat with zeros.
    return (b"\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00mp42isomavc1"
            b"\x00\x00\x00\x08mdat" + b"\x00" * 1024)


def _mov_bytes():
    # ftyp box with brand 'qt  '
    return (b"\x00\x00\x00\x20ftypqt  \x00\x00\x02\x00qt  qt  "
            b"\x00\x00\x00\x08mdat" + b"\x00" * 1024)


def _png_bytes():
    # tiny valid 1x1 PNG
    return bytes.fromhex(
        "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489"
        "0000000D49444154789C62000100000500010D0A2DB40000000049454E44AE426082"
    )


def _upload(nc_id, headers, meta, filename, content, ctype):
    return requests.post(
        f"{API}/ncs/{nc_id}/evidence",
        headers=headers,
        files={"file": (filename, io.BytesIO(content), ctype)},
        data={"meta": json.dumps(meta)},
        timeout=30,
    )


# ------------------------- (1) stamp_lines unit test -------------------------
class TestStampLines:
    def test_gallery_no_captured_at_shows_not_available(self):
        from media import stamp_lines
        meta = {"source": "gallery", "captured_at": None,
                "project_ref": "EF-X", "postcode": "AB1 2CD", "nc_number": "NC-01"}
        l1, l2, l3 = stamp_lines(meta)
        assert l1 == "DATE TAKEN: NOT AVAILABLE"
        assert l2 == "GPS: NOT AVAILABLE (GALLERY UPLOAD)"
        assert "GALLERY UPLOAD" in l3
        # must NOT contain current time
        now_str = datetime.now(timezone.utc).strftime("%d/%m/%Y")
        assert now_str not in l1

    def test_gallery_with_exif_captured_at_marked_from_file(self):
        from media import stamp_lines
        meta = {"source": "gallery",
                "captured_at": "2024-05-01T10:30:00+00:00",
                "tz": "Europe/London", "tz_offset_min": 60,
                "project_ref": "EF-X", "postcode": "AB1 2CD", "nc_number": "NC-01"}
        l1, l2, _ = stamp_lines(meta)
        assert "(FROM FILE, NOT VERIFIED)" in l1
        assert "GPS: NOT AVAILABLE (GALLERY UPLOAD)" in l2

    def test_in_app_camera_stamp_unchanged(self):
        from media import stamp_lines
        meta = {"source": "in_app_camera",
                "captured_at": "2025-01-01T12:00:00+00:00",
                "tz": "UTC", "tz_offset_min": 0,
                "lat": 51.5, "lng": -0.12, "accuracy_m": 10, "location_verified": True,
                "project_ref": "EF-X", "postcode": "AB1 2CD", "nc_number": "NC-01"}
        l1, l2, l3 = stamp_lines(meta)
        assert "NOT AVAILABLE" not in l1
        assert "FROM FILE" not in l1
        assert "GPS: 51.5000, -0.1200" in l2
        assert "GALLERY UPLOAD" not in l3


# ------------------------ (2) gallery video upload ---------------------------
class TestGalleryVideoUpload:
    def test_mp4_gallery_upload_sets_active_video(self, tokens, project_and_nc):
        nid = project_and_nc["nid"]
        cid = f"TEST_mp4_{uuid.uuid4().hex[:10]}"
        meta = {"kind": "video", "source": "gallery", "captured_at": None,
                "tz": "UTC", "tz_offset_min": 0,
                "lat": None, "lng": None, "accuracy_m": None, "client_id": cid}
        r = _upload(nid, tokens["installer"]["h"], meta,
                    "clip.mp4", _mp4_bytes(), "video/mp4")
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["kind"] == "video"
        assert j["source"] == "gallery"
        assert j["location_verified"] is False
        assert j.get("lat") in (None, 0) or j.get("lat") is None
        assert j.get("lng") is None
        TestGalleryVideoUpload.first_mp4_id = j["id"]
        TestGalleryVideoUpload.first_mp4_cid = cid
        # becomes NC active video
        pr = requests.get(f"{API}/projects/{project_and_nc['pid']}",
                          headers=tokens["installer"]["h"], timeout=15).json()
        nc = next(n for n in pr["ncs"] if n["id"] == nid)
        assert nc.get("active_video_id") == j["id"]

    def test_mov_gallery_upload(self, tokens, project_and_nc):
        nid = project_and_nc["nid"]
        meta = {"kind": "video", "source": "gallery", "captured_at": None,
                "tz": "UTC", "tz_offset_min": 0,
                "lat": None, "lng": None, "accuracy_m": None,
                "client_id": f"TEST_mov_{uuid.uuid4().hex[:10]}"}
        r = _upload(nid, tokens["installer"]["h"], meta,
                    "clip.mov", _mov_bytes(), "video/quicktime")
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["kind"] == "video" and j["source"] == "gallery"
        assert j["location_verified"] is False

    def test_non_video_bytes_with_kind_video_rejected_415(self, tokens, project_and_nc):
        nid = project_and_nc["nid"]
        meta = {"kind": "video", "source": "gallery", "captured_at": None,
                "tz": "UTC", "tz_offset_min": 0,
                "lat": None, "lng": None, "accuracy_m": None,
                "client_id": f"TEST_bad_{uuid.uuid4().hex[:10]}"}
        r = _upload(nid, tokens["installer"]["h"], meta,
                    "fake.mp4", _png_bytes(), "video/mp4")
        assert r.status_code == 415, r.text

    def test_retry_same_client_id_returns_same_file(self, tokens, project_and_nc):
        nid = project_and_nc["nid"]
        cid = TestGalleryVideoUpload.first_mp4_cid
        meta = {"kind": "video", "source": "gallery", "captured_at": None,
                "tz": "UTC", "tz_offset_min": 0,
                "lat": None, "lng": None, "accuracy_m": None, "client_id": cid}
        r = _upload(nid, tokens["installer"]["h"], meta,
                    "clip.mp4", _mp4_bytes(), "video/mp4")
        assert r.status_code == 200
        assert r.json()["id"] == TestGalleryVideoUpload.first_mp4_id

    def test_unassigned_installer_gets_403_or_404(self, tokens, project_and_nc):
        nid = project_and_nc["nid"]
        meta = {"kind": "video", "source": "gallery", "captured_at": None,
                "tz": "UTC", "tz_offset_min": 0,
                "lat": None, "lng": None, "accuracy_m": None,
                "client_id": f"TEST_x_{uuid.uuid4().hex[:10]}"}
        r = _upload(nid, tokens["installer2"]["h"], meta,
                    "clip.mp4", _mp4_bytes(), "video/mp4")
        assert r.status_code in (403, 404), r.text


# ------------------------ (3) export-note endpoint ---------------------------
class TestExportNote:
    def test_installer_forbidden(self, tokens, project_and_nc):
        r = requests.put(f"{API}/ncs/{project_and_nc['nid']}/export-note",
                         headers=tokens["installer"]["h"],
                         json={"note": "x"}, timeout=10)
        assert r.status_code == 403, r.text

    def test_over_600_chars_rejected_422(self, tokens, project_and_nc):
        r = requests.put(f"{API}/ncs/{project_and_nc['nid']}/export-note",
                         headers=tokens["employee"]["h"],
                         json={"note": "a" * 601}, timeout=10)
        assert r.status_code == 422, r.text

    def test_employee_can_set_and_fetch(self, tokens, project_and_nc):
        note = "Replaced cladding section per spec. Visual check OK."
        r = requests.put(f"{API}/ncs/{project_and_nc['nid']}/export-note",
                         headers=tokens["employee"]["h"],
                         json={"note": note}, timeout=10)
        assert r.status_code == 200, r.text
        assert r.json().get("export_note") == note
        pr = requests.get(f"{API}/projects/{project_and_nc['pid']}",
                         headers=tokens["employee"]["h"], timeout=15).json()
        nc = next(n for n in pr["ncs"] if n["id"] == project_and_nc["nid"])
        assert nc.get("export_note") == note


# ---------------------- (4) PDF export shape ---------------------------------
class TestPdfExport:
    @pytest.fixture(scope="class")
    def exported_zip(self, tokens, project_and_nc):
        # ensure at least one photo + the gallery video are present, plus an export note
        meta = {"kind": "photo", "source": "in_app_camera",
                "captured_at": (datetime.now(timezone.utc) - timedelta(minutes=5))
                              .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "tz": "UTC", "tz_offset_min": 0,
                "lat": 51.5, "lng": -0.12, "accuracy_m": 10,
                "client_id": f"TEST_pdfphoto_{uuid.uuid4().hex[:8]}"}
        up = _upload(project_and_nc["nid"], tokens["installer"]["h"], meta,
                     "p.png", _png_bytes(), "image/png")
        assert up.status_code == 200, up.text
        # (gallery mp4 already uploaded in TestGalleryVideoUpload)
        # set an export note (also ensures the section appears)
        requests.put(f"{API}/ncs/{project_and_nc['nid']}/export-note",
                     headers=tokens["employee"]["h"],
                     json={"note": "Factual remediation note for PDF."}, timeout=10)
        link = requests.get(f"{API}/export/link",
                            params={"project_id": project_and_nc["pid"]},
                            headers=tokens["admin"]["h"], timeout=15).json()
        url = BASE + link["url"] if link["url"].startswith("/") else link["url"]
        r = requests.get(url, headers=tokens["admin"]["h"], timeout=60)
        assert r.status_code == 200, f"{r.status_code} {r.text[:400]}"
        assert r.headers.get("content-type", "").startswith("application/zip")
        return zipfile.ZipFile(io.BytesIO(r.content))

    def test_zip_contains_pdf_and_videos_folder(self, exported_zip):
        names = exported_zip.namelist()
        assert any(n.endswith(".pdf") and n.startswith("NC-") for n in names), names
        assert any(n.startswith("Videos/") and n.endswith(".mp4") for n in names), names

    def test_pdf_text_omits_internal_workflow_info(self, exported_zip, project_and_nc):
        import pymupdf
        pdf_name = next(n for n in exported_zip.namelist() if n.endswith(".pdf"))
        pdf_bytes = exported_zip.read(pdf_name)
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        # banned terms
        banned_exact = ["APPROVED", "Approved", "status", "confidence",
                        "stamped with", "Employee", "Internally"]
        for term in banned_exact:
            assert term not in text, f"PDF should not contain '{term}':\n---\n{text[:1500]}"
        # "AI" is a two-letter word; make sure no AI-decision wording is present
        for phrase in ["Approved by AI", "AI confidence", "AI assessment",
                       "AI verified", "AI-verified"]:
            assert phrase not in text, phrase
        # required sections / wording
        assert "Project Reference:" in text
        assert "NC Reference:" in text
        assert "NON-COMPLIANCE" in text
        assert "ABCXYZ-UNIQUE" in text  # original TrustMark wording
        assert "REMEDIATION PHOTOGRAPHS" in text
        assert "REMEDIATION NOTE" in text
        assert "Factual remediation note for PDF." in text
        # address line
        assert "20 Iter Lane" in text

    def test_empty_note_omits_section(self, tokens, project_and_nc, exported_zip):
        # clear note then re-export
        r = requests.put(f"{API}/ncs/{project_and_nc['nid']}/export-note",
                         headers=tokens["employee"]["h"],
                         json={"note": ""}, timeout=10)
        assert r.status_code == 200
        link = requests.get(f"{API}/export/link",
                            params={"project_id": project_and_nc["pid"]},
                            headers=tokens["admin"]["h"], timeout=15).json()
        url = BASE + link["url"]
        r2 = requests.get(url, headers=tokens["admin"]["h"], timeout=60)
        assert r2.status_code == 200
        z = zipfile.ZipFile(io.BytesIO(r2.content))
        import pymupdf
        pdf_name = next(n for n in z.namelist() if n.endswith(".pdf"))
        doc = pymupdf.open(stream=z.read(pdf_name), filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        assert "REMEDIATION NOTE" not in text, text[:1500]


# ---------------------- (5) corrupt image resilience -------------------------
class TestExportResilienceCorruptImage:
    def test_readable_filter_drops_bad_bytes(self):
        from evidence_pack import readable
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (10, 10), (255, 0, 0)).save(buf, "JPEG")
        good = buf.getvalue()
        bad = b"not-an-image-bytes"
        kept = readable([good, bad, good])
        assert len(kept) == 2
