import { useState } from "react";
import { Modal, Pressable, ScrollView, Text, View } from "react-native";
import { Image } from "expo-image";
import { useVideoPlayer, VideoView } from "expo-video";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { absUrl } from "@/src/api";
import { EvidenceFile } from "@/src/projects";
import { Badge, Feather, fmtDate } from "@/src/ui";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

export function Thumb({ f, size = 72, onPress, testID }: { f: EvidenceFile; size?: number; onPress?: () => void; testID?: string }) {
  const s = useStyles();
  const { colors } = useTheme();
  const uri = f.urls.thumb ? absUrl(f.urls.thumb) : null;
  return (
    <Pressable onPress={onPress} style={[s.thumb, { width: size, height: size }]} testID={testID}>
      {uri ? (
        <Image source={{ uri, cacheKey: `${f.id}-thumb` }} style={{ width: "100%", height: "100%" }} contentFit="cover" transition={120} recyclingKey={f.id} />
      ) : (
        <Feather name={f.kind === "video" ? "film" : "image"} size={22} color={colors.muted} />
      )}
      {f.kind === "video" ? <View style={s.play}><Feather name="play" size={14} color={colors.onSurfaceInverse} /></View> : null}
      {f.kind === "photo" && !f.location_verified ? <View style={s.flag}><Feather name="map-pin" size={10} color={colors.onWarning} /></View> : null}
    </Pressable>
  );
}

function VideoPlayer({ uri }: { uri: string }) {
  const player = useVideoPlayer(uri, (p) => (p.loop = false));
  return <VideoView player={player} style={{ width: "100%", aspectRatio: 9 / 12 }} contentFit="contain" nativeControls />;
}

const SOURCE: Record<string, string> = { in_app_camera: "Captured in app", gallery: "Uploaded from phone gallery", employee_upload: "Uploaded by employee", pdf_import: "Imported from inspection PDF", system: "System / TrustMark import" };

export function MediaViewer({ f, onClose }: { f: EvidenceFile | null; onClose: () => void }) {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const [variant, setVariant] = useState<"stamped" | "original">("stamped");
  if (!f) return null;
  const img = f.kind === "video" ? null : absUrl(f.urls[variant] ? f.urls[variant] : f.urls.display || f.urls.original);
  const rows: [string, string][] = [
    ["Source", SOURCE[f.source] ?? f.source],
    ["Device capture", f.captured_at ? `${fmtDate(f.captured_at)} ${f.tz ?? ""}` : "Not reported"],
    ["Server received", fmtDate(f.server_received_at)],
    ["GPS", f.lat != null && f.lng != null ? `${f.lat.toFixed(5)}, ${f.lng.toFixed(5)}` : "Not available"],
    ["Accuracy", f.accuracy_m != null ? `±${Math.round(f.accuracy_m)} m` : "—"],
    ["Location", f.location_verified ? "GPS reading present (device-reported)" : "LOCATION NOT VERIFIED"],
    ...(f.duration_s ? ([["Duration", `${Math.round(f.duration_s)} s`]] as [string, string][]) : []),
    ["SHA-256", f.sha256],
    ["Size", `${(f.size / 1024 / 1024).toFixed(2)} MB`],
  ];
  return (
    <Modal visible animationType="fade" onRequestClose={onClose}>
      <View style={[s.root, { paddingTop: insets.top }]} testID="media-viewer">
        <View style={s.top}>
          <Text style={s.title}>{f.kind === "video" ? "VIDEO EVIDENCE" : "PHOTO EVIDENCE"}</Text>
          <Pressable onPress={onClose} style={s.close} testID="media-viewer-close" hitSlop={8}>
            <Feather name="x" size={22} color={colors.onSurfaceInverse} />
          </Pressable>
        </View>
        <ScrollView contentContainerStyle={{ paddingBottom: insets.bottom + space.xl }}>
          {f.kind === "video" ? (
            <VideoPlayer uri={absUrl(f.urls.original)} />
          ) : (
            <Image source={{ uri: img!, cacheKey: `${f.id}-${variant}` }} style={s.img} contentFit="contain" transition={120} />
          )}
          {f.kind === "photo" && f.urls.stamped ? (
            <View style={s.toggle}>
              {(["stamped", "original"] as const).map((v) => (
                <Pressable key={v} onPress={() => setVariant(v)} style={[s.tBtn, variant === v && { backgroundColor: colors.brandPrimary }]} testID={`viewer-variant-${v}`}>
                  <Text style={[s.tText, variant === v && { color: colors.onBrandPrimary }]}>{v === "stamped" ? "STAMPED COPY" : "ORIGINAL"}</Text>
                </Pressable>
              ))}
            </View>
          ) : null}
          <View style={s.meta}>
            {!f.location_verified && f.kind !== "trustmark" ? <Badge status="more_evidence" label="Location not verified" /> : null}
            {rows.map(([k, v]) => (
              <View key={k} style={s.row}>
                <Text style={s.k}>{k}</Text>
                <Text style={s.v} selectable>{v}</Text>
              </View>
            ))}
            <Text style={s.note}>Device GPS and time are evidence metadata reported by the phone, not independently verified.</Text>
          </View>
        </ScrollView>
      </View>
    </Modal>
  );
}

const useStyles = makeStyles((c) => ({
  thumb: { borderWidth: BORDER, borderColor: c.border, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center", overflow: "hidden" },
  play: { position: "absolute", bottom: 0, left: 0, backgroundColor: c.surfaceInverse, padding: 3 },
  flag: { position: "absolute", top: 0, right: 0, backgroundColor: c.warning, padding: 3 },
  root: { flex: 1, backgroundColor: c.surfaceInverse },
  top: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: space.lg, height: 56 },
  title: { fontFamily: fonts.mono, color: c.onSurfaceInverse, fontSize: 13 },
  close: { width: 44, height: 44, alignItems: "center", justifyContent: "center", borderWidth: BORDER, borderColor: c.onSurfaceInverse },
  img: { width: "100%", aspectRatio: 3 / 4, backgroundColor: c.surfaceInverse },
  toggle: { flexDirection: "row", margin: space.lg, borderWidth: BORDER, borderColor: c.onSurfaceInverse },
  tBtn: { flex: 1, height: 40, alignItems: "center", justifyContent: "center" },
  tText: { fontFamily: fonts.mono, fontSize: 12, color: c.onSurfaceInverse },
  meta: { backgroundColor: c.surface, margin: space.lg, padding: space.lg, gap: space.sm, borderWidth: BORDER, borderColor: c.border },
  row: { flexDirection: "row", gap: space.md },
  k: { width: 110, fontFamily: fonts.mono, fontSize: 11, color: c.muted, textTransform: "uppercase" },
  v: { flex: 1, fontFamily: fonts.mono, fontSize: 12, color: c.onSurface },
  note: { fontFamily: fonts.mono, fontSize: 11, color: c.muted, marginTop: space.sm },
}));
