import { useCallback, useState } from "react";
import { FlatList, Keyboard, Pressable, RefreshControl, ScrollView, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { RateRow, openExport, openReport, useProject } from "@/src/projects";
import { useAuth } from "@/src/auth";
import { Badge, Btn, ErrorBlock, Feather, Field, Label, Loading, Progress, Sheet, fmtDate, money, useToast } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { StaffNCCard } from "@/src/components/StaffNCCard";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

type SchedRow = { installer_id: string; name: string; date: string | null };

/** Optional scheduled visit date per installer (YYYY-MM-DD). Blank = not scheduled; nothing is invented. */
function ScheduleRows({ pid, rows }: { pid: string; rows: SchedRow[] }) {
  const s = useStyles();
  const qc = useQueryClient();
  const toast = useToast();
  const [vals, setVals] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const save = async (r: SchedRow) => {
    const raw = (vals[r.installer_id] ?? r.date ?? "").trim();
    const m = raw.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    const date = raw === "" ? null : m ? `${m[3]}-${m[2].padStart(2, "0")}-${m[1].padStart(2, "0")}` : raw;
    if (date && !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
      toast("Use DD/MM/YYYY");
      return;
    }
    setBusy(r.installer_id);
    try {
      await api(`/projects/${pid}/schedule/${r.installer_id}`, { method: "PUT", body: { date } });
      qc.invalidateQueries({ queryKey: ["project", pid] });
      setVals((v) => {
        const { [r.installer_id]: _, ...rest } = v;
        return rest;
      });
      toast(date ? `${r.name} scheduled` : `${r.name} schedule cleared`);
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(null);
    }
  };
  const show = (d: string | null) => (d ? d.split("-").reverse().join("/") : "");
  return (
    <View style={s.rates} testID="project-schedules">
      <Label>Scheduled visits</Label>
      {rows.map((r) => {
        const v = vals[r.installer_id] ?? show(r.date);
        return (
          <View key={r.installer_id} style={s.rateRow}>
            <Text style={[s.rateName, { flex: 1 }]}>{r.name}</Text>
            <View style={{ width: 130 }}>
              <Field placeholder="DD/MM/YYYY" value={v} onChangeText={(t) => setVals((x) => ({ ...x, [r.installer_id]: t }))} keyboardType="numbers-and-punctuation" testID={`schedule-input-${r.installer_id}`} />
            </View>
            {vals[r.installer_id] !== undefined && vals[r.installer_id] !== show(r.date) ? (
              <Btn title="Save" small onPress={() => save(r)} loading={busy === r.installer_id} testID={`schedule-save-${r.installer_id}`} />
            ) : null}
          </View>
        );
      })}
    </View>
  );
}

export default function StaffProject() {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const toast = useToast();
  const qc = useQueryClient();
  const router = useRouter();
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
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [rateFor, setRateFor] = useState<RateRow | null>(null);
  const [rateText, setRateText] = useState("");
  const [reason, setReason] = useState("");
  const [savingRate, setSavingRate] = useState(false);
  const [rateErr, setRateErr] = useState("");
  const [delOpen, setDelOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [delErr, setDelErr] = useState("");
  const doDelete = async () => {
    setDeleting(true);
    setDelErr("");
    try {
      await api(`/projects/${id}`, { method: "DELETE" });
      setDelOpen(false);
      ["projects", "dashboard", "review-groups", "tracker"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      qc.removeQueries({ queryKey: ["project", id] });
      toast("Project deleted");
      if (router.canGoBack()) router.back();
      else router.replace("/");
    } catch (e: any) {
      setDelErr(e?.message || "Could not delete the project");
    } finally {
      setDeleting(false);
    }
  };
  const closeRate = () => {
    if (savingRate) return;
    Keyboard.dismiss();
    setRateErr("");
    setRateFor(null);
  };

  const saveRate = async () => {
    if (!rateFor) return;
    const t = rateText.trim().replace(/^£/, "");
    const value = Number(t);
    if (!/^\d+(\.\d{1,2})?$/.test(t) || !isFinite(value)) {
      setRateErr("Enter a valid amount, e.g. 150 or 150.50");
      return;
    }
    if (value > 100000) {
      setRateErr("Amount is too large");
      return;
    }
    Keyboard.dismiss();
    setSavingRate(true);
    setRateErr("");
    try {
      const r = await api<{ earning_updated: boolean }>(`/projects/${id}/rates/${rateFor.installer_id}`, { method: "PUT", body: { rate: value, reason: reason.trim() || null } });
      setRateFor(null);
      qc.invalidateQueries({ queryKey: ["project", id] });
      qc.invalidateQueries({ queryKey: ["admin-payments"] });
      toast(r.earning_updated ? "Rate saved · unpaid amount updated" : "Rate saved");
    } catch (e: any) {
      setRateErr(e?.message || "Could not save the rate. Please try again.");
    } finally {
      setSavingRate(false);
    }
  };

  if (q.isLoading) return <Loading />;
  if (q.isError || !q.data)
    return (
      <View style={s.root}>
        <ScreenHeader title="PROJECT" back />
        <View style={{ padding: space.lg }}><ErrorBlock message={q.fetchStatus === "paused" ? "Not available offline. Open this project once while connected." : (q.error as Error)?.message || "Failed to load project"} onRetry={() => q.refetch()} /></View>
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
          <Text style={s.progText} testID="project-progress-text">Project Progress: {p.nc_done} of {p.nc_total} NCs Completed</Text>
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
            {isAdmin && p.rate_rows?.length ? (
              <View style={s.rates} testID="project-rates">
                <Label>Installer rates</Label>
                {p.rate_rows.map((r) => (
                  <View key={r.installer_id} style={s.rateRow} testID={`rate-row-${r.installer_id}`}>
                    <View style={{ flex: 1 }}>
                      <Text style={s.rateName}>{r.name}</Text>
                      <Text style={s.rateMeta} testID={`rate-value-${r.installer_id}`}>
                        {r.pay_type === "daily" ? "Daily wage · paid outside the app" : `${r.rate != null ? money(r.rate) : "Rate not set"} per job${r.earning ? ` · ${r.earning.status === "paid" ? "Paid" : "Earned, unpaid"} ${money(r.earning.amount)}` : " · Not yet earned"}`}
                      </Text>
                    </View>
                    {isAdmin && r.pay_type !== "daily" && r.earning?.status !== "paid" ? (
                      <Btn title="Edit" small variant="outline" onPress={() => { setRateFor(r); setRateText(r.rate != null ? String(r.rate) : ""); setReason(""); }} testID={`rate-edit-${r.installer_id}`} />
                    ) : null}
                  </View>
                ))}
              </View>
            ) : null}
            {p.schedule_rows?.length ? <ScheduleRows pid={id} rows={p.schedule_rows} /> : null}
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
        <Btn title="Delete project" icon="trash-2" variant="danger" onPress={menu(() => { setDelErr(""); setDelOpen(true); })} testID="delete-project-button" />
      </Sheet>

      <Sheet visible={delOpen} onClose={() => !deleting && setDelOpen(false)} title="Delete this project?" testID="delete-sheet" dismissOnBackdrop={false}>
        <Text style={s.rateName} testID="delete-ref">{p.ref}</Text>
        <Text style={s.hint} testID="delete-address">{p.address}{p.postcode ? `, ${p.postcode}` : ""}</Text>
        <Text style={s.hint}>It will be hidden from dashboards, searches, review queues and installer jobs. Nothing is erased; an admin can restore it from Deleted projects.</Text>
        {delErr ? <Text style={s.err} testID="delete-error">{delErr}</Text> : null}
        <Btn title="Delete project" icon="trash-2" variant="danger" onPress={doDelete} loading={deleting} testID="delete-confirm" />
        <Btn title="Cancel" variant="outline" onPress={() => setDelOpen(false)} disabled={deleting} testID="delete-cancel" />
      </Sheet>

      <Sheet visible={!!rateFor} onClose={closeRate} title={`Agreed rate · ${rateFor?.name ?? ""}`} testID="rate-sheet" dismissOnBackdrop={false}>
        <Field label="Rate per job (£)" value={rateText} onChangeText={(t) => { setRateText(t); setRateErr(""); }} keyboardType="decimal-pad" returnKeyType="next" testID="rate-input" />
        <Field label="Reason (optional)" value={reason} onChangeText={setReason} maxLength={300} returnKeyType="done" testID="rate-reason-input" />
        <Text style={s.hint}>{rateFor?.earning ? "This job is earned but unpaid, so the unpaid amount will change too." : "Applies when this installer's NCs on this project are approved."} Every change is logged.</Text>
        {rateErr ? <Text style={s.err} testID="rate-error">{rateErr}</Text> : null}
        <Btn title="Save rate" onPress={saveRate} loading={savingRate} disabled={savingRate} testID="rate-save" />
        <Btn title="Cancel" variant="outline" onPress={closeRate} disabled={savingRate} testID="rate-cancel" />
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
  rates: { borderWidth: BORDER, borderColor: c.border, padding: space.md, gap: space.sm },
  rateRow: { flexDirection: "row", alignItems: "center", gap: space.sm, minHeight: 44 },
  rateName: { fontSize: 15, fontWeight: "700", color: c.onSurface },
  rateMeta: { fontSize: 13, color: c.muted },
  hint: { fontSize: 13, color: c.muted },
  err: { fontSize: 14, fontWeight: "600", color: c.error },
}));
