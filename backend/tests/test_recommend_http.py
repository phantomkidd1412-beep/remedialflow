# Knowledge Index Phase 3 - photo recommendations: public HTTP auth/validation checks (non-mutating, no AI calls). Uses QA-KBUI seed.
import os
import asyncio
import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")
BASE_URL = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")).rstrip("/")
ADMIN = ("qa.kbui.admin@example.com", "QaCheck-7731x")
INST = ("qa.kbui.installer@example.com", "QaCheck-7731x")


def _db_lookup():
    async def go():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        p = await c.projects.find_one({"ref": "QA-KBUI"})
        if not p:
            return None
        nc = await c.ncs.find_one({"project_id": str(p["_id"])})
        foreign = await c.files.find_one({"kind": "photo", "nc_id": {"$ne": str(nc["_id"])}})
        return {"nc": str(nc["_id"]), "status": nc["checklist"]["status"], "foreign": foreign and str(foreign["_id"])}
    return asyncio.new_event_loop().run_until_complete(go())


SEED = _db_lookup()


def _login(email, pw):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pw})
    if r.status_code != 200:
        pytest.skip(f"login failed {r.status_code}")
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def adm():
    s = requests.Session(); s.headers["Authorization"] = f"Bearer {_login(*ADMIN)}"; return s


@pytest.fixture(scope="module")
def inst():
    s = requests.Session(); s.headers["Authorization"] = f"Bearer {_login(*INST)}"; return s


@pytest.fixture(scope="module")
def nc():
    if not SEED:
        pytest.skip("QA-KBUI seed missing")
    return SEED["nc"]


# installer is blocked from all recommendation endpoints
def test_installer_get_403(inst, nc):
    assert inst.get(f"{BASE_URL}/api/ncs/{nc}/recommendations").status_code == 403


def test_installer_post_put_propose_403(inst, nc):
    assert inst.post(f"{BASE_URL}/api/ncs/{nc}/recommendations", json={"force": False}).status_code == 403
    assert inst.put(f"{BASE_URL}/api/ncs/{nc}/recommendations/it-a", json={"action": "accept"}).status_code == 403
    assert inst.post(f"{BASE_URL}/api/ncs/{nc}/recommendations/propose-profile", json={"name": "x"}).status_code == 403


def test_no_auth(nc):
    assert requests.get(f"{BASE_URL}/api/ncs/{nc}/recommendations").status_code in (401, 403)


# admin GET shape
def test_admin_get_shape(adm, nc):
    r = adm.get(f"{BASE_URL}/api/ncs/{nc}/recommendations")
    assert r.status_code == 200
    d = r.json()
    assert [i["item_id"] for i in d["items"]] == ["it-a", "it-b", "it-c"]
    assert len(d["photos"]) == 4
    assert d["available"] == (SEED["status"] in ("confirmed", "auto"))


# validation
def test_replace_foreign_file_400(adm, nc):
    fid = SEED["foreign"] or "0123456789abcdef01234567"
    r = adm.put(f"{BASE_URL}/api/ncs/{nc}/recommendations/it-c", json={"action": "replace", "file_id": fid})
    assert r.status_code == 400 and "photos" in r.json()["detail"]


def test_replace_bogus_id_400(adm, nc):
    r = adm.put(f"{BASE_URL}/api/ncs/{nc}/recommendations/it-c", json={"action": "replace", "file_id": "not-an-id"})
    assert r.status_code == 400


def test_unknown_item_404(adm, nc):
    assert adm.put(f"{BASE_URL}/api/ncs/{nc}/recommendations/it-zzz", json={"action": "accept"}).status_code == 404


def test_bad_action_422(adm, nc):
    assert adm.put(f"{BASE_URL}/api/ncs/{nc}/recommendations/it-a", json={"action": "approve"}).status_code == 422


@pytest.mark.skipif(not SEED or SEED["status"] != "draft", reason="only while checklist is draft")
def test_draft_blocks_run_and_propose(adm, nc):
    r = adm.post(f"{BASE_URL}/api/ncs/{nc}/recommendations", json={"force": False})
    assert r.status_code == 400 and "Confirm" in r.json()["detail"]
    r = adm.post(f"{BASE_URL}/api/ncs/{nc}/recommendations/propose-profile", json={"name": "TEST_x"})
    assert r.status_code == 400


def test_bad_nc_id(adm):
    assert adm.get(f"{BASE_URL}/api/ncs/0123456789abcdef01234567/recommendations").status_code in (400, 404)
