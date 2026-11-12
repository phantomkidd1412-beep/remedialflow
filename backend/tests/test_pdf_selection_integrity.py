"""Employee-selected photos must ALL appear in the PDF, in order, whatever AI recommends. Dummy data, fake storage,
mocked AI (no credits). Each photo has a unique solid colour so the PDF's embedded images can be identified."""
import asyncio
import io
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bson import ObjectId  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from PIL import Image  # noqa: E402

from core import db, now_iso  # noqa: E402

COLOURS = [(230, 25, 75), (60, 180, 75), (255, 225, 25), (0, 130, 200), (245, 130, 48), (145, 30, 180), (70, 240, 240),
           (240, 50, 230), (128, 128, 0), (0, 0, 128), (170, 110, 40), (0, 128, 128)]


def go(c):
    return asyncio.get_event_loop().run_until_complete(c)


def jpeg(col):
    b = io.BytesIO()
    Image.new("RGB", (400, 300), col).save(b, "JPEG", quality=95)
    return b.getvalue()


def pdf_colours(pdf: bytes):
    import pymupdf
    out = []
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    for page in doc:
        for info in page.get_image_info(xrefs=True):  # placement order on the page
            pix = pymupdf.Pixmap(doc, info["xref"])
            r, g, b = pix.pixel(pix.width // 2, pix.height // 2)[:3]
            out.append(min(range(len(COLOURS)), key=lambda k: sum((x - y) ** 2 for x, y in zip(COLOURS[k], (r, g, b)))))
    return out


@pytest.fixture
def env(monkeypatch, tmp_path):
    import server
    import ai
    from storage import storage
    blobs = {}
    monkeypatch.setattr(storage, "aget", lambda path: _aget(blobs, path))
    monkeypatch.setattr(server, "PACK_DIR", tmp_path / "packs")
    calls = []

    async def fake_arrange(*a, **k):  # AI must never be needed when a selection exists
        calls.append("arrange")
        return None
    monkeypatch.setattr(ai, "arrange_photos", fake_arrange)
    pid = str(go(db.projects.insert_one({"ref": f"QA-SEL-{ObjectId()}", "address": "3 Pdf Lane, Testtown", "postcode": "TE3 3ST", "installer_id": None,
                                         "status": "in_progress", "created_at": now_iso(), "deleted": None})).inserted_id)
    ncs, files = {}, {}
    for num, count in (("NC-01", 10), ("NC-02", 3)):
        nid = str(go(db.ncs.insert_one({"project_id": pid, "number": num, "description": f"Test {num}", "required_work": "", "status": "employee_review",
                                        "safety_critical": False, "created_at": now_iso()})).inserted_id)
        ncs[num] = nid
        for i in range(count):
            c = i if num == "NC-01" else 10 + (i % 2)
            fid = ObjectId()
            path = f"qa/{fid}.jpg"
            blobs[path] = jpeg(COLOURS[c])
            ai_bad = num == "NC-01" and i in (1, 6)  # AI thinks these are unsuitable
            go(db.files.insert_one({"_id": fid, "project_id": pid, "nc_id": nid, "kind": "photo", "status": "submitted", "source": "in_app_camera",
                                    "paths": {"original": path, "stamped": path}, "server_received_at": f"2026-06-01T10:00:{i:02d}Z",
                                    "classification": {"relevant": not ai_bad, "stage": "after"}, "rejected_items": ["x"] if ai_bad else [],
                                    "pdf_include": False if ai_bad else None, "original_name": f"{num}-{i}.jpg"}))
            files[(num, i)] = str(fid)
    yield server, pid, ncs, files, blobs, calls
    go(db.files.delete_many({"project_id": pid}))
    go(db.ncs.delete_many({"project_id": pid}))
    go(db.projects.delete_many({"_id": ObjectId(pid)}))


async def _aget(blobs, path):
    if path not in blobs:
        raise RuntimeError("not found")
    return blobs[path], "image/jpeg"


def build(server, pid):
    p = go(db.projects.find_one({"_id": ObjectId(pid)}))
    path, fp = go(server.cached_pack(pid, p))
    z = zipfile.ZipFile(path)
    return {n.split("_")[0]: pdf_colours(z.read(n)) for n in z.namelist() if n.endswith(".pdf")}, fp


def test_every_selected_photo_in_pdf_in_order(env):
    server, pid, ncs, files, blobs, calls = env
    # AI recommended 4 (0,2,3,4); employee picks 7 incl. non-recommended and AI-"unsuitable" ones (1, 6), in their own order
    pick = [9, 1, 0, 6, 5, 3, 8]
    go(db.ncs.update_one({"_id": ObjectId(ncs["NC-01"])}, {"$set": {
        "photo_recs": {"items": {f"i{k}": {"file_id": files[("NC-01", k)], "source": "ai", "state": "pending"} for k in (0, 2, 3, 4)}},
        "evidence_selection": {"ids": [files[("NC-01", k)] for k in pick] + [files[("NC-01", 9)]], "ai_ids": [files[("NC-01", 0)]], "seq": 5}}}))
    go(db.ncs.update_one({"_id": ObjectId(ncs["NC-02"])}, {"$set": {"evidence_selection": {"ids": [files[("NC-02", 2)], files[("NC-02", 0)]], "seq": 1}}}))
    pdfs, fp = build(server, pid)
    assert pdfs["NC-01"] == pick  # all 7, staff order, no duplicates (repeated id shown once), unselected excluded
    assert pdfs["NC-02"] == [10, 10]  # second NC: its own 2 selected photos
    assert calls == []  # no AI involved in an explicit selection
    again, fp2 = build(server, pid)  # regenerate / download again: identical selection
    assert again == pdfs and fp2 == fp
    # a selected photo marked superseded by a later resubmission still appears (employee choice wins)
    go(db.files.update_one({"_id": ObjectId(files[("NC-01", 5)])}, {"$set": {"status": "superseded"}}))
    assert build(server, pid)[0]["NC-01"] == pick


def test_unloadable_selected_photo_blocks_report(env):
    server, pid, ncs, files, blobs, calls = env
    go(db.ncs.update_one({"_id": ObjectId(ncs["NC-01"])}, {"$set": {"evidence_selection": {"ids": [files[("NC-01", k)] for k in (0, 1, 2)], "seq": 1}}}))
    f = go(db.files.find_one({"_id": ObjectId(files[("NC-01", 1)])}))
    del blobs[f["paths"]["original"]]  # storage lost it
    p = go(db.projects.find_one({"_id": ObjectId(pid)}))
    with pytest.raises(HTTPException) as e:
        go(server.cached_pack(pid, p))
    assert e.value.status_code == 422 and "NC-01" in e.value.detail and files[("NC-01", 1)] in e.value.detail
    assert not list((server.PACK_DIR).glob("*.zip"))  # nothing cached as a "successful" report
    blobs[f["paths"]["original"]] = b"not a jpeg"  # present but corrupt
    with pytest.raises(HTTPException):
        go(server.cached_pack(pid, p))
