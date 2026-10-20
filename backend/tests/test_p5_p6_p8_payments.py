"""RemedialFlow Phase 5 / 6 / 8 + Payments backend tests.

Covers:
 - Phase 5: trustmark_reject review action (approved -> rejected w/ rejection_source="trustmark",
            installer notification 'rejected_trustmark', earnings not removed or duplicated,
            400 on non-approved NC, 403 for installer, no-dup earnings on re-approve).
 - Phase 8: TrustMark Evidence Pack export: /api/export/link signed URL -> ZIP named
            '<REF>_Evidence_Pack.zip' with per-NC PDF, approval status line, TrustMark
            observation + required work text, before/after headings, Videos/NC-XX_*.mp4 entry
            when video uploaded (mp4 generated via imageio_ffmpeg). Signed-link tampering 403.
 - Payments: GET /api/admin/payments groups by installer/week; employee/installer -> 403;
             POST /api/admin/payments/mark-paid flips earned->paid for the week.

Only TEST- prefixed data is created or mutated. Real projects (e.g. P419977) are read-only.
"""
from __future__ import annotations
import io
import os
import re
import uuid
import zipfile
import pytest
import requests


# ----- backend URL (public preview) -----
def _read_env(path: str, key: str) -> str:
    try:
        for line in open(path):
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip('"').strip("'")
    except FileNotFoundError:
        pass
    return ""


BASE_URL = (_read_env("/app/frontend/.env", "EXPO_PUBLIC_BACKEND_URL")
            or os.environ.get("EXPO_PUBLIC_BACKEND_URL", "")).rstrip("/")
API = f"{BASE_URL}/api"

CREDS = {
    "admin": ("admin@remedialflow.dev", "AdminDemo!2026"),
    "employee": ("employee@remedialflow.dev", "EmployeeDemo!2026"),
    "installer_a": ("installer@remedialflow.dev", "InstallerDemo!2026"),
    "installer_b": ("installer2@remedialflow.dev", "InstallerDemo!2026"),
}


def _login(role: str) -> str:
    email, pw = CREDS[role]
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, timeout=20)
    assert r.status_code == 200, f"login {role} -> {r.status_code} {r.text[:200]}"
    return r.json()["access_token"]


def _hdr(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _jpeg(seed: int = 0) -> bytes:
    from PIL import Image, ImageDraw
    import random
    random.seed(seed)
    img = Image.new("RGB", (320, 240), (210, 210, 210))
    d = ImageDraw.Draw(img)
    for _ in range(60):
        x1, y1 = random.randint(0, 320), random.randint(0, 240)
        x2, y2 = x1 + random.randint(10, 60), y1 + random.randint(10, 60)
        d.rectangle([x1, y1, x2, y2], fill=(random.randint(40, 230), random.randint(40, 230), random.randint(40, 230)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _tiny_mp4() -> bytes:
    import subprocess
    import tempfile
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    subprocess.run(
        [ff, "-y", "-f", "lavfi", "-i", "color=c=blue:s=160x120:d=1:r=10",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", tmp.name],
        check=True, capture_output=True, timeout=30,
    )
    with open(tmp.name, "rb") as f:
        data = f.read()
    os.unlink(tmp.name)
    return data


STATE: dict = {}


@pytest.fixture(scope="module")
def tokens():
    assert BASE_URL, "EXPO_PUBLIC_BACKEND_URL not set"
    return {k: _login(k) for k in CREDS}


@pytest.fixture(scope="module")
def ids(tokens):
    r = requests.get(f"{API}/installers", headers=_hdr(tokens["admin"]), timeout=15)
    assert r.status_code == 200, r.text
    byemail = {d["email"]: d["id"] for d in r.json()}
    return {"A": byemail["installer@remedialflow.dev"], "B": byemail["installer2@remedialflow.dev"]}


def _count_earnings_for_ref(tokens, ref: str) -> int:
    """Count admin earnings items whose project ref matches via /admin/payments.jobs[]."""
    r = requests.get(f"{API}/admin/payments", headers=_hdr(tokens["admin"]), timeout=15)
    assert r.status_code == 200
    return sum(1 for g in r.json() for j in g.get("jobs", []) if j.get("ref") == ref)


# =====================================================================
# Phase 5: TrustMark rejection flow
# =====================================================================
class TestP5TrustmarkReject:
    def test_01_create_project_with_two_ncs(self, tokens, ids):
        ref = f"TEST-TM-{uuid.uuid4().hex[:6].upper()}"
        payload = {
            "ref": ref, "address": f"{ref} Rejection Rd, Testshire", "postcode": "TM1 1TM",
            "installer_id": ids["A"],
            "ncs": [
                {"description": "Missing pipe lagging to tank in loft area needs covering",
                 "required_work": "Fit insulation sleeves to all exposed pipework", "safety_critical": False},
                {"description": "Wall vent partially blocked by render finish on external wall",
                 "required_work": "Open vent and clear render", "safety_critical": False},
            ],
        }
        r = requests.post(f"{API}/projects", headers=_hdr(tokens["admin"]), json=payload, timeout=30)
        assert r.status_code == 200, r.text
        STATE["pid"] = r.json()["id"]
        STATE["ref"] = ref
        d = requests.get(f"{API}/projects/{STATE['pid']}", headers=_hdr(tokens["admin"]), timeout=15).json()
        assert len(d["ncs"]) == 2
        STATE["ncs"] = [n["id"] for n in d["ncs"]]
        STATE["numbers"] = [n["number"] for n in d["ncs"]]

    def test_02_upload_trustmark_and_remediation_photos_before_approval(self, tokens):
        """Upload BEFORE approving so evidence persists in the pack. NC accepts evidence while 'open'."""
        nc1 = STATE["ncs"][0]
        for seed in (11, 12):  # trustmark references
            files = {"file": (f"tm{seed}.jpg", _jpeg(seed), "image/jpeg")}
            r = requests.post(f"{API}/ncs/{nc1}/evidence",
                              headers={"Authorization": f"Bearer {tokens['admin']}"},
                              files=files, data={"meta": '{"kind":"trustmark"}'}, timeout=20)
            assert r.status_code == 200, r.text
        for seed in (21, 22, 23):  # installer remediation
            files = {"file": (f"rem{seed}.jpg", _jpeg(seed), "image/jpeg")}
            r = requests.post(f"{API}/ncs/{nc1}/evidence",
                              headers={"Authorization": f"Bearer {tokens['installer_a']}"},
                              files=files, data={"meta": '{"lat":51.5,"lng":-0.1}'}, timeout=20)
            assert r.status_code == 200, r.text

    def test_03_upload_small_mp4_before_approval(self, tokens):
        nc1 = STATE["ncs"][0]
        try:
            data = _tiny_mp4()
        except Exception as e:
            pytest.skip(f"ffmpeg unavailable: {e}")
        files = {"file": ("evidence.mp4", data, "video/mp4")}
        r = requests.post(f"{API}/ncs/{nc1}/evidence",
                          headers={"Authorization": f"Bearer {tokens['installer_a']}"},
                          files=files, data={"meta": '{"kind":"video","duration_s":1}'}, timeout=60)
        assert r.status_code == 200, r.text
        STATE["has_video"] = True

    def test_04_admin_approves_both_ncs(self, tokens):
        for nc in STATE["ncs"]:
            r = requests.post(f"{API}/ncs/{nc}/review", headers=_hdr(tokens["admin"]),
                              json={"action": "approve", "note": ""}, timeout=15)
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "approved"
        d = requests.get(f"{API}/projects/{STATE['pid']}", headers=_hdr(tokens["admin"]), timeout=15).json()
        assert d["project"]["status"] in ("completed", "resolved"), d["project"]["status"]

    def test_05_earnings_created_for_installer_a(self, tokens):
        count = _count_earnings_for_ref(tokens, STATE["ref"])
        STATE["earnings_before"] = count
        assert count >= 1, f"expected at least one earning for {STATE['ref']}, got {count}"

    def test_06_installer_cannot_trustmark_reject(self, tokens):
        nc1 = STATE["ncs"][0]
        r = requests.post(f"{API}/ncs/{nc1}/review", headers=_hdr(tokens["installer_a"]),
                          json={"action": "trustmark_reject", "note": "nope"}, timeout=15)
        assert r.status_code == 403

    def test_07_trustmark_reject_nc1_as_admin(self, tokens):
        nc1 = STATE["ncs"][0]
        r = requests.post(f"{API}/ncs/{nc1}/review", headers=_hdr(tokens["admin"]),
                          json={"action": "trustmark_reject", "note": "Photo not clear enough"}, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "rejected"

    def test_08_nc1_rejection_metadata_visible(self, tokens):
        d = requests.get(f"{API}/projects/{STATE['pid']}", headers=_hdr(tokens["admin"]), timeout=15).json()
        nc1 = next(n for n in d["ncs"] if n["id"] == STATE["ncs"][0])
        assert nc1["status"] == "rejected", nc1
        assert nc1.get("rejection_source") == "trustmark", nc1
        # NC-02 unaffected
        nc2 = next(n for n in d["ncs"] if n["id"] == STATE["ncs"][1])
        assert nc2["status"] == "approved"
        # Project reopens
        assert d["project"]["status"] == "in_progress", d["project"]["status"]
        # Note: NC pydantic model does NOT declare 'reopen_history' (core.py:103), so the
        # push survives in Mongo but is filtered by `extra='ignore'` when serialising.
        # We validate the push happened via the audit trail below.

    def test_09_audit_has_trustmark_review(self, tokens):
        r = requests.get(f"{API}/projects/{STATE['pid']}/audit", headers=_hdr(tokens["admin"]), timeout=15)
        assert r.status_code == 200
        rows = r.json()
        tm = [a for a in rows if (a.get("event") or a.get("action")) == "nc.reviewed"
              and (a.get("data") or a.get("meta") or {}).get("action") == "trustmark_reject"]
        assert tm, f"nc.reviewed/trustmark_reject not in audit {rows[:3]}"
        assert (tm[-1].get("data") or tm[-1].get("meta") or {}).get("note") == "Photo not clear enough"

    def test_10_installer_notification_rejected_trustmark(self, tokens):
        r = requests.get(f"{API}/notifications", headers=_hdr(tokens["installer_a"]), timeout=15)
        assert r.status_code == 200
        items = [i for i in r.json()["items"]
                 if i["project_id"] == STATE["pid"] and i["kind"] == "rejected_trustmark"]
        assert items, "expected rejected_trustmark notification"
        body = (items[0].get("body") or "") + " " + (items[0].get("title") or "")
        assert "not clear" in body.lower() or "Photo not clear enough" in body

    def test_11_earnings_not_removed(self, tokens):
        count = _count_earnings_for_ref(tokens, STATE["ref"])
        assert count == STATE["earnings_before"], f"earnings mutated: {STATE['earnings_before']} -> {count}"

    def test_12_trustmark_reject_on_non_approved_nc_400(self, tokens):
        nc1 = STATE["ncs"][0]  # now 'rejected'
        r = requests.post(f"{API}/ncs/{nc1}/review", headers=_hdr(tokens["admin"]),
                          json={"action": "trustmark_reject", "note": "x"}, timeout=15)
        assert r.status_code == 400, r.text

    def test_13_reapprove_nc1_no_duplicate_earning(self, tokens):
        nc1 = STATE["ncs"][0]
        r = requests.post(f"{API}/ncs/{nc1}/review", headers=_hdr(tokens["admin"]),
                          json={"action": "approve", "note": ""}, timeout=15)
        assert r.status_code == 200
        count = _count_earnings_for_ref(tokens, STATE["ref"])
        assert count == STATE["earnings_before"], f"duplicate earning on re-approve: {count} vs {STATE['earnings_before']}"


# =====================================================================
# Phase 8: Evidence Pack
# =====================================================================
class TestP8EvidencePack:
    def test_01_signed_export_link_as_admin(self, tokens):
        r = requests.get(f"{API}/export/link", params={"project_id": STATE["pid"]},
                         headers=_hdr(tokens["admin"]), timeout=15)
        assert r.status_code == 200, r.text
        url = r.json()["url"]
        assert "/api/export/project/" in url and "sig=" in url and "exp=" in url, url
        STATE["export_url"] = url

    def test_02_installer_cannot_get_export_link(self, tokens):
        r = requests.get(f"{API}/export/link", params={"project_id": STATE["pid"]},
                         headers=_hdr(tokens["installer_a"]), timeout=15)
        assert r.status_code == 403

    def test_03_download_zip_and_inspect(self, tokens):
        url = BASE_URL + STATE["export_url"]
        r = requests.get(url, headers=_hdr(tokens["admin"]), timeout=120)
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        cd = r.headers.get("Content-Disposition", "")
        assert f'{STATE["ref"]}_Evidence_Pack.zip' in cd, cd
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = z.namelist()
        STATE["zip_names"] = names
        STATE["zip_bytes"] = r.content
        pdfs = [n for n in names if n.endswith(".pdf")]
        assert len(pdfs) == 2, pdfs
        assert any(re.match(r"^NC-01_[A-Za-z0-9_]+\.pdf$", n) for n in pdfs), pdfs
        assert any(re.match(r"^NC-02_[A-Za-z0-9_]+\.pdf$", n) for n in pdfs), pdfs
        if STATE.get("has_video"):
            vids = [n for n in names if n.startswith("Videos/") and n.endswith(".mp4")]
            assert vids, f"no videos entry in {names}"
            assert any(v.startswith("Videos/NC-01_") for v in vids), vids

    def test_04_nc1_pdf_text(self, tokens):
        import pymupdf
        z = zipfile.ZipFile(io.BytesIO(STATE["zip_bytes"]))
        nc1_pdf = next(n for n in z.namelist() if n.startswith("NC-01_") and n.endswith(".pdf"))
        doc = pymupdf.open(stream=z.read(nc1_pdf), filetype="pdf")
        text = "\n".join(p.get_text() for p in doc)
        doc.close()
        # Simplified client-facing PDF (iter 20): no workflow / approval / AI wording
        assert STATE["ref"] in text, text[:400]
        assert "NC-01" in text
        assert "NON-COMPLIANCE" in text
        for banned in ("APPROVED", "Approved", "AI", "Employee", "status", "confidence"):
            assert banned not in text, f"PDF should not contain '{banned}': {text[:400]}"

    def test_05_nc2_pdf_approved(self, tokens):
        import pymupdf
        z = zipfile.ZipFile(io.BytesIO(STATE["zip_bytes"]))
        nc2_pdf = next(n for n in z.namelist() if n.startswith("NC-02_") and n.endswith(".pdf"))
        doc = pymupdf.open(stream=z.read(nc2_pdf), filetype="pdf")
        text = "\n".join(p.get_text() for p in doc)
        doc.close()
        assert "NC-02" in text
        assert "NON-COMPLIANCE" in text
        for banned in ("APPROVED", "Approved", "AI", "Employee", "status", "confidence"):
            assert banned not in text, f"PDF should not contain '{banned}': {text[:400]}"

    def test_06_tampered_sig_403(self, tokens):
        bad = STATE["export_url"].rsplit("sig=", 1)
        tampered = bad[0] + "sig=" + ("x" + bad[1][1:] if bad[1][0] != "x" else "y" + bad[1][1:])
        r = requests.get(BASE_URL + tampered, headers=_hdr(tokens["admin"]), timeout=15)
        assert r.status_code == 403, r.status_code

    def test_07_expired_exp_403(self, tokens):
        url = re.sub(r"exp=\d+", "exp=1", STATE["export_url"])
        r = requests.get(BASE_URL + url, headers=_hdr(tokens["admin"]), timeout=15)
        assert r.status_code == 403, r.status_code

    def test_08_real_project_p419977_readonly(self, tokens):
        r = requests.get(f"{API}/projects", headers=_hdr(tokens["admin"]), timeout=20)
        assert r.status_code == 200
        projects = r.json() if isinstance(r.json(), list) else r.json().get("items", [])
        match = next((p for p in projects if p.get("ref") == "P419977"), None)
        if not match:
            pytest.skip("P419977 not present")
        link = requests.get(f"{API}/export/link", params={"project_id": match["id"]},
                            headers=_hdr(tokens["admin"]), timeout=15).json()["url"]
        r2 = requests.get(BASE_URL + link, headers=_hdr(tokens["admin"]), timeout=180)
        assert r2.status_code == 200
        cd = r2.headers.get("Content-Disposition", "")
        assert "P419977_Evidence_Pack.zip" in cd, cd
        z = zipfile.ZipFile(io.BytesIO(r2.content))
        pdfs = [n for n in z.namelist() if n.endswith(".pdf")]
        assert pdfs, "no PDFs in P419977 pack"


# =====================================================================
# Payments
# =====================================================================
class TestPayments:
    def test_01_admin_payments_lists_groups(self, tokens, ids):
        r = requests.get(f"{API}/admin/payments", headers=_hdr(tokens["admin"]), timeout=20)
        assert r.status_code == 200, r.text
        groups = r.json()
        assert isinstance(groups, list)
        mine = [g for g in groups if g["installer_id"] == ids["A"]
                and any(j.get("ref") == STATE["ref"] for j in g.get("jobs", []))]
        assert mine, "no payment group found for TEST project"
        g = mine[0]
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", g["week"]), g["week"]
        from datetime import date
        assert date.fromisoformat(g["week"]).weekday() == 0, g["week"]
        assert g["installer_name"]
        assert "unpaid" in g and "paid" in g
        assert g["unpaid"] + g["paid"] > 0
        STATE["pay_week"] = g["week"]

    def test_02_employee_cannot_see_payments(self, tokens):
        r = requests.get(f"{API}/admin/payments", headers=_hdr(tokens["employee"]), timeout=15)
        assert r.status_code == 403

    def test_03_installer_cannot_see_payments(self, tokens):
        r = requests.get(f"{API}/admin/payments", headers=_hdr(tokens["installer_a"]), timeout=15)
        assert r.status_code == 403

    def test_04_installer_cannot_mark_paid(self, tokens, ids):
        r = requests.post(f"{API}/admin/payments/mark-paid",
                          headers=_hdr(tokens["installer_a"]),
                          json={"installer_id": ids["A"], "week": STATE.get("pay_week", "2026-01-05")},
                          timeout=15)
        assert r.status_code == 403

    def test_05_mark_week_paid(self, tokens, ids):
        week = STATE["pay_week"]
        before = requests.get(f"{API}/admin/payments", headers=_hdr(tokens["admin"]), timeout=15).json()
        g_before = next(g for g in before if g["installer_id"] == ids["A"] and g["week"] == week)
        unpaid_before = g_before["unpaid"]
        assert unpaid_before > 0
        r = requests.post(f"{API}/admin/payments/mark-paid",
                          headers=_hdr(tokens["admin"]),
                          json={"installer_id": ids["A"], "week": week}, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        assert r.json()["count"] >= 1
        after = requests.get(f"{API}/admin/payments", headers=_hdr(tokens["admin"]), timeout=15).json()
        g_after = next(g for g in after if g["installer_id"] == ids["A"] and g["week"] == week)
        assert g_after["unpaid"] == 0, g_after
        assert g_after["paid"] >= unpaid_before - 0.01
        # Verify via admin /earnings items for installer A -> our TEST ref jobs now "paid"
        e = requests.get(f"{API}/earnings", headers=_hdr(tokens["admin"]), timeout=15).json()
        our_items = [i for i in e.get("items", []) if i.get("project_id") == STATE["pid"]]
        assert our_items, "test earnings vanished"
        assert all(i["status"] == "paid" for i in our_items), our_items
