import { createContext, PropsWithChildren, useCallback, useContext, useEffect, useState } from "react";
import { storage } from "@/src/utils/storage";
import { queryClient } from "@/src/query-client";
import * as API from "@/src/api";

type Ctx = {
  user: API.User | null;
  ready: boolean;
  offline: boolean;
  signIn: (email: string, password: string) => Promise<API.User>;
  signOut: () => Promise<void>;
};

const AuthCtx = createContext<Ctx>(null as any);
const CACHED = "cached_user";

export function AuthProvider({ children }: PropsWithChildren) {
  const [user, setUser] = useState<API.User | null>(null);
  const [ready, setReady] = useState(false);
  const [offline, setOffline] = useState(false);

  const clear = useCallback(async () => {
    setUser(null);
    await storage.removeItem(CACHED);
    queryClient.clear();
  }, []);

  useEffect(() => {
    API.setAuthLostHandler(() => void clear());
    (async () => {
      try {
        const u = await API.refreshSession();
        if (u) {
          setUser(u);
          await storage.setItem(CACHED, JSON.stringify(u));
        } else await storage.removeItem(CACHED);
      } catch {
        // No connection: keep working on-site with the cached identity; uploads queue until online.
        const raw = await storage.getItem(CACHED, null);
        if (raw) {
          setUser(JSON.parse(String(raw)));
          setOffline(true);
        }
      } finally {
        setReady(true);
      }
    })();
  }, [clear]);

  const signIn = async (email: string, password: string) => {
    const u = await API.login(email.trim(), password);
    setUser(u);
    setOffline(false);
    await storage.setItem(CACHED, JSON.stringify(u));
    return u;
  };

  const signOut = async () => {
    await API.logout();
    await clear();
  };

  return <AuthCtx.Provider value={{ user, ready, offline, signIn, signOut }}>{children}</AuthCtx.Provider>;
}

export const useAuth = () => useContext(AuthCtx);
