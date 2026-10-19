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


def get_storage() -> StorageProvider:
    provider = os.environ.get("STORAGE_PROVIDER", "emergent")
    if provider == "emergent":
        return EmergentStorage()
    raise RuntimeError(f"Storage provider '{provider}' not implemented; add a StorageProvider subclass")


storage = get_storage()


def evidence_path(project_id: str, nc_id: str, submission_id: str, file_id: str, variant: str, ext: str) -> str:
    return f"{APP_NAME}/projects/{project_id}/ncs/{nc_id}/submissions/{submission_id}/{file_id}/{variant}.{ext}"
