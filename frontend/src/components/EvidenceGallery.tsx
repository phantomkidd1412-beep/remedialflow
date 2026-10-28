import { useEffect, useRef, useState } from "react";
import { Pressable, Text, useWindowDimensions, View } from "react-native";
import Feather from "@react-native-vector-icons/feather";
import { Image } from "expo-image";
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
type Rec = { item_id: string; title: string; file_id: string | null; state: string; source: string; flags: string[] };
type CL = { checklist: { status: string; items: { id: string; title: string }[] } | null };
const pending: Record<string, string[]> = {}; // unsaved local selections survive switching NCs
let seqCounter = Date.now();

/** Single evidence gallery: Select Evidence (instant local taps, debounced background save), Auto Select (switch ON),
 *  drag-to-reorder of the selected photos (= PDF order). null selection = never selected (legacy PDF behaviour). */
export type PendingUpload = { key: string; uri: string; pct: number; status: "queued" | "uploading" | "done" | "failed"; error?: string; shown?: boolean };

/** Photos from one multi-photo upload (client_id "up-<batch>-<index>") are shown in the order they were picked, not the
 *  order the uploads happened to finish. They keep the slots the batch occupies; everything else keeps its server order. */
const BATCH = /^up-(\d+)-(\d+)$/;
export function pickedOrder(list: EvidenceFile[]): EvidenceFile[] {
  const idx = (f: EvidenceFile) => BATCH.exec(f.client_id ?? "");
  const groups: Record<string, EvidenceFile[]> = {};
  list.forEach((f) => { const m = idx(f); if (m) (groups[m[1]] ??= []).push(f); });
  Object.values(groups).forEach((g) => g.sort((a, b) => Number(idx(a)![2]) - Number(idx(b)![2])));
  const used: Record<string, number> = {};
  return list.map((f) => { const m = idx(f); if (!m) return f; const i = used[m[1]] ?? 0; used[m[1]] = i + 1; return groups[m[1]][i]; });
}

type Tile = { real: EvidenceFile } | { up: PendingUpload };
/** Upload placeholders sit in their picked position among the batch's finished photos; a placeholder is dropped as soon as
 *  a photo with its client_id exists, so a photo is never shown twice (retries reuse the same key). */
export function withPlaceholders(photos: EvidenceFile[], uploads: PendingUpload[]): Tile[] {
  const tiles: Tile[] = photos.map((f) => ({ real: f }));
  const have = new Set(photos.map((p) => p.client_id).filter(Boolean));
  const keyOf = (t: Tile) => ("real" in t ? t.real.client_id ?? "" : t.up.key);
  for (const u of uploads) {
    if (u.shown || have.has(u.key)) continue;
    const m = BATCH.exec(u.key);
    let at = tiles.length;
    if (m) {
      let after = -1;
      let before = -1;
      tiles.forEach((t, i) => {
        const mm = BATCH.exec(keyOf(t));
        if (!mm || mm[1] !== m[1]) return;
        if (Number(mm[2]) < Number(m[2])) after = i;
        else if (before < 0) before = i;
      });
      at = after >= 0 ? after + 1 : before >= 0 ? before : tiles.length;
    }
    tiles.splice(at, 0, { up: u });
  }
  return tiles;
}

export function EvidenceGallery({ nc, projectId, onUpload, onDetails, uploads = [], onRetry }: { nc: NCItem; projectId: string; onUpload: () => void; onDetails?: (id: string) => void; uploads?: PendingUpload[]; onRetry?: (key: string) => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const qc = useQueryClient();
  const toast = useToast();
  const { width } = useWindowDimensions();
  const server = nc.evidence_selection?.ids ?? null;
  const [ids, setIds] = useState<string[] | null>(pending[nc.id] ?? server);
  const [mode, setMode] = useState(false);
  const [state, setState] = useState<"idle" | "saving" | "error">(pending[nc.id] ? "error" : "idle");
  const [pv, setPv] = useState<{ files: EvidenceFile[]; i: number; all: boolean } | null>(null);
  const [ask, setAsk] = useState(false);
  const [auto, setAuto] = useState<AutoRes | null>(null);
  const [autoBusy, setAutoBusy] = useState(false);
  const [review, setReview] = useState(false);
  const [busyItem, setBusyItem] = useState<string | null>(null);
  const recsQ = useQuery({ queryKey: ["photo-recs", nc.id], queryFn: () => api<{ items: Rec[] }>(`/ncs/${nc.id}/recommendations`), enabled: !!auto, staleTime: 30000 });
  const toReview = (recsQ.data?.items ?? []).filter((i) => i.file_id && (i.state === "replaced" || (i.state === "pending" && i.source !== "staff")));
  const clQ = useQuery({ queryKey: ["checklist", nc.id], queryFn: () => api<CL>(`/ncs/${nc.id}/checklist`), enabled: !!auto?.needs_checklist_confirmation, staleTime: 30000 });
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

  const photos = pickedOrder(nc.photos);
  const tiles = withPlaceholders(photos, uploads);
  const byId: Record<string, EvidenceFile> = Object.fromEntries(photos.map((f) => [f.id, f]));
  const selected = (ids ?? []).filter((i) => byId[i]);
  const has = ids !== null;
  const toggle = (id: string) => schedule(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  const open = (id: string, all: boolean) => {
    const files = all ? photos : selected.map((x) => byId[x]);
    setPv({ files, i: Math.max(0, files.findIndex((x) => x.id === id)), all });
  };
  const tap = (f: EvidenceFile) => {
    if (mode) return toggle(f.id);
    open(f.id, !(has && selected.includes(f.id)));
  };
  const size = Math.max(84, Math.min(140, Math.floor((Math.min(width, 1100) - 96) / 6)));
  const runAuto = async (m: "replace" | "add") => {
    setAsk(false);
    setAutoBusy(true);
    try {
      const r = await api<AutoRes>(`/ncs/${nc.id}/evidence-selection/auto`, { method: "POST", body: { mode: m } });
      setAuto(r);
      ["checklist", "photo-recs", "evidence-review"].forEach((k) => qc.invalidateQueries({ queryKey: [k, nc.id] }));
      const calls = `AI calls: ${r.ai_calls} of ${r.ai_limit}`;
      if (r.needs_checklist_confirmation) { setReview(true); return toast(`Please confirm the evidence checklist (${calls})`); }
      latest.current = r.selection?.ids ?? [];
      serverSeq.current = Math.max(serverSeq.current, r.selection?.seq ?? 0);
      delete pending[nc.id];
      setIds(r.selection?.ids ?? []);
      cache(r.selection);
      toast(`${r.selection?.ids.length ?? 0} photo(s) selected · ${calls}`);
      if (r.suggested.length || r.review_notes.length || r.missing.length) setReview(true);
    } catch (e: any) {
      toast(e.message);
    } finally {
      setAutoBusy(false);
    }
  };
  const decide = async (it: Rec, action: "accept" | "reject") => {
    setBusyItem(it.item_id);
    try {
      const res = await api<{ items: Rec[] }>(`/ncs/${nc.id}/recommendations/${it.item_id}`, { method: "PUT", body: { action } });
      qc.setQueryData(["photo-recs", nc.id], res);
      qc.invalidateQueries({ queryKey: ["project", projectId] }); // Reject removes the photo from the PDF selection
      qc.invalidateQueries({ queryKey: ["checklist", nc.id] });
      toast(action === "accept" ? "Accepted by staff" : "Rejected · removed from the PDF selection");
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusyItem(null);
    }
  };
  const confirmChecklist = async () => {
    setAutoBusy(true);
    try {
      await api(`/ncs/${nc.id}/checklist`, { method: "PUT", body: { items: clQ.data?.checklist?.items ?? [] } });
      setReview(false);
      setAutoBusy(false);
      await runAuto(selected.length ? "add" : "replace");
    } catch (e: any) {
      toast(e.message);
      setAutoBusy(false);
    }
  };
  const pendingCount = auto?.needs_checklist_confirmation ? 1 : toReview.length;

  return (
    <View style={s.gap}>
      <Label>Evidence ({photos.length} photo{photos.length === 1 ? "" : "s"}{has ? ` · ${selected.length} selected` : ""})</Label>
      {uploads.length ? (
        <Text style={s.noteText} testID={`upload-progress-${nc.number}`}>
          {uploads.filter((u) => u.status === "done").length} of {uploads.length} photographs uploaded
          {uploads.some((u) => u.status === "failed") ? ` · ${uploads.filter((u) => u.status === "failed").length} failed, tap to retry` : ""}
        </Text>
      ) : null}
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
      <View style={s.grid}>
        {tiles.map((t) => {
          if ("up" in t) {
            const u = t.up;
            return (
              <Pressable key={u.key} onPress={u.status === "failed" ? () => onRetry?.(u.key) : undefined} disabled={u.status !== "failed"}
                style={[s.cell, { borderColor: u.status === "failed" ? colors.error : colors.border }]} testID={`upload-tile-${u.key}`}
                accessibilityLabel={u.status === "failed" ? "Upload failed, tap to retry" : "Uploading photograph"}>
                <Image source={{ uri: u.uri }} style={{ width: size, height: size, borderRadius: radius.md, opacity: 0.45 }} contentFit="cover" />
                <View style={[s.upOverlay, { width: size, height: size }]} pointerEvents="none">
                  {u.status === "failed" ? <Feather name="rotate-cw" size={20} color={colors.onSurface} /> : null}
                  <Text style={s.upText}>{u.status === "failed" ? "Retry" : u.status === "queued" ? "Waiting" : u.status === "done" ? "✓" : `${Math.round(u.pct * 100)}%`}</Text>
                </View>
              </Pressable>
            );
          }
          const f = t.real;
          const on = selected.includes(f.id);
          return (
            <View key={f.id} style={[s.cell, on && { borderColor: colors.brandPrimary }, has && !on && { opacity: 0.4 }]} testID={`gallery-cell-${f.id}`}>
              <Thumb f={f} size={size} onPress={() => tap(f)} onLongPress={onDetails ? () => onDetails(f.id) : undefined} testID={`staff-photo-${f.id}`} />
              {on ? <View style={[s.tick, { backgroundColor: colors.brandPrimary }]} pointerEvents="none"><Feather name="check" size={12} color={colors.onBrandPrimary} /></View> : null}
              {f.status === "draft" ? <Text style={s.tag}>DRAFT</Text> : null}
            </View>
          );
        })}
        <Pressable style={[s.add, { width: size + 8, height: size + 8, borderColor: colors.border }]} onPress={onUpload} testID={`add-evidence-${nc.number}`}>
          <Feather name="upload" size={20} color={colors.onSurface} />
        </Pressable>
      </View>
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
      {auto && pendingCount ? (
        <Pressable onPress={() => setReview(true)} style={[s.note, { borderColor: colors.warning }]} testID={`auto-review-open-${nc.number}`}>
          <Text style={s.noteText}>{auto.needs_checklist_confirmation ? "Confirm the evidence checklist" : `${pendingCount} AI suggestion${pendingCount === 1 ? "" : "s"} to review`} · AI calls {auto.ai_calls} of {auto.ai_limit}</Text>
        </Pressable>
      ) : null}
      <Sheet visible={review} onClose={() => setReview(false)} title={auto?.needs_checklist_confirmation ? "Confirm evidence checklist" : "Review AI suggestions"} testID="auto-review">
        <Text style={s.small} testID={`auto-ai-calls-${nc.number}`}>AI calls used: {auto?.ai_calls ?? 0} of {auto?.ai_limit ?? 1}. Nothing is approved until staff confirm.</Text>
        {(auto?.review_notes ?? []).filter((m) => m.startsWith("Safety")).map((m) => <Text key={m} style={[s.warn, { backgroundColor: colors.warning, color: colors.onWarning }]}>{m}</Text>)}
        {auto?.needs_checklist_confirmation ? (
          <>
            {(clQ.data?.checklist?.items ?? []).map((i) => <Text key={i.id} style={s.item}>• {i.title}</Text>)}
            <Btn title="Confirm checklist & select photos" onPress={confirmChecklist} loading={autoBusy} disabled={!clQ.data?.checklist?.items?.length} testID="auto-confirm-checklist" />
          </>
        ) : (
          <>
            {toReview.map((it) => (
              <View key={it.item_id} style={s.rev} testID={`auto-rev-${it.item_id}`}>
                {it.file_id && byId[it.file_id] ? <Thumb f={byId[it.file_id]} size={56} onPress={() => open(it.file_id!, true)} /> : null}
                <View style={{ flex: 1, gap: 2 }}>
                  <Text style={s.item}>{it.title} · {it.state === "replaced" ? "Chosen by staff" : "AI suggested"}</Text>
                  {it.flags.map((x) => <Text key={x} style={s.small}>⚑ {x}</Text>)}
                  <View style={s.head}>
                    <Btn title={it.state === "replaced" ? "Confirm" : "Accept"} small onPress={() => decide(it, "accept")} loading={busyItem === it.item_id} style={s.flex} testID={`auto-accept-${it.item_id}`} />
                    {it.state === "pending" ? <Btn title="Reject" small variant="outline" onPress={() => decide(it, "reject")} disabled={busyItem === it.item_id} style={s.flex} testID={`auto-reject-${it.item_id}`} /> : null}
                  </View>
                </View>
              </View>
            ))}
            {(auto?.missing ?? []).map((m) => <Text key={m} style={s.small}>Missing evidence: {m}</Text>)}
            {!toReview.length ? <Text style={s.small}>All suggestions reviewed.</Text> : null}
          </>
        )}
      </Sheet>
      <Sheet visible={ask} onClose={() => setAsk(false)} title="You already selected photos" testID="auto-select-confirm">
        <Btn title="Replace my selection" onPress={() => runAuto("replace")} testID="auto-replace" />
        <Btn title="Add to my selection" variant="outline" onPress={() => runAuto("add")} testID="auto-add" />
        <Btn title="Cancel" variant="outline" onPress={() => setAsk(false)} testID="auto-cancel" />
      </Sheet>
      {pv ? (
        <PhotoViewer key={pv.all ? "all" : "sel"} files={pv.files} index={pv.i} onClose={() => setPv(null)} showInfo
          select={{ ids: selected, toggle }}
          scope={has && selected.length && selected.length !== photos.length ? { label: pv.all ? "Show selected only" : "Show all photos", onPress: (id) => open(pv.all && !selected.includes(id) ? selected[0] : id, !pv.all) } : undefined} />
      ) : null}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  gap: { gap: space.xs },
  head: { flexDirection: "row", alignItems: "center", gap: space.sm },
  flex: { flex: 1 },
  errText: { fontFamily: fonts.text, fontSize: 13, fontWeight: "700" },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: space.sm, paddingVertical: 2 },
  cell: { borderWidth: 3, borderColor: "transparent", borderRadius: radius.md, padding: 1 },
  tick: { position: "absolute", top: 4, right: 4, width: 20, height: 20, borderRadius: 10, alignItems: "center", justifyContent: "center" },
  num: { position: "absolute", top: 2, left: 2, minWidth: 18, height: 18, borderRadius: 9, alignItems: "center", justifyContent: "center", paddingHorizontal: 3 },
  numText: { fontFamily: fonts.text, fontSize: 10, fontWeight: "700" },
  add: { borderWidth: BORDER, borderStyle: "dashed", borderRadius: radius.md, alignItems: "center", justifyContent: "center" },
  tag: { fontFamily: fonts.text, fontSize: 9, color: c.muted, textAlign: "center" },
  small: { fontFamily: fonts.text, fontSize: 11, color: c.muted },
  item: { fontFamily: fonts.text, fontSize: 13, color: c.onSurface },
  warn: { fontFamily: fonts.text, fontSize: 12, fontWeight: "700", padding: space.sm, borderRadius: radius.md, overflow: "hidden" },
  rev: { flexDirection: "row", gap: space.sm, borderWidth: BORDER, borderColor: c.border, borderRadius: radius.md, padding: space.sm, marginBottom: space.xs },
  noteText: { fontFamily: fonts.text, fontSize: 12, fontWeight: "700", color: c.onSurface },
  upOverlay: { position: "absolute", top: 1, left: 1, alignItems: "center", justifyContent: "center", gap: 2 },
  upText: { fontFamily: fonts.text, fontSize: 13, fontWeight: "700", color: c.onSurface },
  err: { padding: space.sm, borderRadius: radius.md, minHeight: 44, justifyContent: "center" },
  note: { borderWidth: BORDER, borderRadius: radius.md, padding: space.sm, minHeight: 44, justifyContent: "center" },
}));
