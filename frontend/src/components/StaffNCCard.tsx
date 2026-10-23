import { memo, useState } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import * as ImagePicker from "expo-image-picker";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/src/auth";
import { api, authed, BASE, readError } from "@/src/api";
import { uploadForm } from "@/src/utils/upload";
import { AI_STATUS, EvidenceFile, NCItem } from "@/src/projects";
import { Badge, Btn, Feather, Field, Label, useToast } from "@/src/ui";
import { MediaViewer, Thumb } from "@/src/components/Media";
import { PhotoViewer } from "@/src/components/PhotoViewer";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

/** "Remedial works carried out" text on the NC PDF: AI drafts it from recorded information; staff edit before export. */
function ExportNote({ nc, projectId }: { nc: NCItem; projectId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [val, setVal] = useState(nc.export_note ?? "");
  const [busy, setBusy] = useState(false);
  const dirty = val.trim() !== (nc.export_note ?? "");
  const save = async () => {
    setBusy(true);
    try {
      await api(`/ncs/${nc.id}/export-note`, { method: "PUT", body: { note: val } });
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      toast("Works description saved");
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <Label>Remedial works carried out (PDF){nc.export_note_source === "ai" ? " · AI draft, please check" : ""}</Label>
      {nc.installer_note ? <Text style={{ fontSize: 13 }} testID={`installer-note-${nc.number}`}>Installer note: {nc.installer_note}</Text> : null}
      <Field placeholder="20-40 words: what was corrected and how" value={val} onChangeText={setVal} multiline maxLength={600} testID={`export-note-${nc.number}`} />
      {dirty ? <Btn title="Save description" icon="save" variant="outline" small onPress={save} loading={busy} testID={`export-note-save-${nc.number}`} /> : null}
    </>
  );
}

export const StaffNCCard = memo(function StaffNCCard({ nc, projectId, expanded, onToggle, onDone }: { nc: NCItem; projectId: string; expanded: boolean; onToggle: (id: string) => void; onDone?: () => void }) {
  const { user } = useAuth();
  const installers = useQuery({ queryKey: ["installers"], queryFn: () => api<{ id: string; name: string }[]>("/installers"), staleTime: 300000, enabled: expanded });
  const s = useStyles();
  const { colors } = useTheme();
  const qc = useQueryClient();
  const toast = useToast();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [view, setView] = useState<EvidenceFile | null>(null);
  const [pv, setPv] = useState<{ files: EvidenceFile[]; i: number } | null>(null);
  const [showHidden, setShowHidden] = useState(false);
  const hiddenRefs = nc.trustmark_hidden ?? [];

  const setHidden = async (fid: string, excluded: boolean) => {
    try {
      await api(`/files/${fid}/exclude`, { method: "POST", body: { excluded } });
      refresh();
      toast(excluded ? "Reference photo hidden" : "Reference photo restored");
    } catch (e: any) {
      toast(e.message);
    }
  };
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["project", projectId] });
    qc.invalidateQueries({ queryKey: ["review-queue"] });
    qc.invalidateQueries({ queryKey: ["review-groups"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
    qc.invalidateQueries({ queryKey: ["projects"] });
  };

  // PDF photo selection override: automatic -> always include -> never include -> automatic
  const togglePdf = async (f: EvidenceFile) => {
    const next = f.pdf_include == null ? true : f.pdf_include ? false : null;
    try {
      await api(`/evidence/${f.id}/pdf-include`, { method: "PUT", body: { include: next } });
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    } catch (e: any) {
      toast(e.message);
    }
  };

  const act = async (action: string) => {
    if (busy) return; // no duplicate submissions while a decision is in flight
    setBusy(action);
    try {
      await api(`/ncs/${nc.id}/review`, { method: "POST", body: { action, note } });
      setNote("");
      // reflect the confirmed backend state immediately, then refetch the truth
      const next = ({ approve: "approved", reject: "rejected", more_evidence: "more_evidence", trustmark_reject: "rejected" } as Record<string, string>)[action];
      if (next) {
        qc.setQueryData(["project", projectId], (old: any) =>
          old?.ncs ? { ...old, ncs: old.ncs.map((x: NCItem) => (x.id === nc.id ? { ...x, status: next, approved_by: action === "approve" ? "employee" : x.approved_by } : x)) } : old,
        );
      }
      refresh();
      toast(`${nc.number}: ${({ approve: "approved", reject: "rejected", more_evidence: "more evidence requested", escalate: "escalated to Admin", return: "returned to Employee review", trustmark_reject: "reopened (TrustMark rejection)" } as Record<string, string>)[action]}`);
      onDone?.();
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(null);
    }
  };

  const reassign = async (iid: string) => {
    try {
      await api(`/ncs/${nc.id}/assign`, { method: "POST", body: { installer_id: iid } });
      refresh();
      toast(`${nc.number} assigned · installer notified`);
    } catch (e: any) {
      toast(e.message);
    }
  };
  const lockedForEmployee = nc.status === "admin_review" && user?.role !== "admin";

  const upload = async (kind: "trustmark" | "evidence") => {
    const r = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ["images"], quality: 1, allowsMultipleSelection: true, selectionLimit: 6 });
    if (r.canceled) return;
    setBusy(kind);
    try {
      for (const a of r.assets) {
        const res = await authed((t) =>
          uploadForm(`${BASE}/api/ncs/${nc.id}/${kind}`, {
            headers: t ? { Authorization: `Bearer ${t}` } : {},
            fields: kind === "evidence" ? { meta: JSON.stringify({ kind: "photo", source: "employee_upload" }) } : {},
            files: { file: { uri: a.uri, name: a.fileName ?? "photo.jpg", mimeType: a.mimeType ?? "image/jpeg" } },
          }),
        );
        if (!res.ok) throw new Error(await readError(res));
      }
      toast(`${r.assets.length} file(s) uploaded`);
      refresh();
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <View style={s.card} testID={`staff-nc-${nc.number}`}>
      <Pressable style={s.head} onPress={() => onToggle(nc.id)} testID={`staff-nc-toggle-${nc.number}`}>
        <View style={{ flex: 1, gap: 4 }}>
          <View style={s.headRow}>
            <Text style={s.num}>{nc.number}</Text>
            <Badge status={nc.status} testID={`staff-nc-status-${nc.number}`} />
            {nc.safety_critical ? <Badge status="rejected" label="Safety-critical" /> : null}
          </View>
          <Text style={s.desc}>{nc.description}</Text>
          <Text style={s.assignee} testID={`staff-nc-installer-${nc.number}`}>{nc.assigned_installer_name ?? "Unassigned"}{nc.approved_by === "ai" ? " · AI approved" : ""}{nc.reopen_history?.length ? ` · Reopened ${nc.reopen_history.length}× by TrustMark` : ""}</Text>
        </View>
        <Feather name={expanded ? "chevron-up" : "chevron-down"} size={20} color={colors.onSurface} />
      </Pressable>
      {expanded ? (
        <View style={s.body}>
          {nc.original_text && nc.original_text !== nc.description ? (
            <>
              <Label>Original TrustMark wording</Label>
              <Text style={s.body1} selectable testID={`staff-nc-original-${nc.number}`}>{nc.original_text}</Text>
            </>
          ) : null}
          <Label>Required work</Label>
          <Text style={s.body1} selectable>{nc.required_work}</Text>

          {nc.ai ? (
            <View style={s.ai} testID={`staff-nc-ai-${nc.number}`}>
              <Text style={s.aiHead} testID={`staff-nc-ai-status-${nc.number}`}>{AI_STATUS[nc.ai.status ?? ""] ?? `AI: ${nc.ai.outcome.replace(/_/g, " ")}`}{nc.ai.confidence != null && nc.ai.status !== "unavailable" ? ` · ${Math.round(nc.ai.confidence * 100)}%` : ""}</Text>
              <Text style={s.mono}>{nc.ai.feedback}</Text>
              {nc.ai.concerns.map((c, i) => <Text key={i} style={s.concern}>⚑ {c}</Text>)}
              {nc.ai.requests?.length ? <Text style={s.mono}>Requested from installer: {nc.ai.requests.join(" ")}</Text> : null}
              <Text style={s.muted}>{nc.ai.model}</Text>
            </View>
          ) : null}

          <Label>TrustMark reference ({nc.trustmark.length})</Label>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.row}>
            {nc.trustmark.map((f, i) => <Thumb key={f.id} f={f} onPress={() => setPv({ files: nc.trustmark, i })} onLongPress={() => setHidden(f.id, true)} testID={`staff-trustmark-${f.id}`} />)}
            <Pressable style={s.add} onPress={() => upload("trustmark")} testID={`add-trustmark-${nc.number}`}>
              <Feather name="plus" size={20} color={colors.onSurface} />
            </Pressable>
          </ScrollView>
          {hiddenRefs.length ? (
            <Pressable onPress={() => setShowHidden((v) => !v)} style={s.hiddenToggle} testID={`toggle-hidden-${nc.number}`}>
              <Feather name={showHidden ? "eye-off" : "eye"} size={14} color={colors.muted} />
              <Text style={s.muted}>{hiddenRefs.length} logo/decoration image{hiddenRefs.length === 1 ? "" : "s"} hidden from the report · {showHidden ? "Hide" : "Show"}</Text>
            </Pressable>
          ) : null}
          {showHidden ? (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.row}>
              {hiddenRefs.map((f) => (
                <View key={f.id} style={{ gap: 4, opacity: 0.85 }}>
                  <Thumb f={f} size={72} testID={`hidden-trustmark-${f.id}`} />
                  <Pressable onPress={() => setHidden(f.id, false)} style={s.restore} testID={`restore-trustmark-${f.id}`}>
                    <Text style={s.tag}>RESTORE</Text>
                  </Pressable>
                </View>
              ))}
            </ScrollView>
          ) : null}
          <Text style={s.muted}>Long-press a reference photo to hide it. Hidden files stay in storage.</Text>

          {nc.videos?.length ? <Label>Videos ({nc.videos.length})</Label> : null}
          {nc.videos?.length ? (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.row}>
              {nc.videos.map((v) => (
                <View key={v.id} style={{ gap: 2 }}>
                  <Thumb f={v} size={88} onPress={() => setView(v)} testID={`staff-video-${v.id}`} />
                  <Text style={s.tag}>{v.source === "in_app_camera" ? "APP" : v.source === "gallery" ? "GALLERY" : "STAFF"}{v.status === "draft" ? "·DRAFT" : ""}</Text>
                </View>
              ))}
            </ScrollView>
          ) : null}
          <Label>Evidence ({nc.photos.length} photo{nc.photos.length === 1 ? "" : "s"})</Label>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.row}>
            {nc.photos.map((f, i) => (
              <View key={f.id} style={{ gap: 2 }}>
                <Thumb f={f} size={88} onPress={() => setPv({ files: nc.photos, i })} testID={`staff-photo-${f.id}`} />
                <Text style={s.tag}>{f.stage ? `${f.stage.toUpperCase()}·` : ""}{f.source === "in_app_camera" ? "APP" : f.source === "gallery" ? "GALLERY" : "STAFF"}{f.status === "draft" ? "·DRAFT" : ""}</Text>
                <Pressable onPress={() => togglePdf(f)} style={[s.restore, f.pdf_include === true && { backgroundColor: colors.success }, f.pdf_include === false && { backgroundColor: colors.error }]} testID={`pdf-include-${f.id}`}>
                  <Text style={[s.tag, f.pdf_include === true && { color: colors.onSuccess }, f.pdf_include === false && { color: colors.onError }]}>{f.pdf_include === true ? "IN PDF" : f.pdf_include === false ? "NOT IN PDF" : "PDF: AUTO"}</Text>
                </Pressable>
              </View>
            ))}
            <Pressable style={[s.add, { width: 88, height: 88 }]} onPress={() => upload("evidence")} testID={`add-evidence-${nc.number}`}>
              <Feather name="upload" size={20} color={colors.onSurface} />
            </Pressable>
          </ScrollView>

          <ExportNote nc={nc} projectId={projectId} />

          <Label>Assigned installer</Label>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.row}>
            {(installers.data ?? []).map((i) => (
              <Pressable key={i.id} onPress={() => reassign(i.id)} style={[s.chip, nc.assigned_installer_id === i.id && { backgroundColor: colors.brandSecondary }]} testID={`reassign-${nc.number}-${i.id}`}>
                <Text style={[s.chipText, nc.assigned_installer_id === i.id && { color: colors.onBrandSecondary }]}>{i.name}</Text>
              </Pressable>
            ))}
          </ScrollView>

          {lockedForEmployee ? (
            <Text style={s.body1} testID={`admin-locked-${nc.number}`}>Escalated · awaiting Admin decision.</Text>
          ) : (
            <>
              <Field placeholder="Note to installer (optional)" value={note} onChangeText={setNote} testID={`review-note-${nc.number}`} />
              {nc.status === "approved" ? (
                <View style={s.approvedBox} testID={`approved-state-${nc.number}`}>
                  <Feather name="check-circle" size={18} color={colors.onSuccess} />
                  <Text style={s.approvedText}>Approved{nc.approved_by === "ai" ? " (AI)" : ""}</Text>
                </View>
              ) : (
                <Btn title="Approve" icon="check" variant="success" onPress={() => act("approve")} loading={busy === "approve"} disabled={!!busy} testID={`approve-${nc.number}`} />
              )}
              <View style={s.actions}>
                <Btn title="More evidence" variant="outline" small onPress={() => act("more_evidence")} loading={busy === "more_evidence"} disabled={!!busy} style={{ flex: 1 }} testID={`more-evidence-${nc.number}`} />
                <Btn title="Reject" variant="danger" small onPress={() => act("reject")} loading={busy === "reject"} disabled={!!busy} style={{ flex: 1 }} testID={`reject-${nc.number}`} />
              </View>
              {nc.status === "approved" ? (
                <Btn title="TrustMark rejected" icon="x-octagon" variant="outline" small onPress={() => act("trustmark_reject")} loading={busy === "trustmark_reject"} testID={`trustmark-reject-${nc.number}`} />
              ) : null}
              {user?.role === "employee" && nc.status === "employee_review" ? (
                <Btn title="Escalate to Admin" icon="arrow-up-circle" variant="dark" small onPress={() => act("escalate")} loading={busy === "escalate"} testID={`escalate-${nc.number}`} />
              ) : null}
              {user?.role === "admin" && nc.status === "admin_review" ? (
                <Btn title="Return to Employee" icon="corner-up-left" variant="outline" small onPress={() => act("return")} loading={busy === "return"} testID={`return-${nc.number}`} />
              ) : null}
            </>
          )}
        </View>
      ) : null}
      <MediaViewer f={view} onClose={() => setView(null)} />
      {pv ? <PhotoViewer files={pv.files} index={pv.i} onClose={() => setPv(null)} showInfo /> : null}
    </View>
  );
});

const useStyles = makeStyles((c) => ({
  card: { borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border },
  approvedBox: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: space.sm, minHeight: 48, backgroundColor: c.success, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border },
  approvedText: { fontFamily: fonts.display, fontWeight: "800", fontSize: 15, color: c.onSuccess, textTransform: "uppercase" },
  head: { flexDirection: "row", alignItems: "center", gap: space.md, padding: space.md, minHeight: 56 },
  headRow: { flexDirection: "row", gap: space.sm, alignItems: "center", flexWrap: "wrap" },
  num: { fontFamily: fonts.text, fontSize: 13, color: c.onSurface },
  desc: { fontFamily: fonts.display, fontWeight: "700", fontSize: 15, color: c.onSurface },
  body: { borderTopWidth: BORDER, borderColor: c.border, padding: space.md, gap: space.sm },
  mono: { fontFamily: fonts.text, fontSize: 12, color: c.onSurface },
  body1: { fontSize: 15, lineHeight: 21, color: c.onSurface },
  assignee: { fontSize: 13, color: c.muted },
  muted: { fontFamily: fonts.text, fontSize: 11, color: c.muted },
  ai: { backgroundColor: c.brandTertiary, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, padding: space.md, gap: 4 },
  aiHead: { fontFamily: fonts.text, fontSize: 12, color: c.onBrandTertiary },
  concern: { fontFamily: fonts.text, fontSize: 11, color: c.onSurface },
  row: { gap: space.sm, paddingBottom: space.xs },
  add: { width: 72, height: 72, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, borderStyle: "dashed", alignItems: "center", justifyContent: "center" },
  tag: { fontFamily: fonts.text, fontSize: 9, color: c.muted },
  hiddenToggle: { flexDirection: "row", alignItems: "center", gap: space.xs, minHeight: 44 },
  restore: { borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, alignItems: "center", justifyContent: "center", minHeight: 32 },
  actions: { flexDirection: "row", gap: space.sm },
  chip: { height: 36, flexShrink: 0, paddingHorizontal: space.md, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, justifyContent: "center" },
  chipText: { fontSize: 13, fontWeight: "600", color: c.onSurface },
}));
