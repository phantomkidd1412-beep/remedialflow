# Master Documents Library (Knowledge Index Phase 2) - public HTTP tests (no AI calls). Cleans up uploaded docs via DB.
import os
import uuid
import asyncio
import pytest
import requests
import pymupdf
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")
BASE_URL = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")).rstrip("/")
ADMIN = ("qa.kbui.admin@example.com", "QaCheck-7731x")
INST = ("qa.kbui.installer@example.com", "QaCheck-7731x")
CREATED = []


def _pdf(extra: str) -> bytes:
    d = pymupdf.open()
    p1 = d.new_page()
    p1.insert_text((72, 72), "TEST_QA Loft Guide introduction page with enough readable text to count as a text page " + extra)
    p2 = d.new_page()
    p2.insert_text((72, 72), "5.1 Loft hatch\nInsulation of 100 mm shall be fitted to the loft hatch and draught stripped. " + extra)
    return d.tobytes()


def _login(email, pw):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pw})
    if r.status_code != 200:
        pytest.skip(f"login failed {r.status_code} {r.text[:120]}")
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def adm():
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {_login(*ADMIN)}"
    yield s
    async def clean():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        from bson import ObjectId
        await c.tech_docs.delete_many({"_id": {"$in": [ObjectId(i) for i in CREATED]}})
        await c.tech_doc_pages.delete_many({"doc_id": {"$in": CREATED}})
        await c.files.delete_many({"kind": "techdoc", "doc_id": {"$in": CREATED}})
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(clean())


@pytest.fixture(scope="module")
def inst():
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {_login(*INST)}"
    return s


TAG = uuid.uuid4().hex[:8]
STATE = {}


def _upload(adm, data, **kw):
    form = {"title": f"TEST_QA Loft Guide {TAG}", "version": "v1", "category": "trustmark", "publisher": "QA", "pub_date": "2024"} | kw
    return adm.post(f"{BASE_URL}/api/admin/documents", files={"file": ("t.pdf", data, "application/pdf")}, data=form)


class TestLibrary:
    def test_upload_draft(self, adm):
        data = _pdf(TAG)
        STATE["bytes"] = data
        r = _upload(adm, data)
        assert r.status_code == 200, r.text
        j = r.json()
        CREATED.append(j["id"])
        STATE["id"] = j["id"]
        assert j["status"] == "draft" and j["pages"] == 2 and j["category"] == "trustmark" and j["rev"] == 1
        assert j["extract_status"] == "ok" and j["file_url"]
        print("upload:", {k: j[k] for k in ("pages", "text_pages", "extract_status", "file_url")})

    def test_duplicate_409(self, adm):
        r = _upload(adm, STATE["bytes"])
        assert r.status_code == 409, r.text
        print("dup:", r.json())

    def test_non_pdf_400(self, adm):
        r = adm.post(f"{BASE_URL}/api/admin/documents", files={"file": ("x.txt", b"hello world", "text/plain")},
                     data={"title": "TEST_x", "version": "1", "category": "other"})
        assert r.status_code == 400, r.text

    def test_bad_category_422(self, adm):
        r = _upload(adm, _pdf(TAG + "cat"), category="nope")
        assert r.status_code == 422

    def test_page_text_admin(self, adm):
        r = adm.get(f"{BASE_URL}/api/documents/{STATE['id']}/pages/2")
        assert r.status_code == 200
        assert "Loft hatch" in r.json()["text"]
        assert adm.get(f"{BASE_URL}/api/documents/{STATE['id']}/pages/9").status_code == 404

    def test_search(self, adm):
        r = adm.get(f"{BASE_URL}/api/admin/documents/{STATE['id']}/search", params={"q": "loft hatch insulation"})
        assert r.status_code == 200
        hits = r.json()
        print("search:", hits)
        assert hits and hits[0]["page"] == 2 and hits[0].get("clause") == "5.1"

    def test_approve(self, adm):
        r = adm.put(f"{BASE_URL}/api/admin/documents/{STATE['id']}/status", json={"status": "approved"})
        assert r.status_code == 200 and r.json()["status"] == "approved"
        assert adm.put(f"{BASE_URL}/api/admin/documents/{STATE['id']}/status", json={"status": "superseded"}).status_code == 422

    def test_replace_and_supersede(self, adm):
        r = adm.post(f"{BASE_URL}/api/admin/documents/{STATE['id']}/replace",
                     files={"file": ("t2.pdf", _pdf(TAG + " rev2"), "application/pdf")}, data={"version": "v2"})
        assert r.status_code == 200, r.text
        n = r.json()
        CREATED.append(n["id"])
        assert n["status"] == "draft" and n["rev"] == 2 and n["replaces"] == STATE["id"] and n["title"].startswith("TEST_QA")
        g = adm.get(f"{BASE_URL}/api/admin/documents/{n['id']}").json()
        revs = {x["rev"]: x["status"] for x in g["revisions"]}
        print("revisions before approve:", revs, "history:", g["history"])
        assert revs == {2: "draft", 1: "approved"}
        r = adm.put(f"{BASE_URL}/api/admin/documents/{n['id']}/status", json={"status": "approved"})
        assert r.status_code == 200
        old = adm.get(f"{BASE_URL}/api/admin/documents/{STATE['id']}").json()
        assert old["status"] == "superseded"
        assert {x["rev"]: x["status"] for x in old["revisions"]} == {2: "approved", 1: "superseded"}
        STATE["new"] = n["id"]

    def test_list_contains(self, adm):
        r = adm.get(f"{BASE_URL}/api/admin/documents")
        assert r.status_code == 200
        ids = [d["id"] for d in r.json()]
        assert STATE["id"] in ids and STATE["new"] in ids

    def test_installer_forbidden(self, inst):
        assert inst.get(f"{BASE_URL}/api/admin/documents").status_code == 403
        assert inst.post(f"{BASE_URL}/api/admin/documents", files={"file": ("t.pdf", b"%PDF-1", "application/pdf")},
                         data={"title": "x", "version": "1"}).status_code == 403
        assert inst.get(f"{BASE_URL}/api/documents/{STATE['id']}/pages/1").status_code == 403

    def test_unauth_401(self):
        assert requests.get(f"{BASE_URL}/api/admin/documents").status_code in (401, 403)

    def test_bad_id(self, adm):
        r = adm.get(f"{BASE_URL}/api/admin/documents/notanid")
        print("bad id:", r.status_code, r.text[:100])
        assert r.status_code in (400, 404, 422)
