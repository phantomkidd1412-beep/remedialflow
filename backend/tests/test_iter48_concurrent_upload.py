# Iter48: concurrent evidence upload (atomic draft-submission claim), client_id idempotency, interrupted/slow uploads, timing.
# Requires: python tests/qa_iter48_seed.py seed > /tmp/i48_seed.json. Writes ~52 small JPEGs to the configured (Dropbox) storage.
import asyncio
import io
import json
import os
import random
import time
import uuid

import httpx
import pytest
from bson import ObjectId
from PIL import Image
from pymongo import MongoClient

BASE = open("/app/frontend/.env").read().split("EXPO_PUBLIC_BACKEND_URL=")[1].split()[0].rstrip("/")
SEED = json.load(open("/tmp/i48_seed.json"))
for line in open("/app/backend/.env"):
    k, _, v = line.strip().partition("=")
    if k in ("MONGO_URL", "DB_NAME"):
        os.environ.setdefault(k, v.strip('"'))
DB = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
TIMINGS = {}


def jpeg(seed: int, px: int = 220) -> bytes:  # ~100-150 KB noisy JPEG, unique per seed
    rnd = random.Random(seed)
    im = Image.frombytes("RGB", (px, px), bytes(rnd.getrandbits(8) for _ in range(px * px * 3)))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=95)
    return b.getvalue()


@pytest.fixture(scope="module")
def token():
    r = httpx.post(f"{BASE}/api/auth/login", json={"email": "qa.i48.admin@example.com", "password": "QaCheck-7731x"}, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def post(c, tok, nid, data, client_id=None, extra=None):
    meta = {"kind": "photo", "source": "employee_upload", **({"client_id": client_id} if client_id else {}), **(extra or {})}
    return await c.post(f"{BASE}/api/ncs/{nid}/evidence", headers={"Authorization": f"Bearer {tok}"},
                        data={"meta": json.dumps(meta)}, files={"file": ("p.jpg", data, "image/jpeg")})


async def batch(tok, nid, n, conc, seed0):
    imgs = [jpeg(seed0 + i) for i in range(n)]
    sem = asyncio.Semaphore(conc)
    async with httpx.AsyncClient(timeout=120) as c:
        async def one(i):
            async with sem:
                return await post(c, tok, nid, imgs[i], client_id=f"qa48-{nid}-{i}")
        t = time.perf_counter()
        res = await asyncio.gather(*[one(i) for i in range(n)])
        return res, time.perf_counter() - t, sum(len(x) for x in imgs)


def check_nc(nid, n):
    files = list(DB.files.find({"nc_id": nid}))
    assert len(files) == n, f"expected {n} files, got {len(files)}"
    subs = {f.get("submission_id") for f in files}
    assert len(subs) == 1 and None not in subs, f"split submissions: {subs}"
    assert DB.submissions.count_documents({"nc_id": nid}) == 1
    nc = DB.ncs.find_one({"_id": ObjectId(nid)})
    assert nc["draft_submission_id"] == subs.pop()
    assert all(f["project_id"] == SEED["pid"] for f in files)
    assert len({f["client_id"] for f in files}) == n
    assert all(f["status"] == "draft" and f["mime"] == "image/jpeg" for f in files)


# Concurrency: 1/5/20 all-at-once, 10 at concurrency 4, 10 sequential (timing baseline); each on a fresh NC with no draft
@pytest.mark.parametrize("num,n,conc", [("NC-01", 1, 1), ("NC-02", 5, 5), ("NC-03", 10, 4), ("NC-04", 20, 20), ("NC-05", 10, 1)])
def test_concurrent_first_uploads_single_submission(token, num, n, conc):
    nid = SEED[num]["nid"]
    assert not (DB.ncs.find_one({"_id": ObjectId(nid)}) or {}).get("draft_submission_id")
    res, secs, size = asyncio.run(batch(token, nid, n, conc, hash(num) % 10000))
    codes = [r.status_code for r in res]
    assert codes == [200] * n, codes
    assert len({r.json()["id"] for r in res}) == n
    TIMINGS[f"{num} n={n} conc={conc}"] = {"secs": round(secs, 2), "per_photo": round(secs / n, 2), "MB": round(size / 1e6, 2)}
    check_nc(nid, n)


# Same client_id: 3 concurrent sends, then a sequential re-send
def test_same_client_id_concurrent_and_sequential(token):
    nid = SEED["NC-06"]["nid"]
    data = jpeg(4242)

    async def go():
        async with httpx.AsyncClient(timeout=120) as c:
            rs = await asyncio.gather(*[post(c, token, nid, data, client_id="qa48-same") for _ in range(3)])
            r2 = await post(c, token, nid, data, client_id="qa48-same")
            return rs, r2
    rs, r2 = asyncio.run(go())
    assert all(r.status_code == 200 for r in rs)
    n = DB.files.count_documents({"nc_id": nid, "client_id": "qa48-same"})
    TIMINGS["same_client_id_concurrent_records"] = n
    assert r2.json()["id"] in {r.json()["id"] for r in rs}
    assert n == 1, f"concurrent same client_id produced {n} records (no unique index)"


# Interrupted upload (size mismatch -> 422, nothing stored), then retry succeeds; plus slow throttled upload alongside fast ones
def test_interrupted_then_retry_and_slow_upload(token):
    nid = SEED["NC-07"]["nid"]
    data = jpeg(777)

    async def slow_post(c, body_bytes, boundary):
        async def gen():
            for i in range(0, len(body_bytes), 8192):
                await asyncio.sleep(0.15)  # ~55 KB/s
                yield body_bytes[i:i + 8192]
        return await c.post(f"{BASE}/api/ncs/{nid}/evidence", headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/form-data; boundary={boundary}"}, content=gen())

    async def go():
        async with httpx.AsyncClient(timeout=180) as c:
            bad = await post(c, token, nid, data, client_id="qa48-int", extra={"size_bytes": len(data) + 500})
            stored_after_bad = DB.files.count_documents({"nc_id": nid})
            ok = await post(c, token, nid, data, client_id="qa48-int", extra={"size_bytes": len(data)})
            b = uuid.uuid4().hex
            sd = jpeg(778)
            body = (f"--{b}\r\nContent-Disposition: form-data; name=\"meta\"\r\n\r\n" + json.dumps({"kind": "photo", "client_id": "qa48-slow"}) +
                    f"\r\n--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"s.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n").encode() + sd + f"\r\n--{b}--\r\n".encode()
            t = time.perf_counter()
            res = await asyncio.gather(slow_post(c, body, b), *[post(c, token, nid, jpeg(800 + i), client_id=f"qa48-fast-{i}") for i in range(3)])
            return bad, stored_after_bad, ok, res, time.perf_counter() - t
    bad, stored_after_bad, ok, res, secs = asyncio.run(go())
    assert bad.status_code == 422 and stored_after_bad == 0, (bad.status_code, bad.text, stored_after_bad)
    assert ok.status_code == 200, ok.text
    assert [r.status_code for r in res] == [200] * 4, [r.text[:120] for r in res]
    TIMINGS["slow+3fast secs"] = round(secs, 2)
    check_nc(nid, 5)


def test_zz_print_timings():
    print("\nTIMINGS", json.dumps(TIMINGS, indent=1))
    json.dump(TIMINGS, open("/app/test_reports/iter48_upload_timings.json", "w"), indent=1)
