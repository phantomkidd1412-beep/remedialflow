import { Linking, Pressable, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Btn, Feather } from "@/src/ui";
import { useGeo, stampTime } from "@/src/components/useGeo";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

export type P = { projectId: string; ncId: string; ref: string; postcode: string; nc: string; stage?: string };

export function PermissionGate({ kind, perm, request }: { kind: "camera" | "microphone"; perm: { granted: boolean; canAskAgain: boolean } | null; request: () => void }) {
  const s = useCameraStyles();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const blocked = perm && !perm.granted && !perm.canAskAgain;
  return (
    <View style={[s.gate, { paddingTop: insets.top + space.xl, paddingBottom: insets.bottom + space.xl }]} testID={`${kind}-permission-gate`}>
      <Feather name={kind === "camera" ? "camera" : "mic"} size={48} color="#FFFFFF" />
      <Text style={s.gateTitle}>{kind === "camera" ? "CAMERA FOR EVIDENCE" : "MICROPHONE FOR VIDEO"}</Text>
      <Text style={s.gateText}>
        {kind === "camera"
          ? "Photos are captured inside the app so each one is automatically stamped with date, time, GPS and the project/NC reference. Location is also requested to geotag your evidence."
          : "Video evidence records audio so reviewers can hear your explanation."}
      </Text>
      {blocked ? (
        <Btn title="Open settings" icon="settings" onPress={() => Linking.openSettings()} testID={`${kind}-open-settings`} />
      ) : (
        <Btn title="Continue" icon="check" onPress={request} testID={`${kind}-allow-button`} />
      )}
      <Btn title="Not now" variant="outline" onPress={() => router.back()} testID={`${kind}-cancel-button`} />
    </View>
  );
}

export function StampBlock({ geo, p, now }: { geo: ReturnType<typeof useGeo>; p: P; now: Date }) {
  const s = useCameraStyles();
  const { colors } = useTheme();
  const verified = geo.pos && geo.pos.coords.accuracy != null && geo.pos.coords.accuracy <= 50;
  return (
    <View style={s.stamp} testID="camera-stamp">
      <Text style={s.stampText}>{stampTime(now)}</Text>
      <Text style={s.stampText}>
        {geo.pos ? `GPS: ${geo.pos.coords.latitude.toFixed(4)}, ${geo.pos.coords.longitude.toFixed(4)} (±${Math.round(geo.pos.coords.accuracy ?? 0)}m)` : geo.status === "denied" ? "GPS: PERMISSION DENIED" : "GPS: ACQUIRING…"}
      </Text>
      <Text style={s.stampText}>Project: {p.ref} · {p.postcode} · {p.nc}</Text>
      <View style={s.locRow}>
        <View style={[s.locTag, { backgroundColor: verified ? colors.success : colors.warning }]}>
          <Text style={s.locText} testID="camera-location-status">{verified ? "LOCATION OK" : "LOCATION NOT VERIFIED"}</Text>
        </View>
        {geo.status === "denied" ? (
          <Pressable onPress={geo.fix} style={s.fixBtn} testID="enable-location-button" hitSlop={6}>
            <Text style={s.fixText}>{geo.canAskAgain ? "ENABLE GPS" : "OPEN SETTINGS"}</Text>
          </Pressable>
        ) : null}
      </View>
    </View>
  );
}

// Shared camera UI is always black regardless of theme; literals are intentional.
export const useCameraStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: "#000000" },
  cam: { flex: 1 },
  focus: { position: "absolute", width: 72, height: 72, borderWidth: 2, borderColor: c.brandPrimary },
  sideBar: { position: "absolute", right: space.lg, top: 140, gap: space.sm },
  sideText: { fontFamily: fonts.text, fontSize: 10, color: "#FFFFFF" },
  topBar: { position: "absolute", left: space.lg, right: space.lg, flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  iconBtn: { width: 48, height: 48, backgroundColor: "#0A0A0A", borderWidth: BORDER, borderRadius: radius.md, borderColor: "#FFFFFF", alignItems: "center", justifyContent: "center" },
  countTag: { backgroundColor: c.brandPrimary, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, paddingHorizontal: space.sm, paddingVertical: 4 },
  countText: { fontFamily: fonts.text, fontSize: 12, color: c.onBrandPrimary },
  bottom: { backgroundColor: "#0A0A0A", paddingHorizontal: space.lg, paddingTop: space.md, gap: space.md },
  stamp: { gap: 2 },
  stampText: { fontFamily: fonts.text, fontSize: 13, color: "#FFFFFF" },
  locRow: { flexDirection: "row", gap: space.sm, marginTop: 4, alignItems: "center" },
  locTag: { paddingHorizontal: 6, paddingVertical: 2 },
  locText: { fontFamily: fonts.text, fontSize: 11, color: "#0A0A0A" },
  fixBtn: { borderWidth: 1, borderColor: "#FFFFFF", paddingHorizontal: 8, paddingVertical: 2 },
  fixText: { fontFamily: fonts.text, fontSize: 11, color: "#FFFFFF" },
  controls: { flexDirection: "row", alignItems: "center", gap: space.md, height: 88 },
  shutter: { width: 80, height: 80, borderRadius: 40, backgroundColor: "#FFFFFF", borderWidth: 4, borderColor: c.brandPrimary },
  mini: { width: 40, height: 40, borderWidth: 1, borderColor: "#FFFFFF" },
  doneBtn: { height: 48, paddingHorizontal: space.lg, backgroundColor: c.brandPrimary, borderWidth: BORDER, borderRadius: radius.md, borderColor: "#FFFFFF", justifyContent: "center" },
  doneText: { fontFamily: fonts.display, fontWeight: "800", fontSize: 14, color: c.onBrandPrimary },
  gate: { flex: 1, backgroundColor: "#0A0A0A", padding: space.xl, justifyContent: "center", gap: space.lg },
  gateTitle: { fontFamily: fonts.display, fontWeight: "800", fontSize: 22, color: "#FFFFFF" },
  gateText: { fontFamily: fonts.text, fontSize: 13, color: "#FFFFFF" },
}));
