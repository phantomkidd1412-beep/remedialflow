import { useEffect, useRef, useState } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import Feather from "@react-native-vector-icons/feather";
import Sortable from "react-native-sortables";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/src/api";
import { EvidenceFile, NCItem, ProjectDetail } from "@/src/projects";
import { Btn, Label, Sheet, useToast } from "@/src/ui";
import { Thumb } from "@/src/components/Media";
import { PhotoViewer } from "@/src/components/PhotoViewer";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

type Sel = { ids: string[]; ai_ids?: string[]; seq?: number } | null;
type AutoRes = { selection: Sel; missing: string[]; suggested: string[]; review_notes: string[]; ai_calls: number; ai_limit: number; checklist_status: string; needs_checklist_confirmation: boolean };
const pending: Record<string, string[]> = {}; // unsaved local selections survive switching NCs
let seqCounter = Date.now();

/** Single evidence gallery: Select Evidence (instant local taps, debounced background save), Auto Select (switch ON),
 *  drag-to-reorder of the selected photos (= PDF order). null selection = never selected (legacy PDF behaviour). */
export function EvidenceGallery({ nc, projectId, onUpload, onDetails }: { nc: NCItem; projectId: string; onUpload: () => void; onDetails?: (id: string) => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const qc = useQueryClient();
  const toast = useToast();
  const server = nc.evidence_selection?.ids ?? null;
  const [ids, setIds] = useState<string[] | null>(pending[nc.id] ?? server);
  const [mode, setMode] = useState(false);
  const [state, setState] = useState<"idle" | "saving" | "error">(pending[nc.id] ? "error" : "idle");
  const [pv, setPv] = useState<{ files: EvidenceFile[]; i: number } | null>(null);
  const [ask, setAsk] = useState(false);
  const [auto, setAuto] = useState<AutoRes | null>(null);
  const [autoBusy, setAutoBusy] = useState(false);
  const recsQ = useQuery({ queryKey: ["photo-recs", nc.id], queryFn: () => api<{ items: { title: string; state: string; source: string }[] }>(`/ncs/${nc.id}/recommendations`), enabled: !!auto, staleTime: 30000 });
  const stillSuggested = (auto?.suggested ?? []).filter((t) => !recsQ.data || recsQ.data.items.some((i) => i.title === t && i.state === "pending" && i.source !== "staff"));
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latest = useRef<string[] | null>(ids);
  const rq = useQuery({ queryKey: ["evidence-review", nc.id], queryFn: () => api<{ enabled: boolean }>(`/ncs/${nc.id}/evidence-review`), staleTime: 30000 });

  const cache = (sel: Sel) =>
    qc.setQueryData<ProjectDetail>(["project", projectId], (old) => (old ? { ...old, ncs: old.ncs.map((n) => (n.id === nc.id ? { ...n, evidence_selection: sel } : n)) } : old));
  const serverSeq = useRef(nc.evidence_selection?.seq ?? 0);
  useEffect(() => { // server-side changes (Reject/Replace in the evidence review) flow into the gallery when nothing is unsaved here
    const sq = nc.evidence_selection?.seq ?? 0;
    if (sq > serverSeq.current && !pending[nc.id]) {
      serverSeq.current = sq;
      latest.current = server;
      setIds(server);
    }
  }, [nc.evidence_selection?.seq]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = async (list: string[], retried = false): Promise<void> => {
    if (latest.current !== list) return; // superseded by a newer selection
    setState("saving");
    try {
      seqCounter = Math.max(seqCounter, serverSeq.current) + 1; // always newer than anything the server has seen
      const sel = await api<Sel & { stale?: boolean }>(`/ncs/${nc.id}/evidence-selection`, { method: "PUT", body: { ids: list, seq: seqCounter } });
      serverSeq.current = Math.max(serverSeq.current, sel?.seq ?? 0);
      if (sel?.stale) {
        if (latest.current !== list) return; // a newer save is already queued/in flight; never resend an outdated list
        if (!retried) return save(list, true); // re-send once with a higher counter
        throw new Error("stale");
      }
      if (latest.current === list) {
        delete pending[nc.id];
        setState("idle");
        cache(sel);
        qc.invalidateQueries({ queryKey: ["photo-recs", nc.id] }); // a removed suggestion shows as removed in the evidence review
      }
    } catch {
      if (latest.current === list) setState("error"); // kept locally (pending) until a retry succeeds
    }
  };
  const schedule = (list: string[]) => {
    latest.current = list;
    pending[nc.id] = list;
    setIds(list);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => save(list), 600);
  };
  useEffect(() => () => { // leaving the NC: send any unsaved change immediately
    if (timer.current) { clearTimeout(timer.current); if (latest.current && pending[nc.id]) save(latest.current); }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const photos = nc.photos;
  const byId: Record<string, EvidenceFile> = Object.fromEntries(photos.map((f) => [f.id, f]));
  const selected = (ids ?? []).filter((i) => byId[i]);
  const has = ids !== null;
  const tap = (f: EvidenceFile, i: number) => {
    if (mode) return schedule(selected.includes(f.id) ? selected.filter((x) => x !== f.id) : [...selected, f.id]);
    if (has && selected.includes(f.id)) return setPv({ files: selected.map((x) => byId[x]), i: selected.indexOf(f.id) });
    setPv({ files: photos, i });
  };
  const runAuto = async (m: "replace" | "add") => {
    setAsk(false);
    setAutoBusy(true);
    try {
      const r = await api<AutoRes>(`/ncs/${nc.id}/evidence-selection/auto`, { method: "POST", body: { mode: m } });
      setAuto(r);
      ["checklist", "photo-recs", "evidence-review"].forEach((k) => qc.invalidateQueries({ queryKey: [k, nc.id] }));
      const calls = `AI calls: ${r.ai_calls} of ${r.ai_limit}`;
      if (r.needs_checklist_confirmation) return toast(`Confirm the evidence checklist first, then press Auto Select again (${calls})`);
      latest.current = r.selection?.ids ?? [];
      serverSeq.current = Math.max(serverSeq.current, r.selection?.seq ?? 0);
      delete pending[nc.id];
      setIds(r.selection?.ids ?? []);
      cache(r.selection);
      toast(`${r.selection?.ids.length ?? 0} photo(s) selected · ${calls}`);
    } catch (e: any) {
      toast(e.message);
    } finally {
      setAutoBusy(false);
    }
  };

  return (
    <View style={s.gap}>
      <Label>Evidence ({photos.length} photo{photos.length === 1 ? "" : "s"}{has ? ` · ${selected.length} selected` : ""})</Label>
      <View style={s.head}>
        <Btn title={mode ? "Done" : "Select Evidence"} small variant={mode ? "primary" : "outline"} onPress={() => setMode(!mode)} disabled={!photos.length} style={s.flex} testID={`select-evidence-${nc.number}`} />
        {rq.data?.enabled ? <Btn title="Auto Select" small variant="outline" icon="star" onPress={() => (selected.length ? setAsk(true) : runAuto("replace"))} loading={autoBusy} disabled={!photos.length} style={s.flex} testID={`auto-select-${nc.number}`} /> : null}
      </View>
      {has && !selected.length ? <Text style={s.small} testID={`selection-empty-${nc.number}`}>No photos selected — the PDF will have no evidence photos.</Text> : null}
      {state === "error" ? (
        <Pressable onPress={() => latest.current && save(latest.current)} style={[s.err, { backgroundColor: colors.error }]} testID={`selection-retry-${nc.number}`}>
          <Text style={[s.errText, { color: colors.onError }]}>Selection not saved — tap to retry</Text>
        </Pressable>
      ) : null}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.row}>
        {photos.map((f, i) => {
          const on = selected.includes(f.id);
          return (
            <View key={f.id} style={[s.cell, on && { borderColor: colors.brandPrimary }, has && !on && { opacity: 0.4 }]} testID={`gallery-cell-${f.id}`}>
              <Thumb f={f} size={84} onPress={() => tap(f, i)} onLongPress={onDetails ? () => onDetails(f.id) : undefined} testID={`staff-photo-${f.id}`} />
              {on ? <View style={[s.tick, { backgroundColor: colors.brandPrimary }]} pointerEvents="none"><Feather name="check" size={12} color={colors.onBrandPrimary} /></View> : null}
              {f.status === "draft" ? <Text style={s.tag}>DRAFT</Text> : null}
            </View>
          );
        })}
        <Pressable style={[s.add, { borderColor: colors.border }]} onPress={onUpload} testID={`add-evidence-${nc.number}`}>
          <Feather name="upload" size={20} color={colors.onSurface} />
        </Pressable>
      </ScrollView>
      {mode ? <Text style={s.small}>Tap photos to select or deselect. Changes save automatically.</Text> : null}
      {selected.length > 1 ? (
        <View style={s.gap} testID={`selection-order-${nc.number}`}>
          <Text style={s.small}>PDF order — hold and drag to rearrange</Text>
          <Sortable.Grid
            columns={5}
            data={selected}
            keyExtractor={(x) => x}
            rowGap={6}
            columnGap={6}
            onDragEnd={({ data }) => schedule(data)}
            renderItem={({ item, index }) => (
              <View testID={`order-${index + 1}-${item}`}>
                <Thumb f={byId[item]} size={56} />
                <View style={[s.num, { backgroundColor: colors.brandPrimary }]} pointerEvents="none"><Text style={[s.numText, { color: colors.onBrandPrimary }]}>{index + 1}</Text></View>
              </View>
            )}
          />
        </View>
      ) : null}
      {auto ? (
        <View style={[s.note, { borderColor: colors.warning }]} testID={`auto-notes-${nc.number}`}>
          <Text style={s.small} testID={`auto-ai-calls-${nc.number}`}>AI calls used: {auto.ai_calls} of {auto.ai_limit}</Text>
          {auto.needs_checklist_confirmation ? <Text style={s.small}>Confirm the evidence checklist in the evidence review, then press Auto Select again. No photos were chosen yet.</Text> : null}
          {stillSuggested.length ? <Text style={s.small} testID={`auto-suggested-${nc.number}`}>AI suggested (not yet accepted by staff): {stillSuggested.join(", ")}. Accept or replace each in the evidence review.</Text> : null}
          {auto.missing.map((m) => <Text key={m} style={s.small}>Missing evidence: {m}</Text>)}
          {auto.review_notes.map((m) => <Text key={m} style={s.small}>{m}</Text>)}
        </View>
      ) : null}
      <Sheet visible={ask} onClose={() => setAsk(false)} title="You already selected photos" testID="auto-select-confirm">
        <Btn title="Replace my selection" onPress={() => runAuto("replace")} testID="auto-replace" />
        <Btn title="Add to my selection" variant="outline" onPress={() => runAuto("add")} testID="auto-add" />
        <Btn title="Cancel" variant="outline" onPress={() => setAsk(false)} testID="auto-cancel" />
      </Sheet>
      {pv ? <PhotoViewer files={pv.files} index={pv.i} onClose={() => setPv(null)} showInfo /> : null}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  gap: { gap: space.xs },
  head: { flexDirection: "row", alignItems: "center", gap: space.sm },
  flex: { flex: 1 },
  errText: { fontFamily: fonts.text, fontSize: 13, fontWeight: "700" },
  row: { gap: space.sm, paddingVertical: 2 },
  cell: { borderWidth: 3, borderColor: "transparent", borderRadius: radius.md, padding: 1 },
  tick: { position: "absolute", top: 4, right: 4, width: 20, height: 20, borderRadius: 10, alignItems: "center", justifyContent: "center" },
  num: { position: "absolute", top: 2, left: 2, minWidth: 18, height: 18, borderRadius: 9, alignItems: "center", justifyContent: "center", paddingHorizontal: 3 },
  numText: { fontFamily: fonts.text, fontSize: 10, fontWeight: "700" },
  add: { width: 90, height: 90, borderWidth: BORDER, borderStyle: "dashed", borderRadius: radius.md, alignItems: "center", justifyContent: "center" },
  tag: { fontFamily: fonts.text, fontSize: 9, color: c.muted, textAlign: "center" },
  small: { fontFamily: fonts.text, fontSize: 11, color: c.muted },
  err: { padding: space.sm, borderRadius: radius.md, minHeight: 44, justifyContent: "center" },
  note: { borderWidth: BORDER, borderRadius: radius.md, padding: space.sm, gap: 2 },
}));
