import { useCallback, useState } from "react";
import { FlatList, Pressable, RefreshControl, ScrollView, Text, View } from "react-native";
import { useLocalSearchParams } from "expo-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { openExport, openReport, useProject } from "@/src/projects";
import { Badge, Btn, ErrorBlock, Feather, Field, Label, Loading, Progress, Sheet, fmtDate, useToast } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { StaffNCCard } from "@/src/components/StaffNCCard";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

export default function StaffProject() {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const toast = useToast();
  const qc = useQueryClient();
  const { id, nc } = useLocalSearchParams<{ id: string; nc?: string }>();
  const q = useProject(id);
  const installers = useQuery({ queryKey: ["installers"], queryFn: () => api<{ id: string; name: string }[]>("/installers"), staleTime: 300000 });
  const [open, setOpen] = useState<string | null>(nc ?? null);
  const [addOpen, setAddOpen] = useState(false);
  const [auditOpen, setAuditOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [desc, setDesc] = useState("");
  const [work, setWork] = useState("");
  const [safety, setSafety] = useState(false);
  const toggle = useCallback((nid: string) => setOpen((o) => (o === nid ? null : nid)), []);
  const audit = useQuery({ queryKey: ["audit", id], queryFn: () => api<any[]>(`/projects/${id}/audit`), enabled: auditOpen });

  if (q.isLoading) return <Loading />;
  if (q.isError || !q.data)
    return (
      <View style={s.root}>
        <ScreenHeader title="PROJECT" back />
        <View style={{ padding: space.lg }}><ErrorBlock message="Failed to load project" onRetry={() => q.refetch()} /></View>
      </View>
    );
  const { project: p, ncs } = q.data;

  const assign = async (iid: string) => {
    await api(`/projects/${id}`, { method: "PATCH", body: { installer_id: iid } });
    qc.invalidateQueries({ queryKey: ["project", id] });
    qc.invalidateQueries({ queryKey: ["projects"] });
  };
  const addNc = async () => {
    try {
      await api(`/projects/${id}/ncs`, { method: "POST", body: { description: desc, required_work: work, safety_critical: safety } });
      setDesc(""); setWork(""); setSafety(false); setAddOpen(false);
      qc.invalidateQueries({ queryKey: ["project", id] });
    } catch (e: any) {
      toast(e.message);
    }
  };

  const resolve = async () => {
    try {
      await api(`/projects/${id}/resolve`, { method: "POST" });
      setMenuOpen(false);
      qc.invalidateQueries({ queryKey: ["project", id] });
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      toast("Marked TrustMark Resolved");
    } catch (e: any) {
      toast(e.message);
    }
  };
  const menu = (fn: () => void) => () => {
    setMenuOpen(false);
    fn();
  };

  return (
    <View style={s.root}>
      <ScreenHeader title={p.ref} back right={
        <Pressable onPress={() => setMenuOpen(true)} style={s.menuBtn} testID="project-actions-button" hitSlop={6}>
          <Feather name="more-horizontal" size={22} color={colors.onSurface} />
        </Pressable>
      }>
        <Text style={s.addr} testID="project-address">{p.address}{p.postcode ? `, ${p.postcode}` : ""}</Text>
        <View style={s.prog}>
          <View style={{ flex: 1 }}><Progress value={p.nc_total ? p.nc_done / p.nc_total : 0} /></View>
          <Text style={s.progText} testID="project-progress-text">{p.nc_done}/{p.nc_total} NCs approved</Text>
        </View>
        <View style={{ flexDirection: "row", gap: 4, flexWrap: "wrap" }}>
          <Badge status={p.status} testID="project-status" />
          {p.needs_review ? <Badge status="employee_review" label="Needs review" /> : null}
          {p.resolved ? <Text style={s.muted}>Resolved {fmtDate(p.resolved.at)} by {p.resolved.by_name}</Text> : null}
        </View>
      </ScreenHeader>
      <FlatList
        data={ncs}
        keyExtractor={(n) => n.id}
        refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => q.refetch()} />}
        contentContainerStyle={[s.list, { paddingBottom: insets.bottom + space.xl }]}
        ListHeaderComponent={
          <View style={{ gap: space.sm, marginBottom: space.sm }}>
            <Label>Assign project (unassigned NCs default to this installer)</Label>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.chips}>
              {(installers.data ?? []).map((i) => (
                <Pressable key={i.id} onPress={() => assign(i.id)} style={[s.chip, p.installer_id === i.id && { backgroundColor: colors.brandSecondary }]} testID={`assign-installer-${i.id}`}>
                  <Text style={[s.chipText, p.installer_id === i.id && { color: colors.onBrandSecondary }]}>{i.name}</Text>
                </Pressable>
              ))}
            </ScrollView>
          </View>
        }
        renderItem={({ item }) => <StaffNCCard nc={item} projectId={id} expanded={open === item.id} onToggle={toggle} />}
      />

      <Sheet visible={menuOpen} onClose={() => setMenuOpen(false)} title="Project actions" testID="project-actions-sheet">
        {p.source_report ? <Btn title="View TrustMark PDF" icon="file-text" variant="outline" onPress={menu(() => openReport(id).then(() => toast("Inspection PDF downloaded")).catch((e) => toast(e.message)))} testID="open-report-button" /> : null}
        <Btn title="Export evidence" icon="download" variant="outline" onPress={menu(() => openExport(id).catch((e) => toast(e.message)))} testID="export-project-button" />
        <Btn title="Audit history" icon="list" variant="outline" onPress={menu(() => setAuditOpen(true))} testID="audit-trail-button" />
        <Btn title="Add NC" icon="plus" variant="outline" onPress={menu(() => setAddOpen(true))} testID="add-nc-button" />
        {p.status === "completed" ? <Btn title="Mark TrustMark Resolved" icon="award" variant="success" onPress={resolve} testID="resolve-project-button" /> : null}
      </Sheet>

      <Sheet visible={addOpen} onClose={() => setAddOpen(false)} title="Add NC" testID="add-nc-sheet">
        <Field label="Description" value={desc} onChangeText={setDesc} multiline testID="nc-description-input" />
        <Field label="Required remedial work" value={work} onChangeText={setWork} multiline testID="nc-work-input" />
        <Pressable onPress={() => setSafety(!safety)} style={s.check} testID="nc-safety-toggle">
          <View style={[s.box, safety && { backgroundColor: colors.error }]} />
          <Text style={s.mono}>Safety-critical (always employee review)</Text>
        </Pressable>
        <Btn title="Add NC" onPress={addNc} disabled={!desc || !work} testID="nc-add-submit" />
      </Sheet>

      <Sheet visible={auditOpen} onClose={() => setAuditOpen(false)} title="Audit trail" testID="audit-sheet">
        <ScrollView style={{ maxHeight: 420 }}>
          {audit.isLoading ? <Text style={s.mono}>Loading…</Text> : null}
          {(audit.data ?? []).map((a) => (
            <View key={a.seq} style={s.auditRow}>
              <Text style={s.mono}>#{a.seq} {a.event}</Text>
              <Text style={s.muted}>{fmtDate(a.at)} · {String(a.hash).slice(0, 12)}…</Text>
            </View>
          ))}
        </ScrollView>
      </Sheet>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  prog: { flexDirection: "row", alignItems: "center", gap: space.md },
  addr: { fontFamily: fonts.display, fontWeight: "800", fontSize: 17, lineHeight: 22, color: c.onSurface },
  progText: { fontSize: 13, fontWeight: "600", color: c.onSurface },
  menuBtn: { width: 44, height: 44, borderWidth: BORDER, borderColor: c.border, alignItems: "center", justifyContent: "center" },
  mono: { fontFamily: fonts.mono, fontSize: 12, color: c.onSurface },
  muted: { fontSize: 12, color: c.muted, alignSelf: "center" },
  list: { padding: space.lg, gap: space.md },
  chips: { gap: space.sm },
  chip: { height: 36, flexShrink: 0, paddingHorizontal: space.md, borderWidth: BORDER, borderColor: c.border, justifyContent: "center" },
  chipText: { fontFamily: fonts.mono, fontSize: 12, color: c.onSurface },
  check: { flexDirection: "row", gap: space.sm, alignItems: "center", minHeight: 44 },
  box: { width: 22, height: 22, borderWidth: BORDER, borderColor: c.border },
  auditRow: { borderBottomWidth: 1, borderColor: c.border, paddingVertical: space.sm },
}));
