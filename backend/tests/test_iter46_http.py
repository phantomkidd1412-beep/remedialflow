# Iter46 HTTP checks on the public URL: health, evidence upload idempotency (client_id), doc upload validation, removed UI endpoints still alive.
# Requires synthetic QA data from qa_iter43_seed.py seed (QA-I43). Env: QA_NC_ID (an NC of QA-I43). Never touches real data.
import io
import os
import uuid

import pytest
import requests
from PIL import Image

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL").rstrip("/")
PW = "QaCheck-7731x"


def login(email):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": PW}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"QA login failed {r.status_code}")
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return s


@pytest.fixture(scope="module")
def admin():
    return login("qa.i43.admin@example.com")


@pytest.fixture(scope="module")
def nc_id():
    v = os.environ.get("QA_NC_ID")
    if not v:
        pytest.skip("QA_NC_ID not set")
    return v


def jpg():
    b = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 200, 30)).save(b, "JPEG", quality=95)
    return b.getvalue()


# API reachable on the public URL (backend /health is not routed through /api ingress)
def test_api_reachable(admin):
    r = admin.get(f"{BASE_URL}/api/auth/me", timeout=15)
    assert r.status_code == 200
    assert r.json().get("email") == "qa.i43.admin@example.com"


# Evidence upload: client_id retry is idempotent (same file returned, no duplicate)
def test_evidence_upload_idempotent(admin, nc_id):
    cid = f"qa46-{uuid.uuid4().hex[:12]}"
    meta = '{"kind":"photo","source":"employee_upload","client_id":"%s"}' % cid
    data = jpg()
    r1 = admin.post(f"{BASE_URL}/api/ncs/{nc_id}/evidence", files={"file": ("a.jpg", data, "image/jpeg")}, data={"meta": meta}, timeout=60)
    assert r1.status_code == 200, r1.text
    r2 = admin.post(f"{BASE_URL}/api/ncs/{nc_id}/evidence", files={"file": ("a.jpg", data, "image/jpeg")}, data={"meta": meta}, timeout=60)
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    assert r1.json().get("size") == len(data)  # original bytes, unmodified size


# Doc upload: missing title/version rejected by the API too
def test_doc_upload_requires_title_version(admin):
    r = admin.post(f"{BASE_URL}/api/admin/documents", files={"file": ("x.pdf", b"%PDF-1.4\n", "application/pdf")}, data={}, timeout=30)
    assert r.status_code == 422


# Backend safeguards behind the removed UI panels still respond
@pytest.mark.parametrize("path", ["checklist", "recommendations", "evidence-review"])
def test_backend_safeguards_alive(admin, nc_id, path):
    r = admin.get(f"{BASE_URL}/api/ncs/{nc_id}/{path}", timeout=30)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), dict)
