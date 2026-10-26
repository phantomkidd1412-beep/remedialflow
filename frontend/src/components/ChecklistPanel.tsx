import { useState } from "react";
import { Pressable, Text, View } from "react-native";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/src/api";
import { Btn, Field, Label, useToast } from "@/src/ui";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

export type CItem = { id: string; title: string; stage: string; locations: string[]; measurement: string; criterion: string; sources: any[] };
export type CView = {
  checklist: { status: "draft" | "confirmed" | "auto"; source: string; match?: { rating: string; score: number } | null; items: CItem[]; notes?: string[] } | null;
  coverage: { id: string; title: string; stage: string; state: "staff" | "ai" | "missing" }[];
};
export const ITEM_STAGE: Record<string, string> = { before: "BEFORE", during: "DURING", after: "COMPLETED", verification: "MEASURE/TEST" };
const STAGE_KEYS = Object.keys(ITEM_STAGE);

export function useChecklist(ncId: string, enabled: boolean) {
  return useQuery({ queryKey: ["checklist", ncId], queryFn: () => api<CView>(`/ncs/${ncId}/checklist`), enabled, staleTime: 30000 });
}

/** Staff-only NC evidence checklist inside the evidence review panel (Knowledge Index Phase 1). */
export function ChecklistPanel({ ncId, number }: { ncId: string; number: string }) {
  const s = useStyles();
  const { colors } = useTheme();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useChecklist(ncId, true);
  const [busy, setBusy] = useState(false);
  const [items, setItems] = useState<CItem[] | null>(null);
  const [title, setTitle] = useState("");
  const [stage, setStage] = useState("after");
  const [pname, setPname] = useState("");
  const cl = q.data?.checklist;
  const run = async (fn: () => Promise<any>, ok: string) => {
    setBusy(true);
    try {
      const res = await fn();
      if (res?.coverage) qc.setQueryData(["checklist", ncId], res);
      qc.invalidateQueries({ queryKey: ["evidence-review", ncId] });
      toast(ok);
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(false);
    }
  };
  const draft = () => run(() => api(`/ncs/${ncId}/checklist/draft`, { method: "POST", body: { force: false } }), "Checklist drafted");
  const editing = items ?? cl?.items ?? [];
  const confirm = () =>
    run(async () => {
      const res = await api(`/ncs/${ncId}/checklist`, { method: "PUT", body: { items: editing } });
      setItems(null);
      return res;
    }, "Checklist confirmed");
  const saveProfile = () =>
    run(async () => {
      await api(`/ncs/${ncId}/checklist/save-profile`, { method: "POST", body: { name: pname } });
      setPname("");
    }, "Profile proposed for admin approval");
  const state = Object.fromEntries((q.data?.coverage ?? []).map((c) => [c.id, c.state]));

  if (!q.data) return null;
  if (!cl)
    return (
      <View style={s.box}>
        <Label>Evidence checklist</Label>
        <Text style={s.small}>No checklist yet. Drafting uses an approved profile when one matches, otherwise one AI call.</Text>
        <Btn title="Draft checklist" small variant="outline" icon="list" onPress={draft} loading={busy} testID={`checklist-draft-${number}`} />
      </View>
    );
  const label = cl.status === "confirmed" ? "Confirmed by staff" : cl.status === "auto" ? "From approved profile" : cl.match?.rating === "uncertain" ? "Uncertain profile match — needs confirmation" : "AI draft — needs confirmation";
  return (
    <View style={s.box} testID={`checklist-${number}`}>
      <Label>Evidence checklist</Label>
      <Text style={[s.small, cl.status === "draft" && { color: colors.onSurface, fontWeight: "700" }]} testID={`checklist-status-${number}`}>{label}</Text>
      {(cl.notes ?? []).map((n, i) => <Text key={i} style={[s.small, { color: colors.error }]} testID={`checklist-note-${i}`}>{n}</Text>)}
      {editing.map((it) => (
        <View key={it.id} style={s.row} testID={`checklist-item-${it.id}`}>
          <Text style={[s.mark, { color: state[it.id] === "missing" || !state[it.id] ? colors.error : colors.success }]}>{state[it.id] === "staff" ? "✓✓" : state[it.id] === "ai" ? "✓" : "○"}</Text>
          <View style={{ flex: 1 }}>
            <Text style={s.item}>{ITEM_STAGE[it.stage]} · {it.title}{it.measurement ? ` (${it.measurement})` : ""}</Text>
            {(it.sources ?? []).filter((x) => x.doc_id).map((x, i) => (
              <Text key={i} style={s.small} testID={`checklist-source-${it.id}-${i}`}>Source: {x.title} {/^v/i.test(String(x.doc_version)) ? x.doc_version : `v${x.doc_version}`}, p.{x.page}{x.clause ? `, clause ${x.clause}` : ""}</Text>
            ))}
          </View>
          <Pressable onPress={() => setItems(editing.filter((x) => x.id !== it.id))} hitSlop={10} testID={`checklist-remove-${it.id}`}>
            <Text style={s.small}>✕</Text>
          </Pressable>
        </View>
      ))}
      <Text style={s.small}>✓✓ staff-marked photo · ✓ matched photo · ○ no photo yet</Text>
      <Field placeholder="Add requirement, e.g. Tape showing 250mm above tee" value={title} onChangeText={setTitle} maxLength={120} testID={`checklist-new-title-${number}`} />
      <View style={s.stages}>
        {STAGE_KEYS.map((k) => (
          <Pressable key={k} style={[s.chip, stage === k && { borderColor: colors.brandPrimary }]} onPress={() => setStage(k)} testID={`checklist-new-stage-${k}`}>
            <Text style={s.chipText}>{ITEM_STAGE[k]}</Text>
          </Pressable>
        ))}
        <Btn title="Add" small variant="outline" disabled={title.trim().length < 3} onPress={() => { setItems([...editing, { id: "", title: title.trim(), stage, locations: [], measurement: "", criterion: "", sources: [] }]); setTitle(""); }} testID={`checklist-add-${number}`} />
      </View>
      <Btn title={items ? "Save & confirm checklist" : cl.status === "confirmed" ? "Re-confirm checklist" : "Confirm checklist"} small onPress={confirm} loading={busy} disabled={!editing.length} testID={`checklist-confirm-${number}`} />
      {cl.status === "confirmed" ? (
        <View style={s.stages}>
          <View style={{ flex: 1 }}>
            <Field placeholder="Profile name, e.g. Gas entry tee clearance" value={pname} onChangeText={setPname} maxLength={120} testID={`profile-name-${number}`} />
          </View>
          <Btn title="Save as profile" small variant="outline" disabled={pname.trim().length < 3} onPress={saveProfile} loading={busy} testID={`profile-save-${number}`} />
        </View>
      ) : null}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  box: { gap: space.xs, borderTopWidth: BORDER, borderColor: c.border, paddingTop: space.sm },
  small: { fontFamily: fonts.text, fontSize: 11, color: c.muted },
  row: { flexDirection: "row", alignItems: "center", gap: space.sm, minHeight: 28 },
  mark: { fontFamily: fonts.text, fontSize: 13, fontWeight: "700", width: 22 },
  item: { flex: 1, fontFamily: fonts.text, fontSize: 12, color: c.onSurface },
  stages: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: space.xs },
  chip: { minHeight: 32, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, justifyContent: "center", paddingHorizontal: space.sm },
  chipText: { fontFamily: fonts.text, fontSize: 10, color: c.onSurface },
}));
