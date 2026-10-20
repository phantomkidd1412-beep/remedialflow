// Per-user offline cache of project data the user has already loaded (app-private storage).
// Never serves data the user has not opened online, and drops entries the server now refuses (403/404).
import { storage } from "@/src/utils/storage";

let uid: string | null = null;
export const setCacheUser = (id: string | null) => {
  uid = id;
};
const k = (u: string, key: string) => `oc:${u}:${key}`;

async function index(u: string): Promise<string[]> {
  const raw = await storage.getItem(k(u, "__index"), null);
  return raw ? JSON.parse(String(raw)) : [];
}

export async function cached<T>(key: string, fetcher: () => Promise<T>): Promise<T> {
  const u = uid;
  try {
    const d = await fetcher();
    if (u) {
      await storage.setItem(k(u, key), JSON.stringify({ at: Date.now(), d }));
      const idx = await index(u);
      if (!idx.includes(key)) await storage.setItem(k(u, "__index"), JSON.stringify([...idx, key]));
    }
    return d;
  } catch (e: any) {
    if (!u) throw e;
    if (e?.status) {
      // the server answered: no longer assigned / deleted → forget the local copy
      if (e.status === 403 || e.status === 404) await storage.removeItem(k(u, key));
      throw e;
    }
    const raw = await storage.getItem(k(u, key), null);
    if (!raw) throw Object.assign(new Error("Not available offline. Open this job once while connected."), { offline: true });
    return JSON.parse(String(raw)).d as T;
  }
}

export async function clearCache(u: string) {
  for (const key of await index(u)) await storage.removeItem(k(u, key));
  await storage.removeItem(k(u, "__index"));
}
