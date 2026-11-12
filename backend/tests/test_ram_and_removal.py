"""Installer photo removal before NC submission + RAM-bounded upload/pack paths. Synthetic records, fake storage, no AI."""
import asyncio
import io
import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bson import ObjectId  # noqa: E402
from fastapi import BackgroundTasks, HTTPException, UploadFile  # noqa: E402
from PIL import Image  # noqa: E402

from core import User, db, now_iso  # noqa: E402


def go(c):
    return asyncio.get_event_loop().run_until_complete(c)


def jpeg(seed=1, size=(1600, 1200)):
    b = io.BytesIO()
    Image.effect_noise(size, 40 + seed).convert("RGB").save(b, "JPEG", quality=90)
    return b.getvalue()


@pytest.fixture
def env(monkeypatch, tmp_path):
    import server
    from storage import storage
    blobs = {}

    def put(path, data, ct=None):
        blobs[path] = bytes(data)
        return "fake:" + path

    def get(path):
        return blobs[path[5:]], "image/jpeg"

    def put_file(path, src, ct=None):
        blobs[path] = open(src, "rb").read()
        return "fake:" + path

    def get_file(path, dst):
        open(dst, "wb").write(blobs[path[5:]])

    for k, v in {"put": put, "get": get, "put_file": put_file, "get_file": get_file}.items():
        monkeypatch.setattr(storage, k, v)
    monkeypatch.setattr(server, "PACK_DIR", tmp_path / "packs")

    async def no_ai(*a, **k):
        return None
    import ai
    monkeypatch.setattr(ai, "arrange_photos", no_ai)
    ids = {}
    for role in ("installer", "employee"):
        ids[role] = str(go(db.users.insert_one({"email": f"qa-del-{role}-{ObjectId()}@example.com", "role": role, "active": True, "name": role})).inserted_id)
    other = str(go(db.users.insert_one({"email": f"qa-del-other-{ObjectId()}@example.com", "role": "installer", "active": True, "name": "o"})).inserted_id)
    pid = str(go(db.projects.insert_one({"ref": f"QA-DEL-{ObjectId()}", "address": "2 Test Rd, Testtown", "postcode": "TE2 2ST", "installer_id": ids["installer"],
                                         "status": "in_progress", "created_at": now_iso(), "deleted": None, "require_video": False})).inserted_id)
    nid = str(go(db.ncs.insert_one({"project_id": pid, "number": "NC-01", "description": "Gap", "required_work": "", "status": "open", "safety_critical": False,
                                    "assigned_installer_id": ids["installer"], "requires_stages": False, "created_at": now_iso()})).inserted_id)
    users = {r: User(id=i, email="x@x", name=r, role=r) for r, i in ids.items()}
    users["other"] = User(id=other, email="o@x", name="o", role="installer")
    yield server, users, pid, nid, blobs
    go(db.files.delete_many({"project_id": pid}))
    go(db.submissions.delete_many({"project_id": pid}))
    go(db.ncs.delete_many({"project_id": pid}))
    go(db.projects.delete_many({"_id": ObjectId(pid)}))
    go(db.users.delete_many({"_id": {"$in": [ObjectId(i) for i in [*ids.values(), other]]}}))


def upload(server, nid, u, data, cid, kind="photo"):
    bg = BackgroundTasks()
    f = go(server.upload_evidence(nid, bg, UploadFile(io.BytesIO(data), filename="x"), json.dumps({"source": "gallery", "client_id": cid, "kind": kind}), u))
    go(bg())
    return f


def test_installer_removes_draft_before_submit_only(env):
    server, u, pid, nid, blobs = env
    a, b, c = (upload(server, nid, u["installer"], jpeg(i), f"c{i}")["id"] for i in range(3))
    go(db.ncs.update_one({"_id": ObjectId(nid)}, {"$set": {"evidence_selection": {"ids": [a, b, c], "ai_ids": [b]}}}))
    with pytest.raises(HTTPException) as e:  # another installer cannot remove it
        go(server.discard_evidence(b, u["other"]))
    assert e.value.status_code in (403, 404)
    go(server.discard_evidence(b, u["installer"]))
    f = go(db.files.find_one({"_id": ObjectId(b)}))
    assert f["status"] == "discarded" and f["discarded_by"] == u["installer"].id
    assert any(k.endswith(".jpg") for k in blobs) and f["paths"]["original"][5:] in blobs  # original kept in storage
    sel = go(db.ncs.find_one({"_id": ObjectId(nid)}))["evidence_selection"]
    assert sel["ids"] == [a, c] and sel["ai_ids"] == []  # order of the remaining photos preserved
    assert go(db.audit.find_one({"event": "evidence.discarded", "data.file_id": b}))
    # after submission the evidence is protected
    go(db.files.update_many({"_id": {"$in": [ObjectId(a), ObjectId(c)]}}, {"$set": {"status": "submitted"}}))
    for fid in (a, c):
        with pytest.raises(HTTPException) as e:
            go(server.discard_evidence(fid, u["installer"]))
        assert e.value.status_code == 400
    # the removed photo never reaches the PDF
    p = go(db.projects.find_one({"_id": ObjectId(pid)}))
    dest = str(server.PACK_DIR / "t.zip")
    server.PACK_DIR.mkdir(parents=True, exist_ok=True)
    go(server.build_evidence_pack(pid, p, dest))
    files = go(db.files.find({"nc_id": nid, "status": {"$in": ["submitted", "draft", "superseded"]}}).to_list(10))
    assert b not in {str(x["_id"]) for x in files}
    assert any(n.endswith(".pdf") for n in zipfile.ZipFile(dest).namelist())


def test_video_upload_streams_and_pack_from_disk(env):
    server, u, pid, nid, blobs = env
    import subprocess
    from media import FFMPEG
    out = os.path.join(str(server.PACK_DIR.parent), "v.mp4")
    subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=2", "-c:v", "libx264", "-preset", "ultrafast", out],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    data = open(out, "rb").read()
    f = upload(server, nid, u["installer"], data, "vid1", "video")
    d = go(db.files.find_one({"_id": ObjectId(f["id"])}))
    assert d["size"] == len(data) and blobs[d["paths"]["original"][5:]] == data  # original stored byte-for-byte
    assert d["processing"] == "ready" and d["playback"] == "ready"
    assert not list((server.MEDIA_CACHE / "uploads").glob("*.part"))  # temp spool cleaned up
    again = upload(server, nid, u["installer"], data, "vid1", "video")  # idempotent retry
    assert again["id"] == f["id"]
    go(db.files.update_one({"_id": d["_id"]}, {"$set": {"status": "submitted"}}))
    p = go(db.projects.find_one({"_id": ObjectId(pid)}))
    path, fp = go(server.cached_pack(pid, p))
    z = zipfile.ZipFile(path)
    vids = [n for n in z.namelist() if n.startswith("Videos/")]
    assert len(vids) == 1 and z.read(vids[0]) == data
