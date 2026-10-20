import { memo, useState } from "react";
import { Image } from "expo-image";
import { Pressable, ScrollView, Text, View } from "react-native";
import { useRouter } from "expo-router";
import * as ImagePicker from "expo-image-picker";
import { NCItem, ProjectRow, DONE, EvidenceFile } from "@/src/projects";
import { evidenceState, QueueItem, STATUS_LABEL, tzInfo, useQueue } from "@/src/queue";
import { Badge, Btn, Feather, Label, Sheet, useToast } from "@/src/ui";
import { MediaViewer, Thumb } from "@/src/components/Media";
import { PhotoViewer } from "@/src/components/PhotoViewer";
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
  const { enqueueSubmit, enqueueUpload, discard, retry, retryNc, pct } = useQueue();
  const [view, setView] = useState<EvidenceFile | null>(null);
  const [pv, setPv] = useState<{ files: EvidenceFile[]; i: number } | null>(null);
  const [confirm, setConfirm] = useState<QueueItem | null>(null);
  const [armed, setArmed] = useState(false);
  const openConfirm = (q: QueueItem) => {
    setArmed(false);
    setConfirm(q);
  };

  const done = DONE.includes(nc.status);
  const uploads = queued.filter((q) => q.type === "upload");
  const ev = evidenceState(queued);
  const sub = queued.find((q) => q.type === "submit");
  const submitQueued = !!sub && sub.status !== "failed";
  const drafts = nc.photos.filter((p) => p.status === "draft");
  const submitted = nc.photos.filter((p) => p.status === "submitted");
  const canSubmit = !done && nc.status !== "assessing" && !submitQueued && (nc.draft_count > 0 || uploads.length > 0);
  const badge = ev
    ? { status: { failed: "failed", submitting: "uploading", uploading: "uploading", submitted: "approved", pending: "pending" }[ev.key], label: ev.label }
    : { status: nc.status, label: nc.status === "assessing" ? "Awaiting Review" : undefined };
  const params = { projectId: project.id, ncId: nc.id, ref: project.ref, postcode: project.postcode, nc: nc.number };

  const pickGallery = async () => {
    const r = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images", "videos"],
      allowsMultipleSelection: true,
      selectionLimit: 10,
      quality: 1, // no re-compression of photos
      exif: true,
      // iOS: hand over the original file (no HEVC->H.264 transcode / downscale)
      preferredAssetRepresentationMode: ImagePicker.UIImagePickerPreferredAssetRepresentationMode.Current,
      videoExportPreset: ImagePicker.VideoExportPreset.Passthrough,
    });
    if (r.canceled) return;
    const photos = r.assets.filter((a) => a.type !== "video");
    const videos = r.assets.filter((a) => a.type === "video");
    const bad = videos.filter((a) => a.mimeType && !/mp4|quicktime|mov/i.test(a.mimeType));
    const video = videos.find((a) => !bad.includes(a));
    for (const a of photos) {
      await enqueueUpload(project.id, nc.id, a.uri, a.mimeType || "image/jpeg", {
        // captured_at only from the file's own EXIF; GPS is never claimed for gallery media
        kind: "photo", source: "gallery", captured_at: exifDate(a.exif), ...tzInfo(), lat: null, lng: null, accuracy_m: null,
      });
    }
    if (video) {
      await enqueueUpload(project.id, nc.id, video.uri, video.mimeType || (/\.mov$/i.test(video.uri) ? "video/quicktime" : "video/mp4"), {
        kind: "video", source: "gallery", captured_at: null, ...tzInfo(), lat: null, lng: null, accuracy_m: null,
        duration_s: video.duration ? Math.round(video.duration / 100) / 10 : null,
      });
    }
    const parts = [photos.length ? `${photos.length} photo(s)` : "", video ? "1 video" : ""].filter(Boolean).join(" + ");
    const notes = [videos.length - bad.length > 1 ? "only one video per NC, first one used" : "", bad.length ? "unsupported video skipped (MP4/MOV only)" : ""].filter(Boolean).join(" · ");
    toast(parts ? `${parts} queued · marked as gallery upload${notes ? ` · ${notes}` : ""}` : notes || "Nothing selected");
  };

  const submit = async () => {
    await enqueueSubmit(project.id, nc.id);
    toast(uploads.some((q) => q.status !== "uploaded") ? "Submit queued · sends after uploads finish" : "Submitting…");
  };

  return (
    <View style={[s.card, expanded && { borderColor: colors.brandPrimary }]} testID={`nc-card-${nc.number}`}>
      <Pressable onPress={() => onToggle(nc.id)} style={s.head} testID={`nc-toggle-${nc.number}`}>
        <View style={{ flex: 1, gap: 4 }}>
          <View style={s.headRow}>
            <Text style={s.num}>{nc.number}</Text>
            <Badge status={badge.status} label={badge.label} testID={`nc-status-${nc.number}`} />
            {nc.safety_critical ? <Badge status="rejected" label="Safety" /> : null}
          </View>
          <Text style={s.desc}>{nc.description}</Text>
        </View>
        <Feather name={expanded ? "chevron-up" : "chevron-down"} size={22} color={colors.onSurface} />
      </Pressable>

      {expanded ? (
        <View style={s.body}>
          {ev?.key === "failed" ? (
            <View style={[s.feedback, { backgroundColor: colors.error }]} testID={`nc-upload-error-${nc.number}`}>
              <Text style={[s.fbText, { color: colors.onError }]}>{ev.error || "Upload failed."} Your evidence is still saved on this phone.</Text>
              <Btn title="Retry upload" icon="refresh-cw" small onPress={() => retryNc(nc.id)} style={{ marginTop: space.sm }} testID={`nc-retry-${nc.number}`} />
            </View>
          ) : ev?.key === "pending" ? (
            <Text style={s.muted} testID={`nc-pending-note-${nc.number}`}>{sub ? "Submit is queued. It sends automatically once all evidence has uploaded." : "Evidence is saved on this phone and uploads automatically when connected."}</Text>
          ) : null}
          {nc.ai?.feedback || nc.review_note || nc.status === "rejected" ? (
            <View style={[s.feedback, nc.status === "more_evidence" || nc.status === "rejected" ? { backgroundColor: colors.warning } : null]} testID={`nc-feedback-${nc.number}`}>
              <Text style={s.fbText}>{nc.status === "rejected" ? `Rejected by ${nc.rejection_source === "trustmark" ? "TrustMark" : nc.rejection_source === "admin" ? "Admin" : "Employee"}${nc.review_note ? ": " : ""}` : ""}{nc.review_note || (nc.status === "rejected" ? "" : nc.ai?.feedback)}</Text>
            </View>
          ) : null}

          <Label>Original TrustMark photos</Label>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.row}>
            {nc.trustmark.length ? nc.trustmark.map((f, i) => <Thumb key={f.id} f={f} size={96} onPress={() => setPv({ files: nc.trustmark, i })} testID={`trustmark-thumb-${f.id}`} />) : <Text style={s.muted}>None provided</Text>}
          </ScrollView>

          {nc.original_text && nc.original_text !== nc.description ? (
            <>
              <Label>Original TrustMark wording</Label>
              <Text style={s.work} selectable>{nc.original_text}</Text>
            </>
          ) : null}
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
                {drafts.map((f, i) => <Thumb key={f.id} f={f} onPress={() => setPv({ files: drafts, i })} testID={`draft-thumb-${f.id}`} />)}
                {uploads.map((q) => (
                  <Pressable key={q.id} style={s.local} onPress={() => (q.status === "failed" ? openConfirm(q) : undefined)} onLongPress={() => q.status !== "uploaded" && q.status !== "uploading" && openConfirm(q)} testID={`queued-item-${q.id}`}>
                    {q.mime?.includes("video") ? <Feather name="film" size={22} color={colors.onSurface} /> : <Image source={{ uri: q.uri }} style={{ width: "100%", height: "100%" }} contentFit="cover" />}
                    <View style={[s.localTag, q.status === "failed" && { backgroundColor: colors.error }, q.status === "uploaded" && { backgroundColor: colors.success }]}>
                      <Text style={[s.localText, q.status === "failed" && { color: colors.onError }, q.status === "uploaded" && { color: colors.onSuccess }]} testID={`queued-status-${q.id}`}>{q.status === "uploading" && pct[q.id] != null ? `${Math.round(pct[q.id] * 100)}%` : STATUS_LABEL[q.status].toUpperCase()}</Text>
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
                {submitted.map((f, i) => <Thumb key={f.id} f={f} size={56} onPress={() => setPv({ files: submitted, i })} testID={`submitted-thumb-${f.id}`} />)}
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
      {pv ? <PhotoViewer files={pv.files} index={pv.i} onClose={() => setPv(null)} /> : null}
      <Sheet visible={!!confirm} onClose={() => setConfirm(null)} title={confirm ? STATUS_LABEL[confirm.status] : "Unsynced item"} testID="queued-item-sheet">
        <Text style={s.work} testID="queued-item-error">{confirm?.error ? confirm.error : "This file is stored on this phone and has not reached the server yet."}</Text>
        <Btn title="Retry upload" icon="refresh-cw" onPress={() => { if (confirm) retry(confirm.id); setConfirm(null); }} testID="queued-item-retry" />
        {/* two-step delete so pending evidence is never lost by a stray tap */}
        <Btn title={armed ? "Tap again to permanently delete" : "Delete from phone"} icon="trash-2" variant={armed ? "danger" : "outline"} onPress={() => { if (!armed) return setArmed(true); if (confirm) discard(confirm.id); setConfirm(null); }} testID="queued-item-delete" />
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
  work: { fontSize: 15, lineHeight: 21, color: c.onSurface, marginBottom: space.sm },
  muted: { fontFamily: fonts.mono, fontSize: 12, color: c.muted },
  actions: { flexDirection: "row", gap: space.sm, marginTop: space.sm },
  local: { width: 72, height: 72, borderWidth: BORDER, borderColor: c.border, borderStyle: "dashed", alignItems: "center", justifyContent: "center", overflow: "hidden", backgroundColor: c.surfaceSecondary },
  localTag: { position: "absolute", bottom: 0, left: 0, right: 0, backgroundColor: c.warning, alignItems: "center" },
  localText: { fontFamily: fonts.mono, fontSize: 9, color: c.onWarning },
}));
