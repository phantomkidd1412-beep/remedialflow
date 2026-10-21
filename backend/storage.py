"""Storage layer. The app only talks to `StorageProvider`; swap providers via STORAGE_PROVIDER
(emergent today; an S3 / Supabase provider only needs put/get implemented)."""
import os
import logging
from abc import ABC, abstractmethod
from typing import Tuple

import requests
from starlette.concurrency import run_in_threadpool

log = logging.getLogger("storage")
APP_NAME = "remedialflow"


class StorageProvider(ABC):
    name = "abstract"

    @abstractmethod
    def put(self, path: str, data: bytes, content_type: str) -> str: ...

    @abstractmethod
    def get(self, path: str) -> Tuple[bytes, str]: ...

    async def aput(self, path: str, data: bytes, content_type: str) -> str:
        return await run_in_threadpool(self.put, path, data, content_type)

    async def aget(self, path: str) -> Tuple[bytes, str]:
        return await run_in_threadpool(self.get, path)


class EmergentStorage(StorageProvider):
    name = "emergent"

    def __init__(self):
        base = (os.environ.get("INTEGRATION_PROXY_URL") or "").strip() or "https://integrations.emergentagent.com"
        self.url = base.rstrip("/") + "/objstore/api/v1/storage"
        self.key = None

    def _init(self, force=False):
        if self.key and not force:
            return self.key
        r = requests.post(f"{self.url}/init", json={"emergent_key": os.environ["EMERGENT_LLM_KEY"]}, timeout=30)
        r.raise_for_status()
        self.key = r.json()["storage_key"]
        return self.key

    def _req(self, method, path, **kw):
        for attempt in range(2):
            r = requests.request(method, f"{self.url}/objects/{path}",
                                 headers={"X-Storage-Key": self._init(), **kw.pop("headers", {})}, **kw)
            if r.status_code == 503 and attempt == 0:
                self._init(force=True)
                continue
            r.raise_for_status()
            return r

    def put(self, path, data, content_type):
        r = self._req("PUT", path, headers={"Content-Type": content_type}, data=data, timeout=600)
        return r.json()["path"]

    def get(self, path):
        r = self._req("GET", path, timeout=120)
        return r.content, r.headers.get("Content-Type", "application/octet-stream")


class HybridStorage(StorageProvider):
    """Dropbox is the primary permanent store for NEW files once an Admin has connected it; files already in
    Emergent storage stay readable. Append-only by design: this class has no delete/move/overwrite operation."""
    name = "hybrid"
    CHUNK = 32 * 1024 * 1024

    def __init__(self):
        self.emergent = EmergentStorage()
        self.refresh = None
        self._dbx = None

    @property
    def dropbox_ready(self) -> bool:
        return bool(self.refresh and os.environ.get("DROPBOX_APP_KEY"))

    def set_refresh(self, token):
        self.refresh, self._dbx = token, None

    def client(self):
        import dropbox
        if not self._dbx:  # PKCE refresh token: app key only, no app secret needed
            self._dbx = dropbox.Dropbox(oauth2_refresh_token=self.refresh, app_key=os.environ["DROPBOX_APP_KEY"], timeout=300)
        return self._dbx

    def _upload(self, d, dpath, data):
        from dropbox.files import CommitInfo, UploadSessionCursor, WriteMode
        if len(data) <= 140 * 1024 * 1024:
            return d.files_upload(data, dpath, mode=WriteMode.add, autorename=False, mute=True)
        start = d.files_upload_session_start(data[: self.CHUNK])
        cur = UploadSessionCursor(start.session_id, self.CHUNK)
        while len(data) - cur.offset > self.CHUNK:
            d.files_upload_session_append_v2(data[cur.offset: cur.offset + self.CHUNK], cur)
            cur.offset += self.CHUNK
        return d.files_upload_session_finish(data[cur.offset:], cur, CommitInfo(path=dpath, mode=WriteMode.add, autorename=False, mute=True))

    def put(self, path, data, content_type):
        if not self.dropbox_ready:
            return self.emergent.put(path, data, content_type)
        import time
        import dropbox
        d, dpath = self.client(), "/" + path  # unique per file id + variant: never collides
        for attempt in range(4):
            try:
                return "dbx:" + self._upload(d, dpath, data).id
            except dropbox.exceptions.ApiError as e:
                if "conflict" in str(e.error).lower():  # an earlier attempt already committed this exact path
                    return "dbx:" + d.files_get_metadata(dpath).id
                raise
            except dropbox.exceptions.BadInputError as e:
                raise RuntimeError(f"Dropbox refused the upload (check app permissions, then reconnect): {str(e)[:160]}")
            except (dropbox.exceptions.InternalServerError, dropbox.exceptions.RateLimitError, requests.RequestException):
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)

    def get(self, path):
        if not path.startswith("dbx:"):
            return self.emergent.get(path)
        import mimetypes
        md, resp = self.client().files_download(path[4:])
        try:
            data = resp.content
        finally:
            resp.close()
        return data, mimetypes.guess_type(md.name)[0] or "application/octet-stream"


def get_storage() -> StorageProvider:
    provider = os.environ.get("STORAGE_PROVIDER", "emergent")
    if provider in ("emergent", "hybrid", "dropbox"):
        return HybridStorage()
    raise RuntimeError(f"Storage provider '{provider}' not implemented; add a StorageProvider subclass")


storage = get_storage()


SYSTEM = f"{APP_NAME}/_system"  # app-generated files (thumbnails, playback copies, frames, report images)


def evidence_path(project_id: str, nc_id: str, submission_id: str, file_id: str, variant: str, ext: str) -> str:
    return f"{SYSTEM}/projects/{project_id}/ncs/{nc_id}/submissions/{submission_id}/{file_id}/{variant}.{ext}"


def safe_name(text: str, limit: int = 120) -> str:
    """Human-readable Dropbox folder/file name part: strips characters Dropbox/Windows reject."""
    import re
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", text or "")
    s = re.sub(r"\s+", " ", s).strip().strip(".").strip()
    return s[:limit].rstrip(" .") or "Unnamed"


def property_folder_name(address: str, postcode: str) -> str:
    addr, pc = (address or "").strip(), (postcode or "").strip().upper()
    if pc and pc.replace(" ", "") not in addr.upper().replace(" ", ""):
        addr = f"{addr}, {pc}" if addr else pc
    return safe_name(addr)


def media_path(folder: str, ref: str, nc_number: str, kind: str, file_id: str, ext: str, ts: str) -> str:
    """Every installer original lands in the property's Media folder; file id keeps each name unique."""
    stamp = ts[:19].replace("-", "").replace(":", "").replace("T", "-")
    return f"{APP_NAME}/{folder}/Media/{stamp}_{safe_name(ref, 60)}_{safe_name(nc_number, 20)}_{kind}_{file_id}.{ext}"


def export_path(folder: str, ref: str, ts: str, uid: str) -> str:
    stamp = ts[:19].replace("-", "").replace(":", "").replace("T", "-")
    r = safe_name(ref, 80)
    return f"{APP_NAME}/{folder}/{r}/{r}_Evidence_Pack_{stamp}_{uid}.zip"
