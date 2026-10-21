import { useState } from "react";
import { FlatList, Modal, Pressable, ScrollView, Text, useWindowDimensions, View } from "react-native";
import { Image } from "expo-image";
import { Gesture, GestureDetector, GestureHandlerRootView } from "react-native-gesture-handler";
import Animated, { useAnimatedStyle, useSharedValue, withTiming } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { absUrl } from "@/src/api";
import { EvidenceFile } from "@/src/projects";
import { Feather, fmtDate } from "@/src/ui";
import { fonts, makeStyles, space } from "@/src/theme";

function ZoomImage({ uri, cacheKey, w, h }: { uri: string; cacheKey: string; w: number; h: number }) {
  const scale = useSharedValue(1);
  const saved = useSharedValue(1);
  const tx = useSharedValue(0);
  const ty = useSharedValue(0);
  const sx = useSharedValue(0);
  const sy = useSharedValue(0);
  const reset = () => {
    "worklet";
    scale.value = withTiming(1);
    saved.value = 1;
    tx.value = withTiming(0);
    ty.value = withTiming(0);
    sx.value = 0;
    sy.value = 0;
  };
  const pinch = Gesture.Pinch()
    .onUpdate((e) => { scale.value = Math.max(1, Math.min(6, saved.value * e.scale)); })
    .onEnd(() => { saved.value = scale.value; if (scale.value <= 1.02) reset(); });
  // Pan only while zoomed so horizontal swipes still page between photos.
  const pan = Gesture.Pan().minPointers(1).manualActivation(true)
    .onTouchesMove((_e, st) => { if (saved.value > 1) st.activate(); else st.fail(); })
    .onUpdate((e) => { tx.value = sx.value + e.translationX; ty.value = sy.value + e.translationY; })
    .onEnd(() => { sx.value = tx.value; sy.value = ty.value; });
  const dbl = Gesture.Tap().numberOfTaps(2).onEnd(() => {
    if (saved.value > 1) reset();
    else { scale.value = withTiming(2.5); saved.value = 2.5; }
  });
  const style = useAnimatedStyle(() => ({ transform: [{ translateX: tx.value }, { translateY: ty.value }, { scale: scale.value }] }));
  return (
    <GestureDetector gesture={Gesture.Simultaneous(pinch, pan, dbl)}>
      <Animated.View style={[{ width: w, height: h }, style]}>
        <Image source={{ uri, cacheKey }} style={{ width: w, height: h }} contentFit="contain" transition={100} />
      </Animated.View>
    </GestureDetector>
  );
}

/** Full-screen swipeable viewer. Metadata panel only when showInfo (staff/audit). */
export function PhotoViewer({ files, index, onClose, showInfo = false }: { files: EvidenceFile[]; index: number | null; onClose: () => void; showInfo?: boolean }) {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const { width, height } = useWindowDimensions();
  const [cur, setCur] = useState(index ?? 0);
  const [info, setInfo] = useState(false);
  if (index === null) return null;
  const f = files[cur];
  const variant = (x: EvidenceFile) => (x.urls.stamped ? "stamped" : x.urls.display ? "display" : "original");
  return (
    <Modal visible animationType="fade" onRequestClose={onClose} statusBarTranslucent>
      <GestureHandlerRootView style={s.root}>
        <View style={s.root} testID="photo-viewer">
          <FlatList
            data={files}
            horizontal
            pagingEnabled
            initialScrollIndex={index}
            getItemLayout={(_, i) => ({ length: width, offset: width * i, index: i })}
            showsHorizontalScrollIndicator={false}
            keyExtractor={(x) => x.id}
            onMomentumScrollEnd={(e) => setCur(Math.round(e.nativeEvent.contentOffset.x / width))}
            renderItem={({ item }) => (
              <View style={{ width, height, alignItems: "center", justifyContent: "center", overflow: "hidden" }}>
                <ZoomImage uri={absUrl(item.urls[variant(item)])} cacheKey={`${item.id}-${variant(item)}`} w={width} h={height} />
              </View>
            )}
          />
          <View style={[s.top, { top: insets.top + space.sm }]}>
            <Text style={s.counter} testID="photo-viewer-counter">{cur + 1} / {files.length}</Text>
            <View style={{ flexDirection: "row", gap: space.sm }}>
              {showInfo ? (
                <Pressable onPress={() => setInfo(!info)} style={s.btn} testID="photo-viewer-info" hitSlop={8}>
                  <Feather name="info" size={22} color="#FFFFFF" />
                </Pressable>
              ) : null}
              <Pressable onPress={onClose} style={s.btn} testID="photo-viewer-close" hitSlop={8}>
                <Feather name="x" size={24} color="#FFFFFF" />
              </Pressable>
            </View>
          </View>
          {showInfo && info && f ? (
            <ScrollView style={[s.info, { bottom: insets.bottom + space.md }]} testID="photo-viewer-meta">
              <Text style={s.infoText} selectable>
                {`Source: ${f.source}\nDevice capture: ${f.captured_at ? fmtDate(f.captured_at) : "—"} ${f.tz ?? ""}\nServer received: ${fmtDate(f.server_received_at)}\nGPS: ${f.lat != null ? `${f.lat.toFixed(5)}, ${f.lng?.toFixed(5)} ±${Math.round(f.accuracy_m ?? 0)}m` : "not available"}\nSHA-256: ${f.sha256}`}
              </Text>
            </ScrollView>
          ) : null}
        </View>
      </GestureHandlerRootView>
    </Modal>
  );
}

// Viewer is always black; literals intentional.
const useStyles = makeStyles(() => ({
  root: { flex: 1, backgroundColor: "#000000" },
  top: { position: "absolute", left: space.lg, right: space.lg, flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  counter: { fontFamily: fonts.text, fontSize: 14, color: "#FFFFFF", backgroundColor: "rgba(0,0,0,0.6)", paddingHorizontal: 8, paddingVertical: 4 },
  btn: { width: 44, height: 44, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(0,0,0,0.6)" },
  info: { position: "absolute", left: space.lg, right: space.lg, maxHeight: 180, backgroundColor: "rgba(0,0,0,0.8)", padding: space.md },
  infoText: { fontFamily: fonts.text, fontSize: 12, color: "#FFFFFF" },
}));
