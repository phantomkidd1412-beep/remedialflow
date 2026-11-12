import { memo, useState } from "react";
import { Image } from "expo-image";
import { FlatList, Pressable, ScrollView, Text, View } from "react-native";
import { useRouter } from "expo-router";
import * as ImagePicker from "expo-image-picker";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/src/api";
import { NCItem, ProjectRow, DONE, EvidenceFile, SIMPLE, simpleStatus, useMediaConfig } from "@/src/projects";
import { SafetyToggle } from "@/src/components/SafetyToggle";
import { evidenceState, QueueItem, STATUS_LABEL, tzInfo, useQueue } from "@/src/queue";
import { Badge, Btn, Feather, Label, Sheet, useToast } from "@/src/ui";
import { MediaViewer, Thumb } from "@/src/components/Media";
import { PhotoViewer } from "@/src/components/PhotoViewer";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

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
  const [removing, setRemoving] = useState<EvidenceFile | null>(null);
  const [busy, setBusy] = useState(false);
  const qc = useQueryClient();
  const openConfirm = (q: QueueItem) => {
    setArmed(false);
    setConfirm(q);
  };
  // Unsubmitted evidence only (server refuses anything else): soft-removed from the NC and its PDF selection.
  const remove = async () => {
    if (!removing) return;
    setBusy(true);
    try {
      await api(`/evidence/${removing.id}`, { method: "DELETE" });
      await qc.invalidateQueries({ queryKey: ["project", project.id] });
      toast(removing.kind === "video" ? "Video removed" : "Photo removed");
      setRemoving(null);
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(false);
    }
  };

  const done = DONE.includes(nc.status);
  const uploads = queued.filter((q) => q.type === "upload");
  const ev = evidenceState(queued);
  const sub = queued.find((q) => q.type === "submit");
  const submitQueued = !!sub && sub.status !== "failed";
  const drafts = nc.photos.filter((p) => p.status === "draft");
  const submitted = nc.photos.filter((p) => p.status === "submitted");
  const draftVideos = (nc.videos ?? []).filter((v) => v.status === "draft");
  const submittedVideos = (nc.videos ?? []).filter((v) => v.status !== "draft");
  const { maxVideoMb } = useMediaConfig();
  const canSubmit = !done && nc.status !== "assessing" && !submitQueued && (nc.draft_count > 0 || uploads.length > 0);
  const simple = simpleStatus(nc, uploads.length);
  const st = SIMPLE[simple];
  const badge = ev
    ? { status: { failed: "failed", submitting: "uploading", uploading: "uploading", compressing: "uploading", submitted: "approved", pending: "pending" }[ev.key], label: ev.label }
    : { status: st.badge, label: st.label };
  const addedPhotos = drafts.length + uploads.filter((q) => q.meta?.kind !== "video").length;
  const addedVideos = draftVideos.length + uploads.filter((q) => q.meta?.kind === "video").length;
  // Mandatory evidence for new NCs: pre, mid and final photos plus a video for this NC (queued items count too)
  const allPhotos = [...nc.photos.filter((p) => p.status !== "superseded"), ...uploads.filter((q) => q.meta?.kind === "photo").map((q) => ({ stage: q.meta?.stage }))];
  const has = {
    pre: allPhotos.some((p) => p.stage === "pre"), mid: allPhotos.some((p) => p.stage === "mid"), final: allPhotos.some((p) => p.stage === "final"),
    video: (nc.videos ?? []).some((v) => v.status !== "superseded") || uploads.some((q) => q.meta?.kind === "video"),
  };
  const missing = nc.requires_stages ? [...(allPhotos.length ? [] : ["a photo"]), ...(has.video || !nc.video_required ? [] : ["a remediation video"])] : [];
  const [orig, setOrig] = useState(false);
  const params = { projectId: project.id, ncId: nc.id, ref: project.ref, postcode: project.postcode, nc: nc.number, orig: orig ? "1" : "0" };

  const pickGallery = async () => {
    const r = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images", "videos"],
      allowsMultipleSelection: true,
      selectionLimit: 0, // no fixed photo limit per NC (0 = unlimited)
      quality: 1, // photos are compressed by the upload queue (or kept original when chosen)
      exif: true,
      preferredAssetRepresentationMode: ImagePicker.UIImagePickerPreferredAssetRepresentationMode.Current,
      // iOS: compress gallery videos to H.264 1080p unless "Original quality" is on
      videoExportPreset: orig ? ImagePicker.VideoExportPreset.Passthrough : ImagePicker.VideoExportPreset.H264_1920x1080,
    });
    if (r.canceled) return;
    const photos = r.assets.filter((a) => a.type !== "video");
    const videos = r.assets.filter((a) => a.type === "video");
    const bad = videos.filter((a) => a.mimeType && !/mp4|quicktime|mov/i.test(a.mimeType));
    const big = videos.filter((a) => !bad.includes(a) && a.fileSize && a.fileSize > maxVideoMb * 1024 * 1024);
    const okVideos = videos.filter((a) => !bad.includes(a) && !big.includes(a));
    for (const a of photos) {
      await enqueueUpload(project.id, nc.id, a.uri, a.mimeType || "image/jpeg", {
        // captured_at only from the file's own EXIF; GPS is never claimed for gallery media
        kind: "photo", source: "gallery", original: orig || undefined, captured_at: exifDate(a.exif), ...tzInfo(), lat: null, lng: null, accuracy_m: null,
      });
    }
    for (const video of okVideos) {
      await enqueueUpload(project.id, nc.id, video.uri, video.mimeType || (/\.mov$/i.test(video.uri) ? "video/quicktime" : "video/mp4"), {
        kind: "video", source: "gallery", captured_at: null, ...tzInfo(), lat: null, lng: null, accuracy_m: null,
        duration_s: video.duration ? Math.round(video.duration / 100) / 10 : null,
      });
    }
    const parts = [photos.length ? `${photos.length} photo(s)` : "", okVideos.length ? `${okVideos.length} video(s)` : ""].filter(Boolean).join(" + ");
    const notes = [big.length ? `${big.length} video(s) over ${maxVideoMb} MB skipped` : "", bad.length ? "unsupported video skipped (MP4/MOV only)" : ""].filter(Boolean).join(" · ");
    toast(parts ? `${parts} queued · marked as gallery upload${notes ? ` · ${notes}` : ""}` : notes || "Nothing selected");
  };

  const submit = async () => {
    if (missing.length) return toast(`Before submitting, add: ${missing.join(", ")}`);
    await enqueueSubmit(project.id, nc.id, undefined); // work comment box removed: never send a value, stored comments untouched
    toast(uploads.some((q) => q.status !== "uploaded") ? `${nc.number} submitted · sends after uploads finish` : `${nc.number} submitted for review`);
    onToggle(nc.id); // back to the NC list, where the new status shows on the card
  };

  return (
    <View style={[s.card, expanded && { borderColor: colors.brandPrimary }, !expanded && (simple === "revisit" || simple === "action") && { borderColor: colors.brandPrimary, borderWidth: 2 }]} testID={`nc-card-${nc.number}`}>
      <Pressable onPress={() => onToggle(nc.id)} style={s.head} testID={`nc-toggle-${nc.number}`}>
        <View style={{ flex: 1, gap: 4 }}>
          <View style={s.headRow}>
            <Feather name={st.icon as any} size={20} color={simple === "approved" ? colors.success : simple === "revisit" ? colors.error : simple === "not_started" ? colors.muted : colors.brandPrimary} testID={`nc-state-icon-${nc.number}-${simple}`} />
            <Text style={s.num}>{nc.number}</Text>
            <Badge status={badge.status} label={badge.label} testID={`nc-status-${nc.number}`} />
          </View>
          <Text style={s.desc}>{nc.description}</Text>
        </View>
        <Feather name={expanded ? "chevron-up" : "chevron-down"} size={22} color={colors.onSurface} />
      </Pressable>
      {nc.safety_critical || (!done && nc.requires_stages) ? (
        <View style={s.flags}>
          <SafetyToggle ncId={nc.id} number={nc.number} value={nc.safety_critical} projectId={project.id} readOnly />
          {!done && nc.requires_stages ? (
            <View style={[s.vidTag, nc.video_required && !has.video && { borderColor: colors.brandPrimary }]} testID={`video-req-${nc.number}`}>
              <Feather name="video" size={14} color={nc.video_required && !has.video ? colors.brandPrimary : colors.muted} />
              <Text style={[s.vidText, nc.video_required && !has.video && { color: colors.brandPrimary }]}>{nc.video_required ? (has.video ? "Video added" : "Video required") : "Video optional"}</Text>
            </View>
          ) : null}
        </View>
      ) : null}

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

          <Text style={s.step} testID={`step1-${nc.number}`}>1  What needs fixing</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.row}>
            {nc.trustmark.length ? nc.trustmark.map((f, i) => <Thumb key={f.id} f={f} size={96} onPress={() => setPv({ files: nc.trustmark, i })} testID={`trustmark-thumb-${f.id}`} />) : <Text style={s.muted}>No TrustMark photos provided</Text>}
          </ScrollView>

          {nc.original_text && nc.original_text !== nc.description ? <Text style={s.work} selectable>{nc.original_text}</Text> : null}
          {!done ? (
            <>
              <Text style={s.step} testID={`step2-${nc.number}`}>2  Add evidence photos</Text>
              <Text style={[s.count, missing.length ? { color: colors.onWarning } : null]} testID={`evidence-count-${nc.number}`}>
                {addedPhotos} photo{addedPhotos === 1 ? "" : "s"}{addedVideos ? ` + ${addedVideos} video${addedVideos === 1 ? "" : "s"}` : ""} added{addedPhotos || addedVideos ? " · not submitted yet" : ""}
                {missing.length ? `\nStill needed: ${missing.join(", ")}` : ""}
              </Text>
            </>
          ) : null}
          {!done ? (
            <View style={s.actions}>
              <Btn title="Take photos" icon="camera" onPress={() => router.push({ pathname: "/camera", params })} style={{ flex: 1.4 }} testID={`take-photos-${nc.number}`} disabled={nc.status === "assessing"} />
              <Btn title="Video" icon="video" variant="outline" onPress={() => router.push({ pathname: "/video", params })} style={{ flex: 1 }} testID={`record-video-${nc.number}`} disabled={nc.status === "assessing"} />
            </View>
          ) : null}
          {!done ? (
            <View style={s.actions}>
              <Btn title="Upload from gallery" icon="image" variant="outline" onPress={pickGallery} style={{ flex: 1 }} testID={`gallery-upload-${nc.number}`} disabled={nc.status === "assessing"} />
            </View>
          ) : null}
          {!done ? (
            <Pressable onPress={() => setOrig((o) => !o)} style={s.origRow} testID={`original-quality-${nc.number}`}>
              <Feather name={orig ? "check-square" : "square"} size={18} color={colors.onSurface} />
              <Text style={s.stageText}>Original quality (larger, slower upload)</Text>
            </Pressable>
          ) : null}

          {drafts.length || uploads.length || draftVideos.length ? (
            <>
              <Label>Added, not yet submitted · tap ✕ to remove a mistake</Label>
              <FlatList
                horizontal
                showsHorizontalScrollIndicator={false}
                contentContainerStyle={s.row}
                data={[...draftVideos.map((f) => ({ k: "v" as const, id: f.id, f })), ...drafts.map((f, i) => ({ k: "p" as const, id: f.id, f, i })), ...uploads.map((q) => ({ k: "q" as const, id: q.id, q }))]}
                keyExtractor={(x) => x.id}
                initialNumToRender={8}
                windowSize={5}
                renderItem={({ item: x }) =>
                  x.k === "v" || x.k === "p" ? (
                    <View>
                      <Thumb f={x.f} onPress={() => (x.k === "v" ? setView(x.f) : setPv({ files: drafts, i: x.i }))} testID={`${x.k === "v" ? "video" : "draft"}-thumb-${x.f.id}`} />
                      {nc.status !== "assessing" ? (
                        <Pressable onPress={() => setRemoving(x.f)} style={s.remove} hitSlop={8} accessibilityLabel="Remove" testID={`remove-draft-${x.f.id}`}>
                          <Feather name="x" size={16} color={colors.onError} />
                        </Pressable>
                      ) : null}
                    </View>
                  ) : (
                    <Pressable style={s.local} onPress={() => (x.q.status === "failed" ? openConfirm(x.q) : undefined)} onLongPress={() => x.q.status !== "uploaded" && x.q.status !== "uploading" && openConfirm(x.q)} testID={`queued-item-${x.q.id}`}>
                      {x.q.mime?.includes("video") ? <Feather name="film" size={22} color={colors.onSurface} /> : <Image source={{ uri: x.q.uri }} style={{ width: "100%", height: "100%" }} contentFit="cover" />}
                      <View style={[s.localTag, x.q.status === "failed" && { backgroundColor: colors.error }, x.q.status === "uploaded" && { backgroundColor: colors.success }]}>
                        <Text style={[s.localText, x.q.status === "failed" && { color: colors.onError }, x.q.status === "uploaded" && { color: colors.onSuccess }]} testID={`queued-status-${x.q.id}`}>{x.q.status === "uploading" && pct[x.q.id] != null ? `${Math.round(pct[x.q.id] * 100)}%` : STATUS_LABEL[x.q.status].toUpperCase()}</Text>
                      </View>
                    </Pressable>
                  )
                }
              />
            </>
          ) : null}

          {submitted.length || submittedVideos.length ? (
            <>
              <Label>Submitted evidence ({submitted.length} photo{submitted.length === 1 ? "" : "s"}{submittedVideos.length ? `, ${submittedVideos.length} video${submittedVideos.length === 1 ? "" : "s"}` : ""})</Label>
              <FlatList
                horizontal
                showsHorizontalScrollIndicator={false}
                contentContainerStyle={s.row}
                data={[...submittedVideos.map((f) => ({ v: true, f, i: 0 })), ...submitted.map((f, i) => ({ v: false, f, i }))]}
                keyExtractor={(x) => x.f.id}
                initialNumToRender={10}
                windowSize={5}
                renderItem={({ item: x }) =>
                  x.v ? (
                    <Thumb f={x.f} size={56} onPress={() => setView(x.f)} testID={`submitted-video-${x.f.id}`} />
                  ) : (
                    <Thumb f={x.f} size={56} onPress={() => setPv({ files: submitted, i: x.i })} testID={`submitted-thumb-${x.f.id}`} />
                  )
                }
              />
            </>
          ) : null}

          {!done ? (
            <>
              <Text style={s.step} testID={`step3-${nc.number}`}>3  Submit NC</Text>
              <Btn title={nc.status === "assessing" ? "Checking evidence…" : submitQueued ? "Submitted" : "Submit NC"} icon="send" variant="dark" onPress={submit} disabled={!canSubmit} style={s.submit} testID={`submit-evidence-${nc.number}`} />
              {!canSubmit && !submitQueued && nc.status !== "assessing" ? <Text style={s.muted}>Add evidence above, then submit.</Text> : null}
            </>
          ) : null}
        </View>
      ) : null}

      <MediaViewer f={view} onClose={() => setView(null)} />
      {pv ? <PhotoViewer files={pv.files} index={pv.i} onClose={() => setPv(null)} /> : null}
      <Sheet visible={!!removing} onClose={() => setRemoving(null)} title={removing?.kind === "video" ? "Remove this video?" : "Remove this photo?"} testID="remove-draft-sheet">
        <Text style={s.work}>It will be taken off {nc.number} and won&apos;t appear in the report. You can add a replacement afterwards.</Text>
        <Btn title="Remove" icon="trash-2" variant="danger" onPress={remove} loading={busy} testID="remove-draft-confirm" />
        <Btn title="Keep it" variant="outline" onPress={() => setRemoving(null)} testID="remove-draft-cancel" />
      </Sheet>
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
  card: { borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, backgroundColor: c.surface },
  head: { flexDirection: "row", alignItems: "center", gap: space.md, padding: space.lg, minHeight: 64 },
  flags: { flexDirection: "row", flexWrap: "wrap", gap: space.sm, paddingHorizontal: space.lg, paddingBottom: space.md, marginTop: -space.sm },
  vidTag: { flexDirection: "row", alignItems: "center", gap: space.xs, minHeight: 36, paddingHorizontal: space.sm, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border },
  vidText: { fontFamily: fonts.text, fontSize: 12, fontWeight: "700", color: c.muted },
  headRow: { flexDirection: "row", alignItems: "center", gap: space.sm, flexWrap: "wrap" },
  num: { fontFamily: fonts.text, fontSize: 14, color: c.onSurface },
  desc: { fontFamily: fonts.display, fontWeight: "700", fontSize: 16, color: c.onSurface },
  body: { borderTopWidth: BORDER, borderColor: c.border, padding: space.lg, gap: space.sm },
  feedback: { backgroundColor: c.brandTertiary, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, padding: space.md, marginBottom: space.sm },
  fbText: { fontFamily: fonts.text, fontSize: 12, color: c.onSurface },
  row: { gap: space.sm, paddingBottom: space.sm, paddingTop: space.sm, paddingRight: space.sm },
  work: { fontSize: 15, lineHeight: 21, color: c.onSurface, marginBottom: space.sm },
  muted: { fontFamily: fonts.text, fontSize: 12, color: c.muted },
  actions: { flexDirection: "row", gap: space.sm, marginTop: space.sm },
  stage: { flex: 1, minHeight: 44, flexDirection: "row", gap: 6, alignItems: "center", justifyContent: "center", borderWidth: BORDER, borderRadius: radius.pill, borderColor: c.border },
  origRow: { flexDirection: "row", alignItems: "center", gap: space.sm, minHeight: 44 },
  stageText: { fontFamily: fonts.text, fontSize: 14, fontWeight: "600", color: c.onSurface },
  local: { width: 72, height: 72, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, borderStyle: "dashed", alignItems: "center", justifyContent: "center", overflow: "hidden", backgroundColor: c.surfaceSecondary },
  localTag: { position: "absolute", bottom: 0, left: 0, right: 0, backgroundColor: c.warning, alignItems: "center" },
  localText: { fontFamily: fonts.text, fontSize: 9, color: c.onWarning },
  step: { fontFamily: fonts.display, fontWeight: "800", fontSize: 17, color: c.onSurface, marginTop: space.sm },
  count: { fontFamily: fonts.text, fontSize: 15, fontWeight: "600", color: c.onSurface },
  submit: { minHeight: 56, marginTop: space.xs },
  remove: { position: "absolute", top: -6, right: -6, width: 28, height: 28, borderRadius: 14, backgroundColor: c.error, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: c.surface },
}));
