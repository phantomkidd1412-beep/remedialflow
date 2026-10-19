import { useEffect, useRef, useState } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { CameraView, useCameraPermissions } from "expo-camera";
import * as Haptics from "expo-haptics";
import { Image } from "expo-image";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { tzInfo, useQueue } from "@/src/queue";
import { Feather } from "@/src/ui";
import { freshFix, useGeo } from "@/src/components/useGeo";
import { P, PermissionGate, StampBlock, useCameraStyles } from "@/src/components/CameraParts";
import { space } from "@/src/theme";


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

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  if (!perm) return <View style={s.root} />;
  if (!perm.granted) return <PermissionGate kind="camera" perm={perm} request={requestPerm} />;

  const shoot = async () => {
    if (!cam.current || busy) return;
    setBusy(true);
    try {
      const captured = new Date();
      const pos = freshFix(geo.pos);
      const photo = await cam.current.takePictureAsync({ quality: 0.95, skipProcessing: false });
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
      await enqueueUpload(p.projectId, p.ncId, photo.uri, "image/jpeg", {
        kind: "photo", source: "in_app_camera", captured_at: captured.toISOString(), ...tzInfo(),
        lat: pos?.coords.latitude ?? null, lng: pos?.coords.longitude ?? null, accuracy_m: pos?.coords.accuracy ?? null,
      });
      setShots((x) => [photo.uri, ...x]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={s.root} testID="camera-screen">
      <CameraView ref={cam} style={s.cam} facing="back" onCameraReady={() => setReady(true)} />
      <View style={[s.topBar, { top: insets.top + space.sm }]}>
        <Pressable onPress={() => router.back()} style={s.iconBtn} testID="camera-close-button" hitSlop={6}>
          <Feather name="x" size={24} color="#FFFFFF" />
        </Pressable>
        <View style={s.countTag}><Text style={s.countText} testID="camera-shot-count">{shots.length} PHOTO{shots.length === 1 ? "" : "S"} QUEUED</Text></View>
      </View>
      <View style={[s.bottom, { paddingBottom: insets.bottom + space.md }]}>
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

