import { useEffect, useRef, useState } from "react";
import { Linking } from "react-native";
import * as Location from "expo-location";

const p2 = (n: number) => String(n).padStart(2, "0");
export const stampTime = (d: Date) =>
  `${p2(d.getDate())}/${p2(d.getMonth() + 1)}/${d.getFullYear()} | ${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`;

/** High-accuracy live location. Never assumes GPS is available. */
export function useGeo(enabled: boolean) {
  const [pos, setPos] = useState<Location.LocationObject | null>(null);
  const [status, setStatus] = useState<"idle" | "granted" | "denied">("idle");
  const [canAskAgain, setCanAskAgain] = useState(true);
  const sub = useRef<Location.LocationSubscription | null>(null);

  const start = async (request: boolean) => {
    let perm = await Location.getForegroundPermissionsAsync();
    if (!perm.granted && request && perm.canAskAgain) perm = await Location.requestForegroundPermissionsAsync();
    setCanAskAgain(perm.canAskAgain);
    if (!perm.granted) {
      setStatus("denied");
      return;
    }
    setStatus("granted");
    try {
      sub.current?.remove();
      sub.current = await Location.watchPositionAsync(
        { accuracy: Location.Accuracy.BestForNavigation, timeInterval: 2000, distanceInterval: 0 },
        (p) => setPos(p),
      );
    } catch {
      setPos(null);
    }
  };

  useEffect(() => {
    if (!enabled) return;
    start(true);
    return () => sub.current?.remove();
  }, [enabled]);

  const fix = () => (canAskAgain ? start(true) : Linking.openSettings());
  return { pos, status, canAskAgain, fix };
}

/** Drop stale fixes (>30s) so evidence is never tagged with an old location. Call at capture time. */
export function freshFix(pos: Location.LocationObject | null) {
  return pos && Date.now() - pos.timestamp < 30000 ? pos : null;
}
