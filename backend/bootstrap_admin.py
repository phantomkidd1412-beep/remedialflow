"""One-time production Admin bootstrap (operator command; there is NO HTTP endpoint for this).

Run on the server by the owner:
    ADMIN_BOOTSTRAP_SECRET=<secret set in the deployment env> python bootstrap_admin.py owner@example.com "Owner Name"
The password is typed interactively (never passed on the command line or logged).
Succeeds only once: a consumed marker is written atomically and the command refuses afterwards.
"""
import asyncio
import getpass
import hmac
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from core import User, audit, db, now_iso  # noqa: E402
from auth import ph  # noqa: E402
from security import DEMO_EMAILS  # noqa: E402


async def main(email: str, name: str):
    expected = os.environ.get("ADMIN_BOOTSTRAP_SECRET", "")
    supplied = os.environ.get("BOOTSTRAP_SECRET_INPUT") or getpass.getpass("Bootstrap secret: ")
    if len(expected) < 24 or not hmac.compare_digest(expected, supplied):
        sys.exit("Invalid bootstrap secret (ADMIN_BOOTSTRAP_SECRET must be set, >= 24 chars).")
    if await db.system.find_one({"_id": "admin_bootstrap", "consumed_at": {"$exists": True}}):
        sys.exit("Bootstrap already completed - it is permanently disabled. Use Admin -> Users instead.")
    email = email.strip().lower()
    if email in DEMO_EMAILS or email.endswith("@remedialflow.dev"):
        sys.exit("Use a real owner email address, not a demo account.")
    pw = os.environ.get("BOOTSTRAP_PASSWORD_INPUT") or getpass.getpass("New admin password (min 12 chars): ")
    if len(pw) < 12:
        sys.exit("Password must be at least 12 characters.")
    # claim the one-time slot first (unique _id => only one process can ever win)
    try:
        await db.system.insert_one({"_id": "admin_bootstrap", "consumed_at": now_iso(), "email": email})
    except Exception:
        sys.exit("Bootstrap already completed - it is permanently disabled.")
    existing = await db.users.find_one({"email": email})
    if existing:
        await db.users.update_one({"_id": existing["_id"]}, {"$set": {"role": "admin", "active": True, "password_hash": ph.hash(pw)},
                                                              "$inc": {"token_version": 1}})
        uid = str(existing["_id"])
    else:
        u = User(email=email, name=name, role="admin", password_hash=ph.hash(pw))
        uid = str((await db.users.insert_one(u.to_mongo())).inserted_id)
    await audit("system.admin_bootstrapped", None, f"user:{uid}", {"email": email})
    print(f"Admin ready: {email}. Bootstrap is now permanently disabled. Remove ADMIN_BOOTSTRAP_SECRET from the environment.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
