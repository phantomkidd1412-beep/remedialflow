import { useEffect, useRef, useState } from "react";
import { FlatList, Image, Platform, Pressable, ScrollView, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import * as ImagePicker from "expo-image-picker";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useProject, STAGES } from "@/src/projects";
import { useQueue, STATUS_LABEL, tzInfo } from "@/src/queue";
import { Btn, Empty, Feather, Label, Loading, useToast } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, CONTENT_MAX, fonts, makeStyles, radius, space, useTheme } from "@/src/theme";

type Stage = "pre" | "mid" | "final";
type Row = { key: string; uri: string; mime: string; video: boolean; name: string; ncId: string | null; stage: Stage | null };

/** Staff batch upload: pick or drop many photos/videos, assign each to an NC (+ stage), then send them through the
 *  same persistent, compressing, retrying upload queue the installers use. Nothing is attached until confirmed. */
export default function BatchUpload() {
  const s = useStyles();
  const { colors } = useTheme();
  const toast = useToast();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { pid } = useLocalSearchParams<{ pid: string }>();
  const proj = useProject(pid);
  const { items, enqueueUpload, retry, discard, pct } = useQueue();
  const [rows, setRows] = useState<Row[]>([]);
  const [sent, setSent] = useState<string[]>([]); // queue item ids created by this screen
  const [orig, setOrig] = useState(false);
  const [cancelled, setCancelled] = useState<string[]>([]);
  const drop = useRef<View>(null);

  const add = (files: { uri: string; mime: string; name: string }[]) =>
    setRows((r) => [
      ...r,
      ...files
        .filter((f) => /^(image|video)\//.test(f.mime))
        .map((f, i) => ({ key: `${Date.now()}-${i}-${f.name}`, uri: f.uri, mime: f.mime, video: f.mime.startsWith("video"), name: f.name, ncId: null, stage: null })),
    ]);

  // desktop drag-and-drop (web only)
  useEffect(() => {
    if (Platform.OS !== "web") return;
    const el = drop.current as unknown as HTMLElement | null;
    if (!el?.addEventListener) return;
    const over = (e: DragEvent) => e.preventDefault();
    const onDrop = (e: DragEvent) => {
      e.preventDefault();
      add(Array.from(e.dataTransfer?.files ?? []).map((f) => ({ uri: URL.createObjectURL(f), mime: f.type, name: f.name })));
    };
    el.addEventListener("dragover", over);
    el.addEventListener("drop", onDrop);
    return () => {
      el.removeEventListener("dragover", over);
      el.removeEventListener("drop", onDrop);
    };
  }, [proj.data]);

  if (proj.isLoading) return <Loading />;
  if (!proj.data) return <Empty icon="alert-circle" text="Project not found" />;
  const ncs = proj.data.ncs;

  const pick = async () => {
    const r = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ["images", "videos"], allowsMultipleSelection: true, selectionLimit: 0, quality: 1 });
    if (!r.canceled) add(r.assets.map((a) => ({ uri: a.uri, mime: a.mimeType || (a.type === "video" ? "video/mp4" : "image/jpeg"), name: a.fileName || a.uri.split("/").pop() || "file" })));
  };
  const set = (key: string, patch: Partial<Row>) => setRows((r) => r.map((x) => (x.key === key ? { ...x, ...patch } : x)));
  const ready = rows.length > 0 && rows.every((r) => r.ncId); // stage is optional: AI arranges photos in the PDF

  const upload = async () => {
    if (!ready) return toast("Choose an NC for every file first");
    const ids: string[] = [];
    for (const r of rows) {
      ids.push(await enqueueUpload(pid, r.ncId!, r.uri, r.mime, {
        kind: r.video ? "video" : "photo", source: "employee_upload", stage: r.video ? undefined : r.stage ?? undefined, original: orig || undefined,
        captured_at: null, ...tzInfo(), lat: null, lng: null, accuracy_m: null,
      }));
    }
    setSent((s0) => [...s0, ...ids]);
    toast(`${rows.length} file(s) queued`);
    setRows([]);
  };
  // synced items leave the queue a few seconds after the server confirms them
  const mine = items.filter((i) => sent.includes(i.id));
  const live = sent.filter((id) => !cancelled.includes(id));
  const ok = live.length - mine.filter((i) => i.status !== "uploaded").length;
  const bad = mine.filter((i) => i.status === "failed").length;

  return (
    <View style={s.root}>
      <ScreenHeader title="Batch upload" sub={`${proj.data.project.ref} · ${proj.data.project.address}`} back />
      <ScrollView contentContainerStyle={[s.content, { paddingBottom: insets.bottom + space.xl }]}>
        <View ref={drop} style={s.drop} testID="batch-drop-zone">
          <Feather name="upload-cloud" size={28} color={colors.muted} />
          <Text style={s.dropText}>{Platform.OS === "web" ? "Drag photos and videos here, or" : "Select photos and videos"}</Text>
          <Btn title="Choose files" icon="image" variant="outline" small onPress={pick} testID="batch-pick" />
        </View>
        <Pressable onPress={() => setOrig((o) => !o)} style={s.row} testID="batch-original">
          <Feather name={orig ? "check-square" : "square"} size={18} color={colors.onSurface} />
          <Text style={s.txt}>Original quality (no photo compression)</Text>
        </Pressable>

        {rows.map((r) => (
          <View key={r.key} style={s.card} testID="batch-row">
            <View style={s.row}>
              {r.video ? <View style={[s.thumb, s.vid]}><Feather name="video" size={22} color={colors.muted} /></View> : <Image source={{ uri: r.uri }} style={s.thumb} />}
              <Text style={[s.txt, { flex: 1 }]} numberOfLines={1}>{r.name}</Text>
              <Pressable onPress={() => setRows((x) => x.filter((y) => y.key !== r.key))} hitSlop={10} testID="batch-remove"><Feather name="x" size={20} color={colors.muted} /></Pressable>
            </View>
            <Label>NC</Label>
            <FlatList horizontal data={ncs} keyExtractor={(n) => n.id} showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space.sm }}
              renderItem={({ item: n }) => (
                <Pressable onPress={() => set(r.key, { ncId: n.id })} style={[s.chip, r.ncId === n.id && s.chipOn]} testID={`batch-nc-${n.number}`}>
                  <Text style={[s.chipText, r.ncId === n.id && { color: colors.onBrandSecondary }]}>{n.number}</Text>
                </Pressable>
              )} />
            {!r.video ? (
              <View style={s.row}>
                {STAGES.map(([k, l]) => (
                  <Pressable key={k} onPress={() => set(r.key, { stage: r.stage === k ? null : k })} style={[s.chip, r.stage === k && s.chipOn]} testID={`batch-stage-${k}`}>
                    <Text style={[s.chipText, r.stage === k && { color: colors.onBrandSecondary }]}>{l}</Text>
                  </Pressable>
                ))}
              </View>
            ) : null}
          </View>
        ))}
        {rows.length ? <Btn title={`Upload ${rows.length} file(s)`} icon="upload" disabled={!ready} onPress={upload} testID="batch-upload" /> : null}

        {live.length ? (
          <View style={s.card} testID="batch-progress">
            <Text style={s.head}>{ok} of {live.length} synced{bad ? ` · ${bad} failed` : ""}</Text>
            {mine.map((i) => (
              <View key={i.id} style={s.row}>
                <Text style={[s.txt, { flex: 1 }]} numberOfLines={1}>{ncs.find((n) => n.id === i.ncId)?.number} · {i.meta?.kind}{i.meta?.stage ? ` · ${i.meta.stage}` : ""}</Text>
                <Text style={[s.txt, i.status === "failed" && { color: colors.onError }, i.status === "uploaded" && { color: colors.onSuccess }]}>
                  {i.status === "uploading" && pct[i.id] != null ? `${Math.round(pct[i.id] * 100)}%` : STATUS_LABEL[i.status]}
                </Text>
                {i.status === "failed" ? <Btn title="Retry" small variant="outline" onPress={() => retry(i.id)} /> : null}
                {i.status === "pending" || i.status === "failed" ? <Btn title="Cancel" small variant="outline" onPress={() => { setCancelled((c0) => [...c0, i.id]); discard(i.id); }} /> : null}
              </View>
            ))}
            {ok === live.length ? <Btn title="Back to project" variant="outline" onPress={() => router.back()} /> : null}
          </View>
        ) : null}
      </ScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  content: { padding: space.lg, gap: space.md, width: "100%", maxWidth: CONTENT_MAX * 0.75, alignSelf: "center" },
  drop: { borderWidth: 2, borderStyle: "dashed", borderColor: c.borderStrong, borderRadius: radius.lg, padding: space.xl, alignItems: "center", gap: space.sm, backgroundColor: c.surfaceSecondary },
  dropText: { fontFamily: fonts.text, fontSize: 15, color: c.onSurfaceSecondary },
  card: { borderWidth: BORDER, borderColor: c.border, borderRadius: radius.lg, padding: space.md, gap: space.sm, backgroundColor: c.surface },
  row: { flexDirection: "row", alignItems: "center", gap: space.sm, flexWrap: "wrap", minHeight: 44 },
  thumb: { width: 56, height: 56, borderRadius: radius.sm, backgroundColor: c.surfaceTertiary },
  vid: { alignItems: "center", justifyContent: "center" },
  txt: { fontFamily: fonts.text, fontSize: 14, color: c.onSurface },
  head: { fontFamily: fonts.text, fontSize: 15, fontWeight: "700", color: c.onSurface },
  chip: { minHeight: 40, paddingHorizontal: space.md, justifyContent: "center", borderWidth: BORDER, borderColor: c.border, borderRadius: radius.pill },
  chipOn: { backgroundColor: c.brandSecondary, borderColor: c.brandPrimary },
  chipText: { fontFamily: fonts.text, fontSize: 14, fontWeight: "600", color: c.onSurface },
}));
