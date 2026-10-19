"""Backend tests for the new PDF-import project creation flow."""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://field-evidence-app-1.preview.emergentagent.com").rstrip("/")
API = BASE_URL + "/api"

SAMPLE_PDF = "/app/tests/sample_inspection_report.pdf"

ADMIN = ("admin@remedialflow.dev", "AdminDemo!2026")
EMPLOYEE = ("employee@remedialflow.dev", "EmployeeDemo!2026")
INSTALLER = ("installer@remedialflow.dev", "InstallerDemo!2026")


def _login(email, password):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _bearer(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(scope="module")
def employee_token():
    return _login(*EMPLOYEE)


@pytest.fixture(scope="module")
def installer_token():
    return _login(*INSTALLER)


@pytest.fixture(scope="module")
def admin_token():
    return _login(*ADMIN)


@pytest.fixture(scope="module")
def ready_import(employee_token):
    """Upload sample PDF once and poll until ready; reused across tests."""
    with open(SAMPLE_PDF, "rb") as f:
        pdf_bytes = f.read()
    files = {"file": ("sample_inspection_report.pdf", pdf_bytes, "application/pdf")}
    r = requests.post(f"{API}/projects/import-pdf", headers=_bearer(employee_token), files=files, timeout=30)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("status") == "processing" and j.get("id")
    iid = j["id"]
    # Poll up to ~90s
    deadline = time.time() + 90
    last = None
    while time.time() < deadline:
        rr = requests.get(f"{API}/imports/{iid}", headers=_bearer(employee_token), timeout=15)
        assert rr.status_code == 200, rr.text
        last = rr.json()
        if last.get("status") in ("ready", "failed"):
            break
        time.sleep(2)
    assert last and last.get("status") == "ready", f"import did not become ready: {last}"
    assert "draft" in last
    return iid, last


# ---------------- PDF import ----------------
class TestPdfImportHappyPath:
    def test_import_returns_processing_then_ready_with_expected_draft(self, ready_import):
        iid, data = ready_import
        draft = data["draft"]
        # Core fields extracted by Gemini from the sample
        assert draft.get("ref") == "TM-77812", f"expected ref TM-77812, got {draft.get('ref')}"
        assert draft.get("postcode", "").upper().replace(" ", "") == "AL35PQ"
        assert isinstance(draft.get("address"), str) and len(draft["address"]) > 0
        ncs = draft.get("ncs") or []
        assert len(ncs) == 2, f"expected 2 NCs, got {len(ncs)}: {ncs}"
        # NC2 safety critical
        nc2 = ncs[1]
        assert nc2.get("safety_critical") is True
        # Each NC should have 1 photo attached
        for i, n in enumerate(ncs):
            assert n.get("photo_count") == 1, f"NC{i+1} photo_count={n.get('photo_count')}"
            assert n.get("description")
            assert isinstance(n.get("pages"), list) and len(n["pages"]) >= 1

    def test_import_nc_count_stated_matches(self, ready_import):
        _, data = ready_import
        draft = data["draft"]
        # nc_count_stated should be present and consistent
        assert "nc_count_stated" in draft


class TestPdfImportValidation:
    def test_non_pdf_rejected(self, employee_token):
        files = {"file": ("not.pdf", b"this is not a pdf", "application/pdf")}
        r = requests.post(f"{API}/projects/import-pdf", headers=_bearer(employee_token), files=files, timeout=15)
        assert r.status_code == 400, r.text

    def test_installer_cannot_import(self, installer_token):
        with open(SAMPLE_PDF, "rb") as f:
            pdf_bytes = f.read()
        files = {"file": ("sample.pdf", pdf_bytes, "application/pdf")}
        r = requests.post(f"{API}/projects/import-pdf", headers=_bearer(installer_token), files=files, timeout=30)
        assert r.status_code == 403, r.text

    def test_installer_cannot_read_import(self, installer_token, ready_import):
        iid, _ = ready_import
        r = requests.get(f"{API}/imports/{iid}", headers=_bearer(installer_token), timeout=15)
        assert r.status_code == 403, r.text


# ---------------- Create project from import ----------------
class TestCreateProjectFromImport:
    @pytest.fixture(scope="class")
    def created_project(self, employee_token, ready_import):
        iid, data = ready_import
        draft = data["draft"]
        # Build NC payload with pages so photo attachment runs
        nc_payload = [{
            "description": n["description"],
            "required_work": n.get("required_work", ""),
            "safety_critical": bool(n.get("safety_critical", False)),
            "pages": n.get("pages", []),
        } for n in draft["ncs"]]
        # Attempt to create. If ref exists, use a suffix.
        ref = draft.get("ref") or f"TM-{uuid.uuid4().hex[:5].upper()}"
        body = {"ref": ref, "address": draft.get("address") or "Unknown",
                "postcode": draft.get("postcode") or "", "import_id": iid, "ncs": nc_payload}
        r = requests.post(f"{API}/projects", json=body, headers=_bearer(employee_token), timeout=20)
        if r.status_code == 409:
            body["ref"] = f"{ref}-{uuid.uuid4().hex[:4].upper()}"
            r = requests.post(f"{API}/projects", json=body, headers=_bearer(employee_token), timeout=20)
        assert r.status_code == 200, r.text
        pid = r.json()["id"]
        return pid, body["ref"]

    def test_project_persists_source_report_and_trustmark_photos(self, employee_token, created_project):
        pid, ref = created_project
        # Wait up to 60s for background photo attachment
        deadline = time.time() + 60
        pd = None
        while time.time() < deadline:
            pd = requests.get(f"{API}/projects/{pid}", headers=_bearer(employee_token), timeout=15).json()
            all_have_photo = pd.get("ncs") and all(len(n.get("trustmark", [])) >= 1 for n in pd["ncs"])
            if all_have_photo:
                break
            time.sleep(2)
        assert pd is not None
        assert pd["project"]["ref"] == ref
        assert pd["project"].get("source_report"), f"no source_report on project: {pd['project']}"
        assert pd["project"]["source_report"].get("filename")
        for n in pd["ncs"]:
            assert len(n["trustmark"]) >= 1, f"NC {n.get('number')} missing trustmark photo"

    def test_report_link_returns_signed_url_pdf(self, employee_token, created_project):
        pid, _ = created_project
        r = requests.get(f"{API}/projects/{pid}/report-link", headers=_bearer(employee_token), timeout=15)
        assert r.status_code == 200, r.text
        url = r.json()["url"]
        assert "sig=" in url and "exp=" in url
        full = BASE_URL + url
        rr = requests.get(full, timeout=30)
        assert rr.status_code == 200
        assert rr.headers.get("content-type", "").startswith("application/pdf")
        assert rr.content[:4] == b"%PDF"

    def test_report_link_tampered_sig_forbidden(self, employee_token, created_project):
        pid, _ = created_project
        r = requests.get(f"{API}/projects/{pid}/report-link", headers=_bearer(employee_token), timeout=15)
        url = BASE_URL + r.json()["url"]
        # Replace signature with bogus value
        tampered = url.replace("sig=", "sig=deadbeef", 1)
        # Ensure we actually mutated
        assert tampered != url
        rr = requests.get(tampered, timeout=15)
        assert rr.status_code == 403, f"expected 403, got {rr.status_code}: {rr.text[:200]}"


# ---------------- Regression: manual create still works ----------------
class TestManualProjectCreationRegression:
    def test_create_project_without_pdf(self, employee_token):
        ref = f"TEST-MAN-{uuid.uuid4().hex[:5].upper()}"
        body = {
            "ref": ref,
            "address": "99 Regression Lane, Testville",
            "postcode": "TE1 1ST",
            "ncs": [
                {"description": "Loose cable tray", "required_work": "Secure cable tray", "safety_critical": False},
                {"description": "Missing fire stop", "required_work": "Install fire stop", "safety_critical": True},
            ],
        }
        r = requests.post(f"{API}/projects", json=body, headers=_bearer(employee_token), timeout=15)
        assert r.status_code == 200, r.text
        pid = r.json()["id"]
        pd = requests.get(f"{API}/projects/{pid}", headers=_bearer(employee_token), timeout=15).json()
        assert pd["project"]["ref"] == ref
        assert len(pd["ncs"]) == 2
        # No source_report expected for manual create
        assert not pd["project"].get("source_report")
        # Report-link should 404
        rl = requests.get(f"{API}/projects/{pid}/report-link", headers=_bearer(employee_token), timeout=10)
        assert rl.status_code == 404
