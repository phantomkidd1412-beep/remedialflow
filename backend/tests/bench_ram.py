"""Peak-RAM benchmark for the heavy evidence paths (preview only, synthetic data, no AI, no real storage).
Storage is replaced by a disk-backed fake so Dropbox/Emergent storage are never touched.
Usage: python tests/bench_ram.py <scenario>   scenarios: photos4 | video | pack
Prints peak RSS growth (MB) of this process (ffmpeg children excluded) and wall time."""
import asyncio
import io
import os
import subprocess
import sys
import tempfile
import threading
import time

import psutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bson import ObjectId  # noqa: E402
from fastapi import BackgroundTasks, UploadFile  # noqa: E402
from PIL import Image  # noqa: E402

import server  # noqa: E402
from core import User, db  # noqa: E402
from storage import storage  # noqa: E402

ROOT = tempfile.mkdtemp(prefix="rf_bench_")


def _p(path):
    return os.path.join(ROOT, path.replace("/", "_").replace(":", "_"))


def put(path, data, ct="application/octet-stream"):
    with open(_p(path), "wb") as fh:
        fh.write(data)
    return "fake:" + path


def get(path):
    with open(_p(path[5:]), "rb") as fh:
        return fh.read(), "image/jpeg"


storage.put, storage.get = put, get
if hasattr(storage, "put_file"):
    def put_file(path, src, ct="application/octet-stream"):
        import shutil
        shutil.copyfile(src, _p(path))
        return "fake:" + path

    def get_file(path, dst):
        import shutil
        shutil.copyfile(_p(path[5:]), dst)
    storage.put_file, storage.get_file = put_file, get_file


class Peak:
    def __init__(self):
        self.proc, self.peak, self.stop = psutil.Process(), 0, False

    def run(self):
        while not self.stop:
            self.peak = max(self.peak, self.proc.memory_info().rss)
            time.sleep(0.01)


def jpeg(seed, w=4032, h=3024):
    img = Image.effect_noise((w // 4, h // 4), 60 + seed).convert("RGB").resize((w, h))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def video(mb):
    out = os.path.join(ROOT, "v.mp4")
    ff = server.FFMPEG if hasattr(server, "FFMPEG") else __import__("media").FFMPEG
    secs = max(4, mb // 4)
    subprocess.run([ff, "-y", "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=30:duration={secs}", "-f", "lavfi", "-i", "anoisesrc",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-b:v", f"{mb * 8 // secs}M", "-c:a", "aac", out],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return open(out, "rb").read()


async def setup():
    uid = str((await db.users.insert_one({"email": "bench@ram.qa", "role": "admin", "active": True, "name": "Bench"})).inserted_id)
    pid = str((await db.projects.insert_one({"ref": f"QA-RAM-{ObjectId()}", "address": "1 Bench St, Testtown", "postcode": "TE1 1ST",
                                             "installer_id": None, "status": "open", "created_at": server.now_iso(), "deleted": None})).inserted_id)
    nid = str((await db.ncs.insert_one({"project_id": pid, "number": "NC-01", "description": "Bench", "required_work": "", "status": "open",
                                        "safety_critical": False, "assigned_installer_id": None, "created_at": server.now_iso()})).inserted_id)
    return User(id=uid, email="bench@ram.qa", name="Bench", role="admin"), pid, nid


async def cleanup(uid, pid):
    await db.files.delete_many({"project_id": pid})
    await db.submissions.delete_many({"project_id": pid})
    await db.ncs.delete_many({"project_id": pid})
    await db.projects.delete_many({"_id": ObjectId(pid)})
    await db.users.delete_many({"_id": ObjectId(uid)})


async def upload(nid, u, data, name, meta):
    bg = BackgroundTasks()
    import json
    f = await server.upload_evidence(nid, bg, UploadFile(io.BytesIO(data), filename=name, size=len(data)), json.dumps(meta), u)
    await bg()
    return f


async def main(scenario):
    u, pid, nid = await setup()
    payloads = [jpeg(i) for i in range(4)] if scenario in ("photos4", "pack", "stamp4") else []
    vid = video(int(os.environ.get("BENCH_VIDEO_MB", "120"))) if scenario in ("video", "pack") else None
    import gc
    gc.collect()
    pk = Peak()
    base = pk.proc.memory_info().rss
    th = threading.Thread(target=pk.run, daemon=True)
    th.start()
    t0 = time.time()
    try:
        if scenario == "stamp4":  # in-app camera path: visible stamp + display + thumb, 4 at once
            from media import process_photo
            from starlette.concurrency import run_in_threadpool
            meta = {"captured_at": "2026-06-01T10:00:00Z", "lat": 51.5, "lng": -0.1, "accuracy_m": 5, "project_ref": "QA", "postcode": "TE1", "nc_number": "NC-01", "source": "in_app_camera"}
            async def one(d):  # same bound as _process_file
                async with server._img_slots:
                    return await run_in_threadpool(process_photo, d, meta)
            outs = await asyncio.gather(*[one(d) for d in payloads])
            assert all(set(o) == {"stamped", "display", "thumb"} for o in outs)
        if scenario in ("photos4", "pack"):
            await asyncio.gather(*[upload(nid, u, d, f"p{i}.jpg", {"source": "gallery", "client_id": f"bench-{pid}-{i}"}) for i, d in enumerate(payloads)])
        if vid:
            await upload(nid, u, vid, "v.mp4", {"kind": "video", "client_id": f"bench-{pid}-v"})
        if scenario == "pack":
            payloads, vid = [], None
            gc.collect()
            base = pk.proc.memory_info().rss
            pk.peak = base
            t0 = time.time()
            p = await db.projects.find_one({"_id": ObjectId(pid)})
            await server.build_evidence_pack(pid, p, os.path.join(ROOT, "pack.zip"))
        dt = time.time() - t0
        st = [(d["kind"], d.get("processing"), d.get("playback"), (d.get("processing_error") or d.get("playback_error") or "")[:80])
              for d in await db.files.find({"project_id": pid}).to_list(20)]
        print("files:", st)
    finally:
        pk.stop = True
        th.join()
        await cleanup(u.id, pid)
    print(f"{scenario}: peak RSS growth {(pk.peak - base) / 2**20:.0f} MB, time {dt:.1f}s")


if __name__ == "__main__":
    os.environ.setdefault("AI_MOCK", "1")
    server.arrange_photos = None
    import ai

    async def no_ai(*a, **k):
        return None
    ai.arrange_photos = no_ai
    asyncio.get_event_loop().run_until_complete(main(sys.argv[1]))
