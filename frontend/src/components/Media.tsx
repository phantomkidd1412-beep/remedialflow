import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Modal, Platform, Pressable, ScrollView, Text, View } from "react-native";
import { Image } from "expo-image";
import { useVideoPlayer, VideoView } from "expo-video";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { absUrl, api } from "@/src/api";
import { EvidenceFile } from "@/src/projects";
import { Badge, Feather, fmtDate } from "@/src/ui";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

export function Thumb({ f, size = 72, onPress, onLongPress, testID }: { f: EvidenceFile; size?: number; onPress?: () => void; onLongPress?: () => void; testID?: string }) {
  const s = useStyles();
  const { colors } = useTheme();
  const uri = f.urls.thumb ? absUrl(f.urls.thumb) : null;
  return (
    <Pressable onPress={onPress} onLongPress={onLongPress} style={[s.thumb, { width: size, height: size }]} testID={testID}>
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

type Play = { state: string; url: string | null; original_url: string; original_mime: string; codec?: string | null };

function Player({ uri, onError }: { uri: string; onError: () => void }) {
  const player = useVideoPlayer(uri, (p) => (p.loop = false));
  useEffect(() => {
    const sub = player.addListener("statusChange", (e) => e.status === "error" && onError());
    return () => sub.remove();
  }, [player, onError]);
  return <VideoView player={player} style={{ width: "100%", flex: 1 }} contentFit="contain" nativeControls fullscreenOptions={{ enable: true }} allowsPictureInPicture={false} />;
}

/** Plays through a freshly signed URL (never a stale cached one). Prefers the universal H.264 copy; iOS can play the
 *  original (HEVC/MOV) while that copy is still being prepared. Clear message instead of a broken player. */
function VideoPlayer({ f }: { f: EvidenceFile }) {
  const s = useStyles();
  const { colors } = useTheme();
  const [info, setInfo] = useState<Play | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [failedUri, setFailedUri] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    api<Play>(`/evidence/${f.id}/play`)
      .then((r) => alive && (setInfo(r), setErr(null)))
      .catch((e) => alive && setErr(e.message || "Could not load video"));
    return () => {
      alive = false;
    };
  }, [f.id, tick]);

  // still preparing the playback copy: poll until ready
  useEffect(() => {
    if (info?.state !== "pending") return;
    const t = setTimeout(() => setTick((n) => n + 1), 4000);
    return () => clearTimeout(t);
  }, [info]);

  const originalOk = Platform.OS === "ios" || /h264|avc/i.test(info?.codec ?? "");
  const uri = info?.url ? absUrl(info.url) : info && originalOk ? absUrl(info.original_url) : null;
  const onError = useCallback(() => setFailedUri(uri), [uri]);
  // web only: a few stripped-down browsers can't decode standard MP4 (H.264) at all
  const webNoMp4 = Platform.OS === "web" && typeof document !== "undefined" && !document.createElement("video").canPlayType('video/mp4; codecs="avc1.42E01E"');

  let msg: string | null = err;
  if (!msg && webNoMp4) msg = "This browser can't play MP4 video. Open RemedialFlow in Chrome, Safari, Edge or Firefox, or on your phone.";
  if (!msg && info && !uri) msg = info.state === "failed" ? "This video can't be played in the app. The original file is kept and included in the export." : "Preparing video for playback…";
  if (!msg && uri && failedUri === uri) msg = "Playback failed. Check your connection and try again. The original file is kept and included in the export.";

  if (!info && !err) return <View style={s.vidBox}><ActivityIndicator color={colors.onSurfaceInverse} /></View>;
  if (msg)
    return (
      <View style={s.vidBox} testID="video-message">
        {info?.state === "pending" && !err ? <ActivityIndicator color={colors.onSurfaceInverse} /> : <Feather name="alert-circle" size={22} color={colors.onSurfaceInverse} />}
        <Text style={s.vidMsg}>{msg}</Text>
        {info?.state !== "pending" ? (
          <Pressable onPress={() => (setFailedUri(null), setInfo(null), setErr(null), setTick((n) => n + 1))} style={s.retry} testID="video-retry">
            <Text style={s.vidMsg}>TRY AGAIN</Text>
          </Pressable>
        ) : null}
      </View>
    );
  return <Player key={uri!} uri={uri!} onError={onError} />;
}

const SOURCE: Record<string, string> = { in_app_camera: "Captured in app", gallery: "Uploaded from phone gallery", employee_upload: "Uploaded by employee", pdf_import: "Imported from inspection PDF", system: "System / TrustMark import" };

export function MediaViewer({ f, onClose }: { f: EvidenceFile | null; onClose: () => void }) {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const [variant, setVariant] = useState<"stamped" | "original">("stamped");
  if (!f) return null;
  if (f.kind === "video")
    // simple player only: playback area, native play/pause/seek/fullscreen, Close. Metadata stays server-side.
    return (
      <Modal visible animationType="fade" onRequestClose={onClose}>
        <View style={[s.root, { paddingTop: insets.top, paddingBottom: insets.bottom }]} testID="media-viewer">
          <View style={s.top}>
            <Pressable onPress={onClose} style={s.close} testID="media-viewer-close" hitSlop={8} accessibilityLabel="Close video">
              <Feather name="x" size={22} color={colors.onSurfaceInverse} />
            </Pressable>
          </View>
          <VideoPlayer f={f} />
        </View>
      </Modal>
    );
  const img = absUrl(f.urls[variant] ? f.urls[variant] : f.urls.display || f.urls.original);
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
          <Text style={s.title}>PHOTO EVIDENCE</Text>
          <Pressable onPress={onClose} style={s.close} testID="media-viewer-close" hitSlop={8}>
            <Feather name="x" size={22} color={colors.onSurfaceInverse} />
          </Pressable>
        </View>
        <ScrollView contentContainerStyle={{ paddingBottom: insets.bottom + space.xl }}>
          <Image source={{ uri: img!, cacheKey: `${f.id}-${variant}` }} style={s.img} contentFit="contain" transition={120} />
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
  thumb: { borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center", overflow: "hidden" },
  play: { position: "absolute", bottom: 0, left: 0, backgroundColor: c.surfaceInverse, padding: 3 },
  flag: { position: "absolute", top: 0, right: 0, backgroundColor: c.warning, padding: 3 },
  root: { flex: 1, backgroundColor: c.surfaceInverse },
  top: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: space.lg, height: 56 },
  title: { fontFamily: fonts.text, color: c.onSurfaceInverse, fontSize: 13 },
  close: { width: 44, height: 44, alignItems: "center", justifyContent: "center", borderWidth: BORDER, borderRadius: radius.md, borderColor: c.onSurfaceInverse },
  img: { width: "100%", aspectRatio: 3 / 4, backgroundColor: c.surfaceInverse },
  toggle: { flexDirection: "row", margin: space.lg, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.onSurfaceInverse },
  tBtn: { flex: 1, height: 40, alignItems: "center", justifyContent: "center" },
  tText: { fontFamily: fonts.text, fontSize: 12, color: c.onSurfaceInverse },
  meta: { backgroundColor: c.surface, margin: space.lg, padding: space.lg, gap: space.sm, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border },
  row: { flexDirection: "row", gap: space.md },
  k: { width: 110, fontFamily: fonts.text, fontSize: 11, color: c.muted, textTransform: "uppercase" },
  v: { flex: 1, fontFamily: fonts.text, fontSize: 12, color: c.onSurface },
  note: { fontFamily: fonts.text, fontSize: 11, color: c.muted, marginTop: space.sm },
  vidBox: { flex: 1, alignItems: "center", justifyContent: "center", gap: space.md, padding: space.xl },
  vidMsg: { fontFamily: fonts.text, fontSize: 13, color: c.onSurfaceInverse, textAlign: "center" },
  retry: { borderWidth: BORDER, borderRadius: radius.md, borderColor: c.onSurfaceInverse, paddingHorizontal: space.lg, height: 44, justifyContent: "center" },
}));
