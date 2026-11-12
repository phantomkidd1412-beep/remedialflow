import { useEffect, useRef, useState } from "react";
import { Linking, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import { useLocalSearchParams, useRouter } from "expo-router";
import { CameraView, useCameraPermissions } from "expo-camera";
import * as Haptics from "expo-haptics";
import { Image } from "expo-image";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { EvidenceMeta, tzInfo, useQueue } from "@/src/queue";
import { Feather } from "@/src/ui";
import { freshFix, useGeo } from "@/src/components/useGeo";
import { P, PermissionGate, StampBlock, useCameraStyles } from "@/src/components/CameraParts";
import { space } from "@/src/theme";
import { BackupPerm, backupPermission, backupPhoto, requestBackupPermission } from "@/src/utils/galleryBackup";

// camera overlay is always dark (over the live preview), so fixed light-on-dark colours like the rest of this screen
const bk = StyleSheet.create({
  bar: { flexDirection: "row", alignItems: "center", gap: space.sm, backgroundColor: "rgba(0,0,0,0.7)", borderRadius: 12, padding: space.sm, marginBottom: space.sm },
  text: { flex: 1, color: "#FFFFFF", fontSize: 13 },
  btn: { minHeight: 36, paddingHorizontal: 12, borderRadius: 8, backgroundColor: "#FFFFFF", justifyContent: "center" },
  btnText: { color: "#000000", fontWeight: "700", fontSize: 13 },
});


export default function Camera() {
  const s = useCameraStyles();
  const p = useLocalSearchParams<P>();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [perm, requestPerm] = useCameraPermissions();
  const geo = useGeo(!!perm?.granted);
  const { enqueueUpload } = useQueue();
  const cam = useRef<CameraView>(null);
  const [now, setNow] = useState(new Date());
  const [shots, setShots] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [zoom, setZoom] = useState(0);
  const [flash, setFlash] = useState<"off" | "on" | "auto">("auto");
  const [af, setAf] = useState<"on" | "off">("on");
  const [focusAt, setFocusAt] = useState<{ x: number; y: number } | null>(null);
  const [zoomBase, setZoomBase] = useState(0);
  // Pinch-to-zoom + tap-to-refocus (expo-camera has no focus-point API: toggling autofocus forces a new focus pass).
  const gestures = Gesture.Simultaneous(
    Gesture.Pinch().runOnJS(true)
      .onStart(() => setZoomBase(zoom))
      .onUpdate((e) => setZoom(Math.max(0, Math.min(1, zoomBase + (e.scale - 1) * 0.35)))),
    Gesture.Tap().runOnJS(true).onEnd((e) => {
      setFocusAt({ x: e.x, y: e.y });
      setAf("off");
      setTimeout(() => setAf("on"), 60);
      setTimeout(() => setFocusAt(null), 900);
    }),
  );

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  const [backup, setBackup] = useState<BackupPerm | null>(null);
  useEffect(() => {
    backupPermission().then(setBackup);
  }, []);

  if (!perm) return <View style={s.root} />;
  if (!perm.granted) return <PermissionGate kind="camera" perm={perm} request={requestPerm} />;

  const shoot = async () => {
    if (!cam.current || busy) return;
    setBusy(true);
    try {
      const captured = new Date();
      const pos = freshFix(geo.pos);
      const photo = await cam.current.takePictureAsync({ quality: 1, skipProcessing: false }); // full quality: the backup is this exact file; uploads are compressed separately
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
      const id = await enqueueUpload(p.projectId, p.ncId, photo.uri, "image/jpeg", {
        kind: "photo", source: "in_app_camera", stage: p.stage as EvidenceMeta["stage"], captured_at: captured.toISOString(), ...tzInfo(),
        lat: pos?.coords.latitude ?? null, lng: pos?.coords.longitude ?? null, accuracy_m: pos?.coords.accuracy ?? null,
      });
      // local backup of the unstamped original, once per capture (never on upload retries); works offline
      backupPhoto(photo.uri, id).then((where) => where === "private" && setBackup((b) => (b === "granted" ? "denied" : b)));
      setShots((x) => [photo.uri, ...x]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={s.root} testID="camera-screen">
      <GestureDetector gesture={gestures}>
        <View style={s.cam} collapsable={false}>
          <CameraView ref={cam} style={{ flex: 1 }} facing="back" zoom={zoom} flash={flash} autofocus={af} onCameraReady={() => setReady(true)} testID="camera-view" />
          {focusAt ? <View pointerEvents="none" style={[s.focus, { left: focusAt.x - 36, top: focusAt.y - 36 }]} testID="camera-focus-ring" /> : null}
          <View pointerEvents="box-none" style={s.sideBar}>
            <Pressable onPress={() => setFlash(flash === "auto" ? "on" : flash === "on" ? "off" : "auto")} style={s.iconBtn} testID="camera-flash-button" hitSlop={6}>
              <Feather name={flash === "off" ? "zap-off" : "zap"} size={22} color="#FFFFFF" />
              <Text style={s.sideText}>{flash.toUpperCase()}</Text>
            </Pressable>
            <Pressable onPress={() => setZoom(zoom > 0.05 ? 0 : 0.3)} style={s.iconBtn} testID="camera-zoom-button" hitSlop={6}>
              <Text style={s.sideText}>{zoom > 0.05 ? `${(1 + zoom * 9).toFixed(1)}x` : "1x"}</Text>
            </Pressable>
          </View>
        </View>
      </GestureDetector>
      <View style={[s.topBar, { top: insets.top + space.sm }]}>
        <Pressable onPress={() => router.back()} style={s.iconBtn} testID="camera-close-button" hitSlop={6}>
          <Feather name="x" size={24} color="#FFFFFF" />
        </Pressable>
        <View style={s.countTag}><Text style={s.countText} testID="camera-shot-count">{shots.length} PHOTO{shots.length === 1 ? "" : "S"} QUEUED</Text></View>
      </View>
      <View style={[s.bottom, { paddingBottom: insets.bottom + space.md }]}>
        {backup && backup !== "granted" ? (
          <View style={bk.bar} testID="backup-banner">
            <Text style={bk.text}>
              {backup === "undetermined" ? "Keep a backup copy of each photo in your Photos app?" : "Photo backup to your Photos app is off. Photos are still kept safely inside RemedialFlow."}
            </Text>
            {backup === "blocked" ? (
              <Pressable onPress={() => Linking.openSettings()} style={bk.btn} testID="backup-open-settings"><Text style={bk.btnText}>Open Settings</Text></Pressable>
            ) : (
              <Pressable onPress={() => requestBackupPermission().then(setBackup)} style={bk.btn} testID="backup-allow"><Text style={bk.btnText}>{backup === "undetermined" ? "Allow" : "Try again"}</Text></Pressable>
            )}
            <Pressable onPress={() => setBackup("granted")} hitSlop={8} testID="backup-dismiss"><Feather name="x" size={18} color="#FFFFFF" /></Pressable>
          </View>
        ) : null}
        <StampBlock geo={geo} p={p} now={now} />
        <View style={s.controls}>
          <ScrollView horizontal style={{ flex: 1 }} contentContainerStyle={{ gap: 4 }} showsHorizontalScrollIndicator={false}>
            {shots.slice(0, 6).map((u) => <Image key={u} source={{ uri: u }} style={s.mini} contentFit="cover" />)}
          </ScrollView>
          <Pressable onPress={shoot} disabled={!ready || busy} style={({ pressed }) => [s.shutter, (pressed || busy) && { transform: [{ scale: 0.92 }] }, !ready && { opacity: 0.4 }]} testID="camera-shutter-button" />
          <View style={{ flex: 1, alignItems: "flex-end" }}>
            <Pressable onPress={() => router.back()} style={s.doneBtn} testID="camera-done-button">
              <Text style={s.doneText}>DONE</Text>
            </Pressable>
          </View>
        </View>
      </View>
    </View>
  );
}

