"""Deactivates the development demo accounts and revokes their sessions (nothing is deleted).
Run ONLY with the owner's approval, after the real Admin exists:  python disable_demo_accounts.py --yes
In APP_ENV=production demo accounts are already refused at login even before this is run."""
import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from core import audit, db  # noqa: E402
from security import DEMO_EMAILS  # noqa: E402


async def main():
    if not await db.users.find_one({"role": "admin", "active": True, "email": {"$nin": list(DEMO_EMAILS)}}):
        sys.exit("Create the real Admin first (bootstrap_admin.py) - refusing to lock you out.")
    for email in DEMO_EMAILS:
        u = await db.users.find_one_and_update({"email": email, "active": True}, {"$set": {"active": False}, "$inc": {"token_version": 1}})
        if u:
            await db.sessions.update_many({"user_id": str(u["_id"])}, {"$set": {"revoked": True}})
            await audit("user.demo_disabled", None, f"user:{u['_id']}", {"email": email})
            print("disabled", email)


if __name__ == "__main__":
    if "--yes" not in sys.argv:
        sys.exit(__doc__)
    asyncio.run(main())
