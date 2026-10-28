# Iter49: API checks backing the picked-order gallery (no storage writes): client_id exposed, server order, selection order persists.
# Requires the QA-I48 seed (/tmp/i48_seed.json) and the 5-photo upload batch made on NC-01 by the iter49 UI run.
import json
import re
import time

import httpx
import pytest

BASE = open("/app/frontend/.env").read().split("EXPO_PUBLIC_BACKEND_URL=")[1].split()[0].rstrip("/")
SEED = json.load(open("/tmp/i48_seed.json"))


@pytest.fixture(scope="module")
def h():
    r = httpx.post(f"{BASE}/api/auth/login", json={"email": "qa.i48.admin@example.com", "password": "QaCheck-7731x"}, timeout=30)
    if r.status_code != 200:
        pytest.skip("QA login failed")
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def nc01(h):
    r = httpx.get(f"{BASE}/api/projects/{SEED['pid']}", headers=h, timeout=30)
    assert r.status_code == 200
    return next(n for n in r.json()["ncs"] if n["number"] == "NC-01")


# GET project exposes client_id for every uploaded photo; one record per picked index
def test_client_id_exposed_one_per_index(h):
    photos = nc01(h)["photos"]
    idx = [int(re.match(r"up-\d+-(\d+)$", p["client_id"]).group(1)) for p in photos]
    assert sorted(idx) == [0, 1, 2, 3, 4]
    assert "_id" not in photos[0]


# Server order is completion order (proves the gallery re-orders client-side), not picked order
def test_server_order_is_not_picked_order(h):
    photos = nc01(h)["photos"]
    idx = [int(p["client_id"].rsplit("-", 1)[1]) for p in photos]
    print("server order", idx)
    assert idx != [0, 1, 2, 3, 4]


# Manual selection/PDF order is saved exactly as sent and read back (drag-and-drop path)
def test_selection_order_persists(h):
    ids = [p["id"] for p in nc01(h)["photos"]]
    order = [ids[3], ids[0], ids[4]]
    nid = SEED["NC-01"]["nid"]
    r = httpx.put(f"{BASE}/api/ncs/{nid}/evidence-selection", headers=h, json={"ids": order, "seq": int(time.time() * 1000) + 10**12}, timeout=30)
    assert r.status_code == 200 and r.json()["ids"] == order
    assert nc01(h)["evidence_selection"]["ids"] == order
