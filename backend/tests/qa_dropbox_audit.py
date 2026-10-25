"""Read-only audit: QA export records that were saved to Dropbox by test runs (no writes)."""
import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv("/app/backend/.env")
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
rows = list(db.dropbox_exports.find({"ref": {"$regex": "^QA"}}, {"ref": 1, "status": 1, "path": 1, "created_at": 1}))
print("QA dropbox_exports:", len(rows))
for r in rows[-8:]:
    print(r.get("ref"), r.get("status"), r.get("path"), r.get("created_at"))
print("QA leftovers users:", db.users.count_documents({"email": {"$regex": r"^qa\."}}),
      "projects:", db.projects.count_documents({"address": "QA TEST 1 Example Road"}))
s = db.settings.find_one({"_id": "global"}) or {}
print("settings:", {k: s.get(k) for k in ("location_scope_enabled", "photo_classification_enabled", "auto_verify")})
print("audit saved_to_dropbox last:", [ (a.get("target"), (a.get("meta") or a.get("details") or {})) for a in db.audit.find({"action": "export.saved_to_dropbox"}).sort("_id", -1).limit(3)])
print("active employees (non-QA):", db.users.count_documents({"role": "employee", "active": True, "email": {"$not": {"$regex": r"^qa\."}}}))
