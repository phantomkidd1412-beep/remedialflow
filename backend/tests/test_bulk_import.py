"""Bulk TrustMark import: rule parser, duplicate skipping, Unassigned creation, idempotent confirm.
Storage and the AI extractor are mocked; synthetic records only (no credits, no real files)."""
import asyncio
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fastapi import BackgroundTasks, UploadFile  # noqa: E402
from core import User, db  # noqa: E402

HERE = os.path.dirname(__file__)
import uuid  # noqa: E402
TM = open(os.path.join(HERE, "..", "..", "tests", "p419977.pdf"), "rb").read() + f"\n%qa-{uuid.uuid4()}\n".encode()  # unique hash vs preview data
OTHER = open(os.path.join(HERE, "..", "..", "tests", "sample_inspection_report.pdf"), "rb").read()


def go(c):
    return asyncio.get_event_loop().run_until_complete(c)


@pytest.fixture
def env(monkeypatch):
    import server
    import bulk_import
    from storage import storage
    blobs = {}

    async def aput(path, data, ctype=None):
        blobs[path] = data
        return path

    async def aget(path):
        return blobs[path], "image/jpeg"

    monkeypatch.setattr(storage, "aput", aput)
    monkeypatch.setattr(storage, "aget", aget)
    ai_calls = []

    class FakeExtractor:
        async def extract(self, path, text):
            ai_calls.append(path)
            return {"ref": "QA-BULK-AI-1", "address": "5 Oak Avenue, St Albans", "postcode": "AL3 5PQ", "nc_count_stated": 1,
                    "ncs": [{"description": "Loft insulation compressed", "original_text": "Loft insulation compressed", "required_work": "",
                             "safety_critical": False, "pages": [1]}], "confidence": 0.8, "notes": ""}

    monkeypatch.setattr(server, "get_pdf_extractor", lambda: FakeExtractor())
    pushes = []

    async def fake_notify(*a, **k):
        pushes.append(a)

    monkeypatch.setattr(server, "notify", fake_notify)
    real_find = bulk_import.find_duplicate

    async def only_test_projects(draft):  # ignore older preview projects at the same address
        hit = await real_find(draft)
        return hit if hit and hit["ref"] in ("P419977", "QA-BULK-AI-1") else None

    monkeypatch.setattr(server, "find_duplicate", only_test_projects)
    monkeypatch.setattr(bulk_import, "find_duplicate", only_test_projects)
    assert not go(db.projects.find_one({"ref": {"$in": ["P419977", "QA-BULK-AI-1"]}})), "preview already has these refs"
    admin = User(id="qa-bulk-admin", email="qa-bulk@example.com", name="QA Bulk", role="admin")
    yield server, bulk_import, admin, ai_calls, pushes
    pids = [str(p["_id"]) for p in go(db.projects.find({"ref": {"$in": ["P419977", "QA-BULK-AI-1"]}}, {"_id": 1}).to_list(10))]
    go(db.ncs.delete_many({"project_id": {"$in": pids}}))
    go(db.files.delete_many({"project_id": {"$in": pids}}))
    go(db.projects.delete_many({"ref": {"$in": ["P419977", "QA-BULK-AI-1"]}}))
    go(db.imports.delete_many({"uploaded_by": admin.id}))
    go(db.bulk_imports.delete_many({"created_by": admin.id}))


async def upload(bi, bid, data, name, admin):
    bg = BackgroundTasks()
    r = await bi.add_file(bid, bg, UploadFile(io.BytesIO(data), filename=name), admin)
    await bg()
    return r


def test_parser_reads_trustmark_layout():
    from media import pdf_text_and_images
    from report_import import parse_trustmark
    d = parse_trustmark(pdf_text_and_images(TM)[0])
    assert d["ref"] == "P419977" and d["postcode"] == "LU1 1TD" and d["address"] == "280 Dallow Road, Luton, Bedfordshire"
    assert [n["description"] for n in d["ncs"]] == ["EWI - Gas entry tee clearances", "EWI - Weathering concern", "EWI - Other Observation"]
    assert d["ncs"][0]["safety_critical"] is True and d["ncs"][2]["original_text"].endswith("is draughty")
    assert parse_trustmark(pdf_text_and_images(OTHER)[0]) is None


def test_bulk_flow(env):
    server, bi, admin, ai_calls, pushes = env
    bid = go(bi.create_batch(admin))["id"]
    assert go(upload(bi, bid, TM, "a.pdf", admin))["status"] == "processing"
    assert go(upload(bi, bid, TM, "a-copy.pdf", admin))["status"] == "skipped"  # same file twice
    go(upload(bi, bid, OTHER, "other.pdf", admin))
    items = go(bi.get_batch(bid, admin))["items"]
    by = {i["filename"]: i for i in items}
    assert by["a.pdf"]["status"] == "ready" and by["a.pdf"]["method"] == "rules" and by["a.pdf"]["nc_count"] == 3
    assert by["a-copy.pdf"]["duplicate"]["reason"] == "Same PDF already in this upload"
    assert by["other.pdf"]["method"] == "ai" and len(ai_calls) == 1  # AI only for the non-standard layout

    res = go(bi.confirm_batch(bid, BackgroundTasks(), admin))
    assert {c["ref"] for c in res["created"]} == {"P419977", "QA-BULK-AI-1"}
    p = go(db.projects.find_one({"ref": "P419977"}))
    assert p["installer_id"] is None and p["source_report"]["filename"] == "a.pdf"
    ncs = go(db.ncs.find({"project_id": str(p["_id"])}).to_list(10))
    assert len(ncs) == 3 and all(n["assigned_installer_id"] is None for n in ncs)
    assert pushes == []  # no assignment alerts on import
    # confirming again creates nothing
    again = go(bi.confirm_batch(bid, BackgroundTasks(), admin))
    assert again["created"] == [] and go(db.projects.count_documents({"ref": "P419977"})) == 1

    # a new upload of the same PDF is skipped with the matching project shown
    bid2 = go(bi.create_batch(admin))["id"]
    go(upload(bi, bid2, TM, "again.pdf", admin))
    it = go(bi.get_batch(bid2, admin))["items"][0]
    assert it["status"] == "skipped" and it["duplicate"]["ref"] == "P419977"
