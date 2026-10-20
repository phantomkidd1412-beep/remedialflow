// Offline-first evidence queue. Items persist on-device and sync sequentially; an item is only
// removed after the server confirms receipt.
import { createContext, PropsWithChildren, useCallback, useContext, useEffect, useRef, useState } from "react";
import { AppState, Platform } from "react-native";
import NetInfo from "@react-native-community/netinfo";
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
  expect?: string[]; // submit only: client ids of uploads that must reach the server before AI assessment starts
};

/** Per-NC evidence state shown to the installer. null = nothing on the phone, show the server status. */
export type EvidenceState = { key: "failed" | "submitting" | "uploading" | "submitted" | "pending"; label: string; error?: string; failed: QueueItem[] };

export function evidenceState(queued: QueueItem[]): EvidenceState | null {
  const sub = queued.find((q) => q.type === "submit");
  const ups = queued.filter((q) => q.type === "upload" && q.status !== "uploaded");
  const failed = [...(sub?.status === "failed" ? [sub] : []), ...ups.filter((q) => q.status === "failed")];
  if (failed.length) return { key: "failed", label: "Upload Failed", error: failed[0].error, failed };
  if (sub?.status === "uploading") return { key: "submitting", label: "Submitting", failed };
  if (ups.some((q) => q.status === "uploading")) return { key: "uploading", label: "Uploading", failed };
  if (sub?.status === "uploaded") return { key: "submitted", label: "Submitted", failed };
  if (sub || ups.length) return { key: "pending", label: "Pending Upload", failed };
  return null;
}

const SUBMIT_TIMEOUT = 45_000;
const UPLOAD_TIMEOUT = 10 * 60_000;
const VIDEO_UPLOAD_TIMEOUT = 90 * 60_000; // long videos on slow site connections

/** On-device byte size, sent with the upload so the server can reject a truncated transfer (native only). */
function sizeOf(uri: string): number | undefined {
  if (Platform.OS === "web") return undefined;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { File } = require("expo-file-system");
    const n = new File(uri).size;
    return typeof n === "number" && n > 0 ? n : undefined;
  } catch {
    return undefined;
  }
}
/** A hung request must never leave an item stuck on Uploading/Submitting: treat it like a dropped connection. */
function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise((res, rej) => {
    const t = setTimeout(() => rej(new Error("timeout")), ms);
    p.then((v) => { clearTimeout(t); res(v); }, (e) => { clearTimeout(t); rej(e); });
  });
}

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
  pct: Record<string, number>; // live upload fraction (0..1) per queue item id; in-memory only
  retryFailed: () => void;
  enqueueUpload: (projectId: string, ncId: string, uri: string, mime: string, meta: EvidenceMeta) => Promise<void>;
  enqueueSubmit: (projectId: string, ncId: string) => Promise<void>;
  discard: (id: string) => Promise<void>;
  clearAll: () => Promise<void>;
  retry: (id: string) => void;
  retryNc: (ncId: string) => void;
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
    const dest = new File(dir, `${id}.${mime.includes("quicktime") ? "mov" : mime.includes("video") ? "mp4" : "jpg"}`);
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
  const [pct, setPct] = useState<Record<string, number>>({});
  const lastPct = useRef(0);
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
            r = await withTimeout(authed((t) =>
              uploadForm(`${BASE}/api/ncs/${next.ncId}/evidence`, {
                headers: t ? { Authorization: `Bearer ${t}` } : {},
                fields: { meta: JSON.stringify({ ...next.meta, client_id: next.id, size_bytes: sizeOf(next.uri!) }) },
                files: { file: { uri: next.uri!, name: next.mime!.includes("video") ? (next.mime!.includes("quicktime") ? "video.mov" : "video.mp4") : "photo.jpg", mimeType: next.mime } },
                onProgress: (f) => {
                  // throttle re-renders: only on >=2% change
                  if (Math.abs(f - lastPct.current) < 0.02 && f < 1) return;
                  lastPct.current = f;
                  setPct((m) => ({ ...m, [next.id]: f }));
                },
              }),
            ), next.mime?.includes("video") ? VIDEO_UPLOAD_TIMEOUT : UPLOAD_TIMEOUT).finally(() => {
              lastPct.current = 0;
              setPct((m) => {
                const { [next.id]: _, ...rest } = m;
                return rest;
              });
            });
          } else {
            // idempotent on client_id; server refuses (409) until every expected upload has arrived
            r = await withTimeout(authed((t) =>
              fetch(`${BASE}/api/ncs/${next.ncId}/submit`, {
                method: "POST",
                headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) },
                body: JSON.stringify({ client_id: next.id, expect: next.expect ?? [] }),
              }),
            ), SUBMIT_TIMEOUT);
          }
        } catch (e: any) {
          if (e?.status === 401) {
            await patch(next.id, { status: "pending", error: "Sign in again to send" });
            break;
          }
          await patch(next.id, { status: "pending", error: "Waiting for connection" });
          break; // offline: stop and retry later
        }
        if (r.ok) {
          // server confirmed receipt (idempotent on client_id) - only now is the local copy released
          if (next.type === "upload") done += 1;
          await patch(next.id, { status: "uploaded", error: undefined });
          setTimeout(() => {
            removeFile(next.uri);
            save(ref.current.filter((i) => i.id !== next.id));
          }, 4000);
          queryClient.invalidateQueries({ queryKey: ["project", next.projectId] });
          queryClient.invalidateQueries({ queryKey: ["projects"] });
        } else if (r.status >= 500 || r.status === 429) {
          await patch(next.id, { status: "pending", error: "Server busy, retrying" });
          break;
        } else {
          const msg = await readError(r);
          if (r.status === 422 && /incomplete/i.test(msg)) {
            // transfer was cut off: file is still on the phone, send it again automatically
            await patch(next.id, { status: "pending", error: "Upload interrupted, retrying" });
            break;
          }
          await patch(next.id, { status: "failed", error: refusal(r.status, msg) });
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
    // connectivity returned: send queued evidence straight away
    const net = NetInfo.addEventListener((s) => {
      if (s.isConnected && s.isInternetReachable !== false) process();
    });
    return () => {
      clearInterval(t);
      sub.remove();
      net();
    };
  }, [process, save]);

  const enqueueUpload: Ctx["enqueueUpload"] = async (projectId, ncId, uri, mime, meta) => {
    const id = uid();
    const item: QueueItem = { id, type: "upload", projectId, ncId, uri: persistFile(uri, id, mime), mime, meta, userId: user?.id, status: "pending", createdAt: new Date().toISOString() };
    await save([...ref.current, item]);
    process();
  };

  const enqueueSubmit: Ctx["enqueueSubmit"] = async (projectId, ncId) => {
    const expect = ref.current.filter((i) => i.type === "upload" && i.ncId === ncId && (!i.userId || i.userId === user?.id)).map((i) => i.id);
    const existing = ref.current.find((i) => i.type === "submit" && i.ncId === ncId);
    if (existing && existing.status !== "failed") return;
    if (existing) {
      // re-submitting after a failure: same idempotency key, refreshed list of expected uploads
      await save(ref.current.map((i) => (i.id === existing.id ? { ...i, status: "pending", error: undefined, expect: [...new Set([...(i.expect ?? []), ...expect])] } : i)));
    } else {
      await save([...ref.current, { id: uid(), type: "submit", projectId, ncId, userId: user?.id, status: "pending", expect, createdAt: new Date().toISOString() }]);
    }
    process();
  };

  const discard = async (id: string) => {
    removeFile(ref.current.find((i) => i.id === id)?.uri);
    // a deleted upload is no longer expected by any pending submit
    await save(ref.current.filter((i) => i.id !== id).map((i) => (i.expect?.includes(id) ? { ...i, expect: i.expect.filter((x) => x !== id) } : i)));
  };

  const clearAll = async () => {
    ref.current.forEach((i) => removeFile(i.uri));
    await save([]);
  };

  const retry = (id: string) => {
    patch(id, { status: "pending", error: undefined }).then(process);
  };

  const retryNc = (ncId: string) => {
    save(ref.current.map((i) => (i.ncId === ncId && i.status === "failed" && (!i.userId || i.userId === user?.id) ? { ...i, status: "pending", error: undefined } : i))).then(process);
  };

  const retryFailed = () => {
    save(ref.current.map((i) => (i.status === "failed" && (!i.userId || i.userId === user?.id) ? { ...i, status: "pending", error: undefined } : i))).then(process);
  };

  // other users' evidence on a shared phone stays stored but is never shown or uploaded under this login
  const visible = items.filter((i) => !i.userId || i.userId === user?.id);

  return (
    <QueueCtx.Provider value={{ items: visible, syncing, progress, pct, retryFailed, enqueueUpload, enqueueSubmit, discard, clearAll, retry, retryNc, syncNow: process }}>
      {children}
    </QueueCtx.Provider>
  );
}

export const useQueue = () => useContext(QueueCtx);

export function tzInfo() {
  return { tz: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", tz_offset_min: -new Date().getTimezoneOffset() };
}
