import { useState } from "react";
import { Pressable, Text, View } from "react-native";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/src/api";
import { NCItem } from "@/src/projects";
import { Sheet, useToast } from "@/src/ui";
import { ITEM_STAGE, useChecklist } from "@/src/components/ChecklistPanel";
import { fonts, makeStyles, BORDER, radius, space, useTheme } from "@/src/theme";

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
  const [picker, setPicker] = useState<{ photo: RPhoto; kind: "loc" | "stage" | "item" } | null>(null);
  const r = q.data;
  const cq = useChecklist(nc.id, !!r?.enabled);
  if (!r?.enabled) return null;
  const citems = cq.data?.checklist?.items ?? [];
  const locs = r.scope?.locations ?? [];
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

  // The location/coverage panel and technical references are no longer shown; classification, coverage and retrieval keep
  // running in the backend. Only the long-press photo details (staff corrections) remain visible.
  return (
    <View testID={`evidence-review-${nc.number}`}>
      <Sheet visible={!!detailsFor && !picker} onClose={() => onDetailsClose?.()} title={`Photo #${detailsFor ? idx(detailsFor) : ""} details`} testID="photo-details">
        {(() => {
          const p = (r.photos ?? []).find((x) => x.id === detailsFor);
          if (!p || !p.classified)
            return (
              <View style={{ gap: space.xs }}>
                <Text style={s.small}>This photo has not been tagged yet.</Text>
                <Pressable style={s.opt} onPress={() => { onDetailsClose?.(); classify(false); }} disabled={busy} testID="details-classify"><Text style={s.optText}>Tag photos now</Text></Pressable>
              </View>
            );
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
