"""READ-ONLY Dropbox inventory of files created by preview QA runs. Lists, never deletes.
A file is reported as a positively identified test file only if:
  - it sits in a property folder whose name starts with "QA " (synthetic QA addresses), or
  - it sits under _system/projects/<id> or _system/documents/... where <id> is NOT a project/document in the preview database
    AND the folder was created on/after the QA window start.
Everything else is untouched and not listed."""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bson import ObjectId  # noqa: E402
from core import db  # noqa: E402
from dropbox_link import load_dropbox  # noqa: E402
from storage import APP_NAME, storage  # noqa: E402

SINCE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def walk(d, path):
    from dropbox.files import FileMetadata
    out, r = [], d.files_list_folder(path, recursive=True)
    while True:
        out += [e for e in r.entries if isinstance(e, FileMetadata)]
        if not r.has_more:
            return out
        r = d.files_list_folder_continue(r.cursor)


async def main():
    await load_dropbox()
    if not storage.dropbox_ready:
        print("Dropbox not connected in preview")
        return
    d = storage.client()
    projects = {str(p["_id"]) for p in await db.projects.find({}, {"_id": 1}).to_list(100000)}
    docs = {str(x["_id"]) for x in await db.tech_docs.find({}, {"_id": 1}).to_list(100000)}
    root = d.files_list_folder("/" + APP_NAME)
    found, kept = [], []
    for e in root.entries:
        name = e.name
        if name.startswith("QA "):
            found += [(f.path_display, f.id, f.size, str(f.server_modified)) for f in walk(d, e.path_lower)]
    for sub in ("projects", "documents"):
        try:
            r = d.files_list_folder(f"/{APP_NAME}/_system/{sub}")
        except Exception:
            continue
        for e in r.entries:
            known = projects if sub == "projects" else docs
            if sub == "projects" and e.name in known:
                continue
            files = walk(d, e.path_lower)
            if sub == "documents":  # _system/documents/<key>/<doc_id>.pdf
                files_test = [f for f in files if os.path.splitext(f.name)[0] not in docs]
                kept += [f.path_display for f in files if f not in files_test]
                files = files_test
            if not files:
                continue
            if all(f.server_modified.replace(tzinfo=timezone.utc) >= SINCE for f in files) and (sub == "documents" or ObjectId.is_valid(e.name)):
                found += [(f.path_display, f.id, f.size, str(f.server_modified)) for f in files]
            else:
                kept += [f.path_display for f in files]
    json.dump({"test_files": found, "uncertain_kept": kept}, open("/app/test_reports/dropbox_test_inventory.json", "w"), indent=1)
    by = {}
    for p, *_ in found:
        k = "/".join(p.split("/")[:5]) if "/_system/" in p else "/".join(p.split("/")[:3])
        by[k] = by.get(k, 0) + 1
    print("POSITIVELY IDENTIFIED TEST FILES:", len(found), "total bytes", sum(x[2] for x in found))
    for k, v in sorted(by.items()):
        print(f"  {v:4d}  {k}")
    print("UNCERTAIN (left untouched):", len(kept))
    for p in kept[:20]:
        print("  ", p)


asyncio.run(main())
