"""PREVIEW-ONLY visual QA seed: one installer + dummy project with 3 NCs (Emergent preview storage; Dropbox not connected).
python tests/qa_preview_installer.py seed | cleanup"""
import asyncio
import io
import json
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bson import ObjectId  # noqa: E402
from PIL import Image  # noqa: E402

from auth import ph  # noqa: E402
from core import db, now_iso  # noqa: E402
from storage import storage  # noqa: E402

EMAIL, PW, REF = "qa.installer.preview@example.com", "PreviewQA!2026", "QA-PREVIEW-UI"
BASE = "http://localhost:8001"


async def seed():
    assert not storage.dropbox_ready, "Dropbox connected: refusing to upload test photos"
    await cleanup()
    uid = str((await db.users.insert_one({"email": EMAIL, "name": "QA Installer", "role": "installer", "active": True, "password_hash": ph.hash(PW),
                                          "token_version": 0, "created_at": now_iso(), "qa": "preview-ui"})).inserted_id)
    pid = str((await db.projects.insert_one({"ref": REF, "address": "12 Station Road, Luton, Bedfordshire", "postcode": "LU1 2AB", "installer_id": uid,
                                             "status": "in_progress", "created_at": now_iso(), "deleted": None, "qa": "preview-ui"})).inserted_id)
    ncs = [("NC-01", "Gas entry tee clearance under 250mm", "open"), ("NC-02", "Render bead missing at window head", "employee_review"),
           ("NC-03", "Vent cover not fitted", "more_evidence")]
    ids = {}
    for num, desc, st in ncs:
        ids[num] = str((await db.ncs.insert_one({"project_id": pid, "number": num, "description": desc, "required_work": "Fix and photograph", "status": st,
                                                 "safety_critical": num == "NC-01", "assigned_installer_id": uid, "requires_stages": False,
                                                 "created_at": now_iso(), "qa": "preview-ui"})).inserted_id)
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", json={"email": EMAIL, "password": PW})
    r.raise_for_status()
    tok = r.json().get("access_token") or r.json().get("token")
    h = {"Authorization": f"Bearer {tok}"} if tok else {}
    for i, col in enumerate([(200, 60, 40), (40, 120, 200)]):
        b = io.BytesIO()
        Image.new("RGB", (800, 600), col).save(b, "JPEG")
        r = s.post(f"{BASE}/api/ncs/{ids['NC-01']}/evidence", headers=h, files={"file": (f"p{i}.jpg", b.getvalue(), "image/jpeg")},
                   data={"meta": json.dumps({"source": "gallery", "client_id": f"qa-preview-{i}"})})
        print("upload", r.status_code)
    print(json.dumps({"email": EMAIL, "project_id": pid, "ncs": ids}))


async def cleanup():
    projs = [str(p["_id"]) async for p in db.projects.find({"ref": REF})]
    for f in await db.files.find({"project_id": {"$in": projs}}).to_list(100):
        for v in (f.get("paths") or {}).values():
            try:
                if not v.startswith("dbx:"):
                    storage.emergent.delete(v) if hasattr(storage.emergent, "delete") else None
            except Exception:
                pass
    await db.files.delete_many({"project_id": {"$in": projs}})
    await db.submissions.delete_many({"project_id": {"$in": projs}})
    await db.ncs.delete_many({"project_id": {"$in": projs}})
    await db.notifications.delete_many({"project_id": {"$in": projs}})
    await db.projects.delete_many({"ref": REF})
    u = await db.users.find_one({"email": EMAIL})
    if u:
        await db.sessions.delete_many({"user_id": str(u["_id"])})
        await db.users.delete_one({"_id": u["_id"]})


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(seed() if sys.argv[1] == "seed" else cleanup())
