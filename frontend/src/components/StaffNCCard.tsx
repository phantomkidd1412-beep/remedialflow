import { memo, useState } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import * as ImagePicker from "expo-image-picker";
import { useQueryClient } from "@tanstack/react-query";
import { api, authed, BASE, readError } from "@/src/api";
import { uploadForm } from "@/src/utils/upload";
import { EvidenceFile, NCItem } from "@/src/projects";
import { Badge, Btn, Feather, Field, Label, useToast } from "@/src/ui";
import { MediaViewer, Thumb } from "@/src/components/Media";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

export const StaffNCCard = memo(function StaffNCCard({ nc, projectId, expanded, onToggle }: { nc: NCItem; projectId: string; expanded: boolean; onToggle: (id: string) => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const qc = useQueryClient();
  const toast = useToast();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [view, setView] = useState<EvidenceFile | null>(null);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["project", projectId] });
    qc.invalidateQueries({ queryKey: ["review-queue"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
    qc.invalidateQueries({ queryKey: ["projects"] });
  };

  const act = async (action: string) => {
    setBusy(action);
    try {
      await api(`/ncs/${nc.id}/review`, { method: "POST", body: { action, note } });
      setNote("");
      refresh();
      toast(`${nc.number}: ${action === "approve" ? "approved" : action === "reject" ? "rejected" : "more evidence requested"}`);
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(null);
    }
  };

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
          <Text style={s.assignee} testID={`staff-nc-installer-${nc.number}`}>{nc.assigned_installer_name ?? "Unassigned"}{nc.approved_by === "ai" ? " · AI approved" : ""}</Text>
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
              <Text style={s.aiHead}>AI: {nc.ai.outcome.replace(/_/g, " ").toUpperCase()} · {Math.round(nc.ai.confidence * 100)}%</Text>
              <Text style={s.mono}>{nc.ai.feedback}</Text>
              {nc.ai.concerns.map((c, i) => <Text key={i} style={s.concern}>⚑ {c}</Text>)}
              <Text style={s.muted}>{nc.ai.model}</Text>
            </View>
          ) : null}

          <Label>TrustMark reference ({nc.trustmark.length})</Label>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.row}>
            {nc.trustmark.map((f) => <Thumb key={f.id} f={f} onPress={() => setView(f)} testID={`staff-trustmark-${f.id}`} />)}
            <Pressable style={s.add} onPress={() => upload("trustmark")} testID={`add-trustmark-${nc.number}`}>
              <Feather name="plus" size={20} color={colors.onSurface} />
            </Pressable>
          </ScrollView>

          <Label>Evidence ({nc.photos.length} photo{nc.photos.length === 1 ? "" : "s"}{nc.video ? " + video" : ""})</Label>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.row}>
            {nc.video ? <Thumb f={nc.video} size={88} onPress={() => setView(nc.video)} testID={`staff-video-${nc.number}`} /> : null}
            {nc.photos.map((f) => (
              <View key={f.id} style={{ gap: 2 }}>
                <Thumb f={f} size={88} onPress={() => setView(f)} testID={`staff-photo-${f.id}`} />
                <Text style={s.tag}>{f.source === "in_app_camera" ? "APP" : f.source === "gallery" ? "GALLERY" : "STAFF"}{f.status === "draft" ? "·DRAFT" : ""}</Text>
              </View>
            ))}
            <Pressable style={[s.add, { width: 88, height: 88 }]} onPress={() => upload("evidence")} testID={`add-evidence-${nc.number}`}>
              <Feather name="upload" size={20} color={colors.onSurface} />
            </Pressable>
          </ScrollView>
          {nc.video_versions > 1 ? <Text style={s.muted}>{nc.video_versions - 1} superseded video version(s) retained</Text> : null}

          <Field placeholder="Note to installer (optional)" value={note} onChangeText={setNote} testID={`review-note-${nc.number}`} />
          <Btn title="Approve" icon="check" variant="success" onPress={() => act("approve")} loading={busy === "approve"} testID={`approve-${nc.number}`} />
          <View style={s.actions}>
            <Btn title="More evidence" variant="outline" small onPress={() => act("more_evidence")} loading={busy === "more_evidence"} style={{ flex: 1 }} testID={`more-evidence-${nc.number}`} />
            <Btn title="Reject" variant="danger" small onPress={() => act("reject")} loading={busy === "reject"} style={{ flex: 1 }} testID={`reject-${nc.number}`} />
          </View>
        </View>
      ) : null}
      <MediaViewer f={view} onClose={() => setView(null)} />
    </View>
  );
});

const useStyles = makeStyles((c) => ({
  card: { borderWidth: BORDER, borderColor: c.border },
  head: { flexDirection: "row", alignItems: "center", gap: space.md, padding: space.md, minHeight: 56 },
  headRow: { flexDirection: "row", gap: space.sm, alignItems: "center", flexWrap: "wrap" },
  num: { fontFamily: fonts.mono, fontSize: 13, color: c.onSurface },
  desc: { fontFamily: fonts.display, fontWeight: "700", fontSize: 15, color: c.onSurface },
  body: { borderTopWidth: BORDER, borderColor: c.border, padding: space.md, gap: space.sm },
  mono: { fontFamily: fonts.mono, fontSize: 12, color: c.onSurface },
  body1: { fontSize: 15, lineHeight: 21, color: c.onSurface },
  assignee: { fontSize: 13, color: c.muted },
  muted: { fontFamily: fonts.mono, fontSize: 11, color: c.muted },
  ai: { backgroundColor: c.brandTertiary, borderWidth: BORDER, borderColor: c.border, padding: space.md, gap: 4 },
  aiHead: { fontFamily: fonts.mono, fontSize: 12, color: c.onBrandTertiary },
  concern: { fontFamily: fonts.mono, fontSize: 11, color: c.onSurface },
  row: { gap: space.sm, paddingBottom: space.xs },
  add: { width: 72, height: 72, borderWidth: BORDER, borderColor: c.border, borderStyle: "dashed", alignItems: "center", justifyContent: "center" },
  tag: { fontFamily: fonts.mono, fontSize: 9, color: c.muted },
  actions: { flexDirection: "row", gap: space.sm },
}));
