// Offline-first evidence queue. Items persist on-device and sync sequentially; an item is only
// removed after the server confirms receipt.
import { createContext, PropsWithChildren, useCallback, useContext, useEffect, useRef, useState } from "react";
import { AppState, Platform } from "react-native";
import { storage } from "@/src/utils/storage";
import { uploadForm } from "@/src/utils/upload";
import { authed, BASE, readError } from "@/src/api";
import { queryClient } from "@/src/query-client";
import { useAuth } from "@/src/auth";

export type EvidenceMeta = {
  kind: "photo" | "video";
  source: "in_app_camera" | "gallery";
  captured_at: string | null;
  tz: string;
  tz_offset_min: number;
  lat: number | null;
  lng: number | null;
  accuracy_m: number | null;
  duration_s?: number | null;
};

export type QueueItem = {
  id: string;
  type: "upload" | "submit";
  projectId: string;
  ncId: string;
  uri?: string;
  mime?: string;
  meta?: EvidenceMeta;
  userId?: string; // owner: items only sync for the installer who captured them
  status: "pending" | "uploading" | "uploaded" | "failed";
  error?: string;
  createdAt: string;
};

export const STATUS_LABEL: Record<QueueItem["status"], string> = { pending: "Pending Upload", uploading: "Uploading", uploaded: "Uploaded", failed: "Upload Failed" };

/** Server refused the item: keep it on the phone and tell the installer what to do. */
function refusal(status: number, msg: string) {
  if (status === 403 || status === 404) return "This NC is no longer assigned to you or the project was removed. Evidence is kept on this phone - contact the office.";
  if (status === 409) return `${msg}. Evidence is kept on this phone - contact the office.`;
  return msg;
}

type Ctx = {
  items: QueueItem[];
  syncing: boolean;
  progress: { done: number; total: number } | null;
  retryFailed: () => void;
  enqueueUpload: (projectId: string, ncId: string, uri: string, mime: string, meta: EvidenceMeta) => Promise<void>;
  enqueueSubmit: (projectId: string, ncId: string) => Promise<void>;
  discard: (id: string) => Promise<void>;
  clearAll: () => Promise<void>;
  retry: (id: string) => void;
  syncNow: () => void;
};

const KEY = "evidence_queue";
const QueueCtx = createContext<Ctx>(null as any);
export const uid = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

function persistFile(uri: string, id: string, mime: string): string {
  if (Platform.OS === "web") return uri;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { File, Directory, Paths } = require("expo-file-system");
    const dir = new Directory(Paths.document, "evidence");
    if (!dir.exists) dir.create({ intermediates: true, idempotent: true });
    const dest = new File(dir, `${id}.${mime.includes("video") ? "mp4" : "jpg"}`);
    new File(uri).copy(dest);
    return dest.uri;
  } catch {
    return uri;
  }
}

function removeFile(uri?: string) {
  if (!uri || Platform.OS === "web" || !uri.includes("/evidence/")) return;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { File } = require("expo-file-system");
    new File(uri).delete();
  } catch {}
}

export function QueueProvider({ children }: PropsWithChildren) {
  const { user } = useAuth();
  const [items, setItems] = useState<QueueItem[]>([]);
  const [syncing, setSyncing] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const ref = useRef<QueueItem[]>([]);
  const running = useRef(false);

  const save = useCallback(async (next: QueueItem[]) => {
    ref.current = next;
    setItems(next);
    await storage.setItem(KEY, JSON.stringify(next));
  }, []);

  const patch = (id: string, p: Partial<QueueItem>) => save(ref.current.map((i) => (i.id === id ? { ...i, ...p } : i)));

  const process = useCallback(async () => {
    if (running.current || !user) return;
    running.current = true;
    setSyncing(true);
    const mine = (i: QueueItem) => !i.userId || i.userId === user.id;
    let done = 0;
    try {
      for (;;) {
        const own = ref.current.filter(mine);
        const blockedNcs = new Set(own.filter((i) => i.type === "upload" && i.status !== "uploaded").map((i) => i.ncId));
        const next = own.find(
          (i) => i.status === "pending" && (i.type === "upload" || !blockedNcs.has(i.ncId)),
        );
        if (!next) break;
        setProgress({ done, total: done + own.filter((i) => i.type === "upload" && (i.status === "pending" || i.status === "uploading")).length });
        await patch(next.id, { status: "uploading", error: undefined });
        let r: Response;
        try {
          if (next.type === "upload") {
            r = await authed((t) =>
              uploadForm(`${BASE}/api/ncs/${next.ncId}/evidence`, {
                headers: t ? { Authorization: `Bearer ${t}` } : {},
                fields: { meta: JSON.stringify({ ...next.meta, client_id: next.id }) },
                files: { file: { uri: next.uri!, name: next.mime!.includes("video") ? "video.mp4" : "photo.jpg", mimeType: next.mime } },
              }),
            );
          } else {
            r = await authed((t) =>
              fetch(`${BASE}/api/ncs/${next.ncId}/submit`, { method: "POST", headers: t ? { Authorization: `Bearer ${t}` } : {} }),
            );
          }
        } catch {
          await patch(next.id, { status: "pending", error: "Waiting for connection" });
          break; // offline: stop and retry later
        }
        if (r.ok) {
          // server confirmed receipt (idempotent on client_id) - only now is the local copy released
          if (next.type === "upload") {
            done += 1;
            await patch(next.id, { status: "uploaded", error: undefined });
            setTimeout(() => {
              removeFile(next.uri);
              save(ref.current.filter((i) => i.id !== next.id));
            }, 4000);
          } else await save(ref.current.filter((i) => i.id !== next.id));
          queryClient.invalidateQueries({ queryKey: ["project", next.projectId] });
          queryClient.invalidateQueries({ queryKey: ["projects"] });
        } else if (r.status >= 500 || r.status === 429) {
          await patch(next.id, { status: "pending", error: "Server busy, retrying" });
          break;
        } else {
          await patch(next.id, { status: "failed", error: refusal(r.status, await readError(r)) });
        }
      }
    } finally {
      running.current = false;
      setSyncing(false);
      setProgress(null);
    }
  }, [user]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    (async () => {
      const raw = await storage.getItem(KEY, null);
      const list: QueueItem[] = raw ? JSON.parse(String(raw)) : [];
      // interrupted uploads go back to pending (server de-duplicates by client_id); confirmed ones are released
      list.filter((i) => i.status === "uploaded").forEach((i) => removeFile(i.uri));
      await save(list.filter((i) => i.status !== "uploaded").map((i) => (i.status === "uploading" ? { ...i, status: "pending" } : i)));
      process();
    })();
    const t = setInterval(() => process(), 15000);
    const sub = AppState.addEventListener("change", (s) => s === "active" && process());
    return () => {
      clearInterval(t);
      sub.remove();
    };
  }, [process, save]);

  const enqueueUpload: Ctx["enqueueUpload"] = async (projectId, ncId, uri, mime, meta) => {
    const id = uid();
    const item: QueueItem = { id, type: "upload", projectId, ncId, uri: persistFile(uri, id, mime), mime, meta, userId: user?.id, status: "pending", createdAt: new Date().toISOString() };
    await save([...ref.current, item]);
    process();
  };

  const enqueueSubmit: Ctx["enqueueSubmit"] = async (projectId, ncId) => {
    if (ref.current.some((i) => i.type === "submit" && i.ncId === ncId)) return;
    await save([...ref.current, { id: uid(), type: "submit", projectId, ncId, userId: user?.id, status: "pending", createdAt: new Date().toISOString() }]);
    process();
  };

  const discard = async (id: string) => {
    removeFile(ref.current.find((i) => i.id === id)?.uri);
    await save(ref.current.filter((i) => i.id !== id));
  };

  const clearAll = async () => {
    ref.current.forEach((i) => removeFile(i.uri));
    await save([]);
  };

  const retry = (id: string) => {
    patch(id, { status: "pending", error: undefined }).then(process);
  };

  const retryFailed = () => {
    save(ref.current.map((i) => (i.status === "failed" && (!i.userId || i.userId === user?.id) ? { ...i, status: "pending", error: undefined } : i))).then(process);
  };

  // other users' evidence on a shared phone stays stored but is never shown or uploaded under this login
  const visible = items.filter((i) => !i.userId || i.userId === user?.id);

  return (
    <QueueCtx.Provider value={{ items: visible, syncing, progress, retryFailed, enqueueUpload, enqueueSubmit, discard, clearAll, retry, syncNow: process }}>
      {children}
    </QueueCtx.Provider>
  );
}

export const useQueue = () => useContext(QueueCtx);

export function tzInfo() {
  return { tz: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", tz_offset_min: -new Date().getTimezoneOffset() };
}
