"""Preview-only probe of the existing technical-document upload API (no AI). Creates a temporary admin + employee,
uploads a synthetic PDF, checks validation/draft/pages/approval, then deletes everything it created.
Usage: EXPO_PUBLIC_BACKEND_URL=... python qa_docs_upload_probe.py"""
import asyncio
import os
import sys

import httpx
import pymupdf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bson import ObjectId  # noqa: E402
from core import db, now_iso  # noqa: E402

BASE = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/") + "/api"
PW = "QaDocs-7731x"
ORIGIN = "https://app.remedialflow.uk"
res = []


def check(name, ok, info=""):
    res.append(ok)
    print(("PASS" if ok else "FAIL"), name, "::", info)


def pdf(pages):
    d = pymupdf.open()
    for i, t in enumerate(pages):
        d.new_page().insert_text((72, 72), f"QA DOC page {i + 1}: {t}")
    return d.tobytes()


async def main():
    from auth import ph
    uids, did = [], None
    for role in ("admin", "employee"):
        uids.append((await db.users.insert_one({"email": f"qa.docs.{role}@example.com", "name": f"QA Docs {role}", "role": role, "active": True,
                                                 "password_hash": ph.hash(PW), "token_version": 0, "created_at": now_iso()})).inserted_id)
    try:
        async with httpx.AsyncClient(timeout=60, headers={"Origin": ORIGIN}) as c:
            tok = {}
            for role in ("admin", "employee"):
                r = await c.post(f"{BASE}/auth/login", json={"email": f"qa.docs.{role}@example.com", "password": PW})
                tok[role] = r.json().get("access_token")
            check("login admin/employee", all(tok.values()))
            H = {"Authorization": f"Bearer {tok['admin']}"}
            body = pdf(["Gas tee clearance must be 250mm from openings", "Ventilation cover fitted", "Sign-off"])
            meta = {"title": "QA Docs Probe PAS", "version": "2026-QA", "category": "pas", "publisher": "QA"}
            r = await c.post(f"{BASE}/admin/documents", data=meta, files={"file": ("qa.pdf", body, "application/pdf")})
            check("no token -> 401", r.status_code == 401, r.status_code)
            r = await c.post(f"{BASE}/admin/documents", data=meta, files={"file": ("qa.pdf", body, "application/pdf")}, headers={"Authorization": f"Bearer {tok['employee']}"})
            check("employee -> 403", r.status_code == 403, r.status_code)
            r = await c.post(f"{BASE}/admin/documents", data={"title": "x"}, files={"file": ("qa.pdf", body, "application/pdf")}, headers=H)
            check("missing version -> 422", r.status_code == 422, r.status_code)
            r = await c.post(f"{BASE}/admin/documents", data=meta, files={"file": ("x.pdf", b"not a pdf", "application/pdf")}, headers=H)
            check("non-PDF -> 400", r.status_code == 400, r.text[:80])
            r = await c.post(f"{BASE}/admin/documents", data=meta, files={"file": ("qa.pdf", body, "application/pdf")}, headers=H)
            d = r.json() if r.status_code == 200 else {}
            did = d.get("id")
            check("upload -> 200 + CORS header", r.status_code == 200 and r.headers.get("access-control-allow-origin") == ORIGIN, f"{r.status_code} {r.headers.get('access-control-allow-origin')}")
            check("draft status, 3 pages, text ok", d.get("status") == "draft" and d.get("pages") == 3 and d.get("text_pages") == 3 and d.get("extract_status") == "ok", {k: d.get(k) for k in ("status", "pages", "text_pages", "extract_status")})
            n = await db.tech_doc_pages.count_documents({"doc_id": did})
            check("page-by-page text stored", n == 3, n)
            f = await db.files.find_one({"doc_id": did})
            check("PDF stored (file record + path)", bool(f and f.get("paths", {}).get("original")), (f or {}).get("paths"))
            r = await c.post(f"{BASE}/admin/documents", data=meta, files={"file": ("qa.pdf", body, "application/pdf")}, headers=H)
            check("same PDF again -> 409 duplicate", r.status_code == 409, r.text[:90])
            r = await c.get(f"{BASE}/admin/documents/{did}/search", params={"q": "250mm"}, headers=H)
            check("search finds page", r.status_code == 200 and "250" in r.text, r.text[:120])
            r = await c.put(f"{BASE}/admin/documents/{did}/status", json={"status": "approved"}, headers={"Authorization": f"Bearer {tok['employee']}"})
            check("employee cannot approve -> 403", r.status_code == 403, r.status_code)
            r = await c.put(f"{BASE}/admin/documents/{did}/status", json={"status": "approved"}, headers=H)
            check("admin approves -> approved", r.status_code == 200 and r.json().get("status") == "approved", r.json().get("status"))
            r = await c.get(f"{BASE}/documents/{did}/pages/1", headers=H)
            check("page 1 readable", r.status_code == 200, r.status_code)
            pre = await c.options(f"{BASE}/admin/documents", headers={"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"})
            print("INFO preview CORS preflight from desktop origin:", pre.status_code, pre.headers.get("access-control-allow-origin"))
    finally:
        if did:
            await db.tech_doc_pages.delete_many({"doc_id": did})
            await db.files.delete_many({"doc_id": did})
            await db.tech_docs.delete_many({"_id": ObjectId(did)})
        await db.sessions.delete_many({"user_id": {"$in": [str(u) for u in uids]}})
        await db.users.delete_many({"_id": {"$in": uids}})
        print("CLEANUP docs", 1 if did else 0, "users", len(uids))
    print(f"RESULT {sum(res)}/{len(res)} passed")


asyncio.run(main())
