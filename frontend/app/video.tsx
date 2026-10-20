import { useEffect, useRef, useState } from "react";
import { Pressable, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { CameraView, useCameraPermissions, useMicrophonePermissions } from "expo-camera";
import { useVideoPlayer, VideoView } from "expo-video";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { tzInfo, useQueue } from "@/src/queue";
import { Btn, Feather, useToast } from "@/src/ui";
import { freshFix, useGeo } from "@/src/components/useGeo";
import { PermissionGate, StampBlock } from "@/src/components/CameraParts";
import { useMediaConfig } from "@/src/projects";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

type P = { projectId: string; ncId: string; ref: string; postcode: string; nc: string };
type Clip = { uri: string; captured: Date; duration: number; lat: number | null; lng: number | null; acc: number | null };
const fmtElapsed = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

function Preview({ clip, onRetake, onUse }: { clip: Clip; onRetake: () => void; onUse: () => void }) {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const player = useVideoPlayer(clip.uri, (p) => p.play());
  return (
    <View style={[s.root, { paddingTop: insets.top }]} testID="video-preview">
      <VideoView player={player} style={{ flex: 1 }} contentFit="contain" nativeControls />
      <View style={[s.bottom, { paddingBottom: insets.bottom + space.md }]}>
        <Text style={s.stampText}>PREVIEW · {fmtElapsed(Math.round(clip.duration))}</Text>
        <View style={{ flexDirection: "row", gap: space.sm }}>
          <Btn title="Retake" icon="rotate-ccw" variant="outline" onPress={onRetake} style={{ flex: 1 }} testID="video-retake-button" />
          <Btn title="Use video" icon="check" onPress={onUse} style={{ flex: 1 }} testID="video-use-button" />
        </View>
      </View>
    </View>
  );
}

export default function VideoScreen() {
  const s = useStyles();
  const { colors } = useTheme();
  const p = useLocalSearchParams<P>();
  const router = useRouter();
  const toast = useToast();
  const insets = useSafeAreaInsets();
  const [cperm, reqC] = useCameraPermissions();
  const [mperm, reqM] = useMicrophonePermissions();
  const geo = useGeo(!!cperm?.granted);
  const { enqueueUpload } = useQueue();
  const { maxVideoMb } = useMediaConfig();
  const cam = useRef<CameraView>(null);
  const [recording, setRecording] = useState(false);
  const [ready, setReady] = useState(false);
  const [now, setNow] = useState(new Date());
  const [clip, setClip] = useState<Clip | null>(null);
  const started = useRef<Date | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  if (!cperm || !mperm) return <View style={s.root} />;
  if (!cperm.granted) return <PermissionGate kind="camera" perm={cperm} request={reqC} />;
  if (!mperm.granted) return <PermissionGate kind="microphone" perm={mperm} request={reqM} />;

  if (clip)
    return (
      <Preview
        clip={clip}
        onRetake={() => setClip(null)}
        onUse={async () => {
          await enqueueUpload(p.projectId, p.ncId, clip.uri, clip.uri.toLowerCase().endsWith(".mov") ? "video/quicktime" : "video/mp4", {
            kind: "video", source: "in_app_camera", captured_at: clip.captured.toISOString(), ...tzInfo(),
            lat: clip.lat, lng: clip.lng, accuracy_m: clip.acc, duration_s: clip.duration,
          });
          toast("Video queued · added to this NC");
          router.back();
        }}
      />
    );

  const toggle = async () => {
    if (!cam.current) return;
    if (recording) {
      cam.current.stopRecording();
      return;
    }
    setRecording(true);
    started.current = new Date();
    setStartedAt(started.current.getTime());
    const pos = freshFix(geo.pos);
    try {
      // no duration limit; size capped by the server-configured maximum (recording stops cleanly at the cap)
      const r = await cam.current.recordAsync({ maxFileSize: maxVideoMb * 1024 * 1024 });
      if (r?.uri) {
        const duration = (Date.now() - started.current!.getTime()) / 1000;
        setClip({ uri: r.uri, captured: started.current, duration, lat: pos?.coords.latitude ?? null, lng: pos?.coords.longitude ?? null, acc: pos?.coords.accuracy ?? null });
      }
    } catch (e: any) {
      toast(e?.message ?? "Recording failed");
    } finally {
      setRecording(false);
    }
  };

  const elapsed = recording && startedAt ? Math.max(0, Math.floor((now.getTime() - startedAt) / 1000)) : 0;

  return (
    <View style={s.root} testID="video-screen">
      <CameraView ref={cam} style={{ flex: 1 }} mode="video" facing="back" onCameraReady={() => setReady(true)} />
      <View style={[s.topBar, { top: insets.top + space.sm }]}>
        <Pressable onPress={() => router.back()} style={s.iconBtn} testID="video-close-button" hitSlop={6}>
          <Feather name="x" size={24} color="#FFFFFF" />
        </Pressable>
        {recording ? (
          <View style={[s.rec, { backgroundColor: colors.error }]}><Text style={s.recText} testID="video-timer">● REC {fmtElapsed(elapsed)}</Text></View>
        ) : (
          <View style={s.rec}><Text style={s.stampText} testID="video-limit">MAX {maxVideoMb} MB</Text></View>
        )}
      </View>
      <View style={[s.bottom, { paddingBottom: insets.bottom + space.md }]}>
        <StampBlock geo={geo} p={p} now={now} />
        <View style={s.controls}>
          <Pressable onPress={toggle} disabled={!ready} style={[s.shutter, recording && s.shutterRec, !ready && { opacity: 0.4 }]} testID="video-record-button">
            {recording ? <View style={s.stopSquare} /> : null}
          </Pressable>
        </View>
      </View>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: "#000000" },
  topBar: { position: "absolute", left: space.lg, right: space.lg, flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  iconBtn: { width: 48, height: 48, backgroundColor: "#0A0A0A", borderWidth: BORDER, borderColor: "#FFFFFF", alignItems: "center", justifyContent: "center" },
  rec: { paddingHorizontal: space.sm, paddingVertical: 4, borderWidth: BORDER, borderColor: c.border },
  recText: { fontFamily: fonts.mono, fontSize: 12, color: c.onError },
  bottom: { backgroundColor: "#0A0A0A", paddingHorizontal: space.lg, paddingTop: space.md, gap: space.md },
  stampText: { fontFamily: fonts.mono, fontSize: 13, color: "#FFFFFF" },
  controls: { alignItems: "center", height: 88, justifyContent: "center" },
  shutter: { width: 80, height: 80, borderRadius: 40, backgroundColor: c.error, borderWidth: 4, borderColor: "#FFFFFF", alignItems: "center", justifyContent: "center" },
  shutterRec: { backgroundColor: "#FFFFFF", borderColor: c.error },
  stopSquare: { width: 28, height: 28, backgroundColor: c.error },
}));
