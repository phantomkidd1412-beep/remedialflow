import { memo, useState } from "react";
import { Image } from "expo-image";
import { Pressable, ScrollView, Text, View } from "react-native";
import { useRouter } from "expo-router";
import * as ImagePicker from "expo-image-picker";
import { NCItem, ProjectRow, DONE, EvidenceFile } from "@/src/projects";
import { QueueItem, tzInfo, useQueue } from "@/src/queue";
import { Badge, Btn, Feather, Label, Sheet, useToast } from "@/src/ui";
import { MediaViewer, Thumb } from "@/src/components/Media";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

function exifDate(exif: any): string | null {
  const raw: string | undefined = exif?.DateTimeOriginal || exif?.DateTime;
  if (!raw) return null;
  const m = raw.match(/(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})/);
  return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).toISOString() : null;
}

export const NCCard = memo(function NCCard({ nc, project, expanded, onToggle, queued }: { nc: NCItem; project: ProjectRow; expanded: boolean; onToggle: (id: string) => void; queued: QueueItem[] }) {
  const s = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const toast = useToast();
  const { enqueueSubmit, enqueueUpload, discard, retry } = useQueue();
  const [view, setView] = useState<EvidenceFile | null>(null);
  const [confirm, setConfirm] = useState<QueueItem | null>(null);

  const done = DONE.includes(nc.status);
  const uploads = queued.filter((q) => q.type === "upload");
  const submitQueued = queued.some((q) => q.type === "submit");
  const drafts = nc.photos.filter((p) => p.status === "draft");
  const submitted = nc.photos.filter((p) => p.status === "submitted");
  const canSubmit = !done && nc.status !== "assessing" && !submitQueued && (nc.draft_count > 0 || uploads.length > 0);
  const params = { projectId: project.id, ncId: nc.id, ref: project.ref, postcode: project.postcode, nc: nc.number };

  const pickGallery = async () => {
    const r = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ["images"], allowsMultipleSelection: true, quality: 1, exif: true, selectionLimit: 10 });
    if (r.canceled) return;
    for (const a of r.assets) {
      await enqueueUpload(project.id, nc.id, a.uri, a.mimeType || "image/jpeg", {
        kind: "photo", source: "gallery", captured_at: exifDate(a.exif), ...tzInfo(), lat: null, lng: null, accuracy_m: null,
      });
    }
    toast(`${r.assets.length} gallery photo(s) queued · marked as gallery upload`);
  };

  const submit = async () => {
    await enqueueSubmit(project.id, nc.id);
    toast(uploads.length ? "Submit queued · sends after uploads finish" : "Submitting…");
  };

  return (
    <View style={[s.card, expanded && { borderColor: colors.brandPrimary }]} testID={`nc-card-${nc.number}`}>
      <Pressable onPress={() => onToggle(nc.id)} style={s.head} testID={`nc-toggle-${nc.number}`}>
        <View style={{ flex: 1, gap: 4 }}>
          <View style={s.headRow}>
            <Text style={s.num}>{nc.number}</Text>
            <Badge status={submitQueued ? "uploading" : nc.status} label={submitQueued ? "Submitting" : undefined} testID={`nc-status-${nc.number}`} />
            {nc.safety_critical ? <Badge status="rejected" label="Safety" /> : null}
          </View>
          <Text style={s.desc} numberOfLines={expanded ? undefined : 2}>{nc.description}</Text>
        </View>
        <Feather name={expanded ? "chevron-up" : "chevron-down"} size={22} color={colors.onSurface} />
      </Pressable>

      {expanded ? (
        <View style={s.body}>
          {nc.ai?.feedback || nc.review_note ? (
            <View style={[s.feedback, nc.status === "more_evidence" || nc.status === "rejected" ? { backgroundColor: colors.warning } : null]} testID={`nc-feedback-${nc.number}`}>
              <Text style={s.fbText}>{nc.review_note || nc.ai?.feedback}</Text>
            </View>
          ) : null}

          <Label>Original TrustMark photos</Label>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.row}>
            {nc.trustmark.length ? nc.trustmark.map((f) => <Thumb key={f.id} f={f} size={96} onPress={() => setView(f)} testID={`trustmark-thumb-${f.id}`} />) : <Text style={s.muted}>None provided</Text>}
          </ScrollView>

          <Label>Required remedial work</Label>
          <Text style={s.work}>{nc.required_work}</Text>

          {!done ? (
            <View style={s.actions}>
              <Btn title="Take photos" icon="camera" onPress={() => router.push({ pathname: "/camera", params })} style={{ flex: 1.4 }} testID={`take-photos-${nc.number}`} disabled={nc.status === "assessing"} />
              <Btn title={nc.video ? "Replace" : "Video"} icon="video" variant="outline" onPress={() => router.push({ pathname: "/video", params })} style={{ flex: 1 }} testID={`record-video-${nc.number}`} disabled={nc.status === "assessing"} />
            </View>
          ) : null}

          {drafts.length || uploads.length || nc.video ? (
            <>
              <Label>New evidence {nc.status === "assessing" ? "" : "(not yet submitted)"}</Label>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.row}>
                {nc.video ? <Thumb f={nc.video} onPress={() => setView(nc.video)} testID={`video-thumb-${nc.number}`} /> : null}
                {drafts.map((f) => <Thumb key={f.id} f={f} onPress={() => setView(f)} testID={`draft-thumb-${f.id}`} />)}
                {uploads.map((q) => (
                  <Pressable key={q.id} style={s.local} onPress={() => (q.status === "failed" ? setConfirm(q) : undefined)} onLongPress={() => setConfirm(q)} testID={`queued-item-${q.id}`}>
                    {q.mime?.includes("video") ? <Feather name="film" size={22} color={colors.onSurface} /> : <Image source={{ uri: q.uri }} style={{ width: "100%", height: "100%" }} contentFit="cover" />}
                    <View style={[s.localTag, q.status === "failed" && { backgroundColor: colors.error }]}>
                      <Text style={[s.localText, q.status === "failed" && { color: colors.onError }]}>{q.status === "failed" ? "FAILED" : q.status === "uploading" ? "UPLOADING" : "QUEUED"}</Text>
                    </View>
                  </Pressable>
                ))}
              </ScrollView>
              {nc.video_versions > 1 ? <Text style={s.muted}>{nc.video_versions - 1} previous video version(s) kept for audit</Text> : null}
            </>
          ) : null}

          {submitted.length ? (
            <>
              <Label>Submitted evidence</Label>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.row}>
                {submitted.map((f) => <Thumb key={f.id} f={f} size={56} onPress={() => setView(f)} testID={`submitted-thumb-${f.id}`} />)}
              </ScrollView>
            </>
          ) : null}

          {!done ? (
            <View style={s.actions}>
              <Btn title="Gallery" icon="image" variant="outline" small onPress={pickGallery} testID={`gallery-upload-${nc.number}`} disabled={nc.status === "assessing"} />
              <Btn title={nc.status === "assessing" ? "AI checking…" : "Submit evidence"} icon="send" variant="dark" onPress={submit} disabled={!canSubmit} style={{ flex: 1 }} testID={`submit-evidence-${nc.number}`} />
            </View>
          ) : null}
        </View>
      ) : null}

      <MediaViewer f={view} onClose={() => setView(null)} />
      <Sheet visible={!!confirm} onClose={() => setConfirm(null)} title="Unsynced item" testID="queued-item-sheet">
        <Text style={s.work}>{confirm?.error ? `Error: ${confirm.error}` : "This file is stored on this phone and has not reached the server yet."}</Text>
        <Btn title="Retry upload" icon="refresh-cw" onPress={() => { if (confirm) retry(confirm.id); setConfirm(null); }} testID="queued-item-retry" />
        <Btn title="Delete from phone" icon="trash-2" variant="danger" onPress={() => { if (confirm) discard(confirm.id); setConfirm(null); }} testID="queued-item-delete" />
      </Sheet>
    </View>
  );
});

const useStyles = makeStyles((c) => ({
  card: { borderWidth: BORDER, borderColor: c.border, backgroundColor: c.surface },
  head: { flexDirection: "row", alignItems: "center", gap: space.md, padding: space.lg, minHeight: 64 },
  headRow: { flexDirection: "row", alignItems: "center", gap: space.sm, flexWrap: "wrap" },
  num: { fontFamily: fonts.mono, fontSize: 14, color: c.onSurface },
  desc: { fontFamily: fonts.display, fontWeight: "700", fontSize: 16, color: c.onSurface },
  body: { borderTopWidth: BORDER, borderColor: c.border, padding: space.lg, gap: space.sm },
  feedback: { backgroundColor: c.brandTertiary, borderWidth: BORDER, borderColor: c.border, padding: space.md, marginBottom: space.sm },
  fbText: { fontFamily: fonts.mono, fontSize: 12, color: c.onSurface },
  row: { gap: space.sm, paddingBottom: space.sm },
  work: { fontFamily: fonts.mono, fontSize: 13, color: c.onSurface, marginBottom: space.sm },
  muted: { fontFamily: fonts.mono, fontSize: 12, color: c.muted },
  actions: { flexDirection: "row", gap: space.sm, marginTop: space.sm },
  local: { width: 72, height: 72, borderWidth: BORDER, borderColor: c.border, borderStyle: "dashed", alignItems: "center", justifyContent: "center", overflow: "hidden", backgroundColor: c.surfaceSecondary },
  localTag: { position: "absolute", bottom: 0, left: 0, right: 0, backgroundColor: c.warning, alignItems: "center" },
  localText: { fontFamily: fonts.mono, fontSize: 9, color: c.onWarning },
}));
