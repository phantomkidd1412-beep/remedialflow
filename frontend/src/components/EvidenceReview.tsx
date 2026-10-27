import { useState } from "react";
import { Pressable, Text, View } from "react-native";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/src/api";
import { EvidenceFile, NCItem } from "@/src/projects";
import { Btn, Field, Label, Sheet, useToast } from "@/src/ui";
import { ChecklistPanel, ITEM_STAGE, useChecklist } from "@/src/components/ChecklistPanel";
import { ReferencesPanel } from "@/src/components/ReferencesPanel";
import { RecommendationsPanel } from "@/src/components/RecommendationsPanel";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

type RPhoto = { id: string; classified: boolean; pdf_include: boolean | null; checklist_item?: string | null; staff: string[]; location_id: string | null; location_label: string | null; location_group: string | null; stage: string | null; component: string | null; dup_of: string | null; relevant: boolean | null };
type Review = {
  enabled: boolean; unclassified?: number; photos?: RPhoto[];
  scope?: { scope_type: string; expected_count: number | null; scope_status: string; method?: string; locations: { id: string; label: string }[]; basis?: string[] };
  coverage?: { status: "complete" | "incomplete" | "unverified"; reason: string; missing: string[]; not_completed?: string[]; expected: number | null; covered: number | null };
};
const STAGES = ["before", "during", "after", "verification", "uncertain"];
const STAGE_TXT: Record<string, string> = { before: "BEFORE", during: "DURING", after: "COMPLETED", verification: "TEST/MEASURE", uncertain: "STAGE ?" };

/** Phase 5 staff-only review of AI location tags, stages, duplicates and expected locations. Renders nothing while the flag is off. */
export function EvidenceReview({ nc, detailsFor = null, onDetailsClose }: { nc: NCItem; detailsFor?: string | null; onDetailsClose?: () => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const qc = useQueryClient();
  const toast = useToast();
  const key = ["evidence-review", nc.id];
  const q = useQuery({ queryKey: key, queryFn: () => api<Review>(`/ncs/${nc.id}/evidence-review`), staleTime: 30000 });
  const [busy, setBusy] = useState(false);
  const [edit, setEdit] = useState(false);
  const [count, setCount] = useState("");
  const [labels, setLabels] = useState("");
  const [basis, setBasis] = useState("");
  const [picker, setPicker] = useState<{ photo: RPhoto; kind: "loc" | "stage" | "item" } | null>(null);
  const r = q.data;
  const cq = useChecklist(nc.id, !!r?.enabled);
  if (!r?.enabled) return null;
  const citems = cq.data?.checklist?.items ?? [];
  const locs = r.scope?.locations ?? [];
  const byId: Record<string, EvidenceFile> = Object.fromEntries(nc.photos.map((f) => [f.id, f]));
  const idx = (id: string) => (r.photos ?? []).findIndex((p) => p.id === id) + 1;

  const run = async (fn: () => Promise<any>, ok: string) => {
    setBusy(true);
    try {
      const res = await fn();
      if (res?.enabled) qc.setQueryData(key, res);
      else qc.invalidateQueries({ queryKey: key });
      qc.invalidateQueries({ queryKey: ["evidence-review", nc.id] });
      qc.invalidateQueries({ queryKey: ["photo-recs", nc.id] });
      toast(ok);
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(false);
    }
  };
  const correct = (p: RPhoto, body: Record<string, any>) => run(() => api(`/evidence/${p.id}/classification`, { method: "PUT", body }), "Correction saved");
  const pick = (value: string | null) => {
    const p = picker;
    setPicker(null);
    if (!p) return;
    if (p.kind === "item")
      return run(async () => {
        qc.setQueryData(["checklist", nc.id], await api(`/evidence/${p.photo.id}/checklist-item`, { method: "PUT", body: { item_id: value } }));
        return api<Review>(`/ncs/${nc.id}/evidence-review`);
      }, "Best photo saved");
    correct(p.photo, p.kind === "loc" ? { location_id: value } : { stage: value });
  };
  const options =
    picker?.kind === "loc" ? [{ v: null, t: "Location not identified" }, ...locs.map((l) => ({ v: l.id, t: l.label }))]
    : picker?.kind === "item" ? [{ v: null, t: "Not a checklist photo" }, ...citems.map((i) => ({ v: i.id, t: `${ITEM_STAGE[i.stage]} · ${i.title}` }))]
    : STAGES.map((v) => ({ v, t: STAGE_TXT[v] }));
  const classify = (force = false) => run(() => api(`/ncs/${nc.id}/evidence-review/classify`, { method: "POST", body: { force } }), "Photos classified");
  const saveScope = () =>
    run(async () => {
      const res = await api(`/ncs/${nc.id}/scope`, { method: "PUT", body: { expected_count: Number(count), labels: labels.split(",").map((x) => x.trim()).filter(Boolean), basis } });
      setEdit(false);
      return res;
    }, "Expected locations saved");

  const cov = r.coverage;
  const covColor = cov?.status === "complete" ? colors.success : cov?.status === "incomplete" ? colors.error : colors.warning;
  const covText = cov?.status === "complete" ? colors.onSuccess : cov?.status === "incomplete" ? colors.onError : colors.onWarning;
  return (
    <View style={s.box} testID={`evidence-review-${nc.number}`}>
      <Label>Location evidence review (staff only)</Label>
      {cov ? (
        <View style={[s.cov, { backgroundColor: covColor }]} testID={`coverage-${nc.number}`}>
          <Text style={[s.covHead, { color: covText }]}>Coverage: {cov.status.toUpperCase()}{cov.expected ? ` · ${cov.covered ?? "?"} of ${cov.expected}` : ""}</Text>
          {cov.reason ? <Text style={[s.small, { color: covText }]}>{cov.reason}</Text> : null}
          {cov.missing?.length ? <Text style={[s.small, { color: covText }]} testID={`coverage-missing-${nc.number}`}>Missing: {cov.missing.join(", ")}</Text> : null}
          {cov.not_completed?.length ? <Text style={[s.small, { color: covText }]}>Shown but not completed: {cov.not_completed.join(", ")}</Text> : null}
        </View>
      ) : null}
      <Text style={s.small} testID={`review-scope-${nc.number}`}>
        Expected: {r.scope?.expected_count ?? "not confirmed"}{locs.length ? ` · ${locs.map((l) => l.label).join(", ")}` : ""}{r.scope?.method === "staff" ? " · confirmed by staff" : r.scope?.method === "rules_preview" ? " · from NC wording" : ""}
      </Text>
      {edit ? (
        <View style={{ gap: space.xs }}>
          <Field placeholder="Expected number of locations" value={count} onChangeText={setCount} keyboardType="number-pad" testID={`scope-count-${nc.number}`} />
          <Field placeholder="Location names, comma separated (optional)" value={labels} onChangeText={setLabels} testID={`scope-labels-${nc.number}`} />
          <Field placeholder="Where this comes from (e.g. NC wording, TrustMark report p.4)" value={basis} onChangeText={setBasis} maxLength={300} testID={`scope-basis-${nc.number}`} />
          <View style={s.actions}>
            <Btn title="Save" small onPress={saveScope} loading={busy} disabled={!Number(count) || basis.trim().length < 5} style={{ flex: 1 }} testID={`scope-save-${nc.number}`} />
            <Btn title="Cancel" small variant="outline" onPress={() => setEdit(false)} style={{ flex: 1 }} />
          </View>
        </View>
      ) : (
        <View style={s.actions}>
          <Btn title="Edit expected locations" small variant="outline" onPress={() => { setCount(String(r.scope?.expected_count ?? "")); setLabels(locs.map((l) => l.label).join(", ")); setEdit(true); }} style={{ flex: 1 }} testID={`scope-edit-${nc.number}`} />
          <Btn title={r.unclassified ? `Classify ${r.unclassified} photo${r.unclassified === 1 ? "" : "s"}` : "Re-classify"} small variant="outline" icon="cpu" onPress={() => classify(!r.unclassified)} loading={busy} disabled={!r.photos?.length} style={{ flex: 1 }} testID={`classify-${nc.number}`} />
        </View>
      )}
      <Sheet visible={!!detailsFor && !picker} onClose={() => onDetailsClose?.()} title={`Photo #${detailsFor ? idx(detailsFor) : ""} details`} testID="photo-details">
        {(() => {
          const p = (r.photos ?? []).find((x) => x.id === detailsFor);
          if (!p) return <Text style={s.small}>This photo has not been tagged yet. Use “Classify” below first.</Text>;
          const open = (kind: "loc" | "stage" | "item") => { onDetailsClose?.(); setPicker({ photo: p, kind }); };
          return (
            <View style={{ gap: space.xs }}>
              <Pressable style={s.opt} onPress={() => open("loc")} disabled={!p.classified} testID="details-loc"><Text style={s.optText}>Location: {p.location_label ?? "not identified"} ▾</Text></Pressable>
              <Pressable style={s.opt} onPress={() => open("stage")} disabled={!p.classified} testID="details-stage"><Text style={s.optText}>Stage: {STAGE_TXT[p.stage ?? "uncertain"]} ▾</Text></Pressable>
              {citems.length ? <Pressable style={s.opt} onPress={() => open("item")} testID="details-item"><Text style={s.optText}>Requirement: {citems.find((i) => i.id === p.checklist_item)?.title ?? "none"} ▾</Text></Pressable> : null}
              {p.dup_of ? <Pressable style={s.opt} onPress={() => { onDetailsClose?.(); correct(p, { not_duplicate: true }); }} testID="details-notdup"><Text style={s.optText}>Marked duplicate of #{idx(p.dup_of)} — not a duplicate</Text></Pressable> : null}
            </View>
          );
        })()}
      </Sheet>
      <Sheet visible={!!picker} onClose={() => setPicker(null)} title={picker?.kind === "loc" ? "Photo location" : picker?.kind === "item" ? "Best photo for checklist item" : "Remediation stage"} testID="review-picker">
        {options.map((o) => {
          const cur = picker && (picker.kind === "loc" ? picker.photo.location_id : picker.kind === "item" ? picker.photo.checklist_item ?? null : picker.photo.stage ?? "uncertain") === o.v;
          return (
            <Pressable key={String(o.v)} style={[s.opt, cur && { borderColor: colors.brandPrimary }]} onPress={() => pick(o.v)} testID={`review-opt-${o.v ?? "none"}`}>
              <Text style={s.optText}>{o.t}{cur ? "  ✓" : ""}</Text>
            </Pressable>
          );
        })}
      </Sheet>
      <ChecklistPanel ncId={nc.id} number={nc.number} />
      <RecommendationsPanel ncId={nc.id} number={nc.number} photos={byId} />
      <ReferencesPanel ncId={nc.id} number={nc.number} />
      <Text style={s.small}>Long-press a photo in the Evidence gallery to correct its location, stage or requirement. Corrections override the AI. Photos are never deleted.</Text>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  box: { gap: space.sm, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, padding: space.md },
  cov: { borderRadius: radius.md, padding: space.sm, gap: 2 },
  covHead: { fontFamily: fonts.text, fontSize: 12, fontWeight: "700" },
  small: { fontFamily: fonts.text, fontSize: 11, color: c.muted },
  actions: { flexDirection: "row", gap: space.sm },
  row: { gap: space.sm, paddingBottom: space.xs },
  photo: { width: 96, gap: 4 },
  chip: { minHeight: 32, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, alignItems: "center", justifyContent: "center", paddingHorizontal: 4 },
  tag: { fontFamily: fonts.text, fontSize: 9, color: c.onSurface },
  opt: { minHeight: 48, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, justifyContent: "center", paddingHorizontal: space.md, marginBottom: space.xs },
  optText: { fontFamily: fonts.text, fontSize: 14, color: c.onSurface },
}));
