import { useEffect, useRef, useState } from "react";
import { FlatList, Pressable, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useQueryClient } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as DocumentPicker from "expo-document-picker";
import { api, authed, BASE, readError } from "@/src/api";
import { uploadForm } from "@/src/utils/upload";
import { Btn, Empty, Feather, useToast } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

type Dup = { reason: string; project_id?: string; ref?: string; address?: string };
type Item = {
  id: string; filename: string; status: "processing" | "ready" | "failed" | "skipped" | "creating" | "created"; method?: "rules" | "ai" | null;
  ref?: string | null; address?: string | null; postcode?: string | null; nc_count: number; duplicate?: Dup | null; error?: string | null; project_id?: string | null;
};
type Row = Item | { id: string; filename: string; status: "uploading" | "upload_failed"; error?: string };

const PARALLEL = 2;

/** Admin: upload many TrustMark PDFs, preview what was read, then create Unassigned projects. Duplicates are skipped. */
export default function BulkImport() {
  const s = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const toast = useToast();
  const qc = useQueryClient();
  const insets = useSafeAreaInsets();
  const [bid, setBid] = useState<string | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [pending, setPending] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = async (id: string) => {
    const d = await api<{ items: Item[] }>(`/admin/bulk-imports/${id}`);
    setItems(d.items);
    return d.items;
  };

  useEffect(() => () => { if (timer.current) clearInterval(timer.current); }, []);
  const processing = items.some((i) => i.status === "processing");
  useEffect(() => {
    if (!bid || !processing) return;
    timer.current = setInterval(() => { refresh(bid).catch(() => {}); }, 2500);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [bid, processing]);

  const pick = async () => {
    const r = await DocumentPicker.getDocumentAsync({ type: "application/pdf", multiple: true, copyToCacheDirectory: true });
    if (r.canceled || !r.assets.length) return;
    setBusy(true);
    try {
      const id = bid ?? (await api<{ id: string }>("/admin/bulk-imports", { method: "POST" })).id;
      setBid(id);
      const assets = r.assets.map((a, k) => ({ ...a, key: `${Date.now()}-${k}` }));
      setPending((p) => [...p, ...assets.map((a) => ({ id: a.key, filename: a.name, status: "uploading" as const }))]);
      let next = 0;
      const worker = async () => {
        while (next < assets.length) {
          const a = assets[next++];
          try {
            const res = await authed((t) => uploadForm(`${BASE}/api/admin/bulk-imports/${id}/files`, {
              headers: t ? { Authorization: `Bearer ${t}` } : {},
              files: { file: { uri: a.uri, name: a.name || "report.pdf", mimeType: "application/pdf" } },
            }));
            if (!res.ok) throw new Error(await readError(res));
            setPending((p) => p.filter((x) => x.id !== a.key));
          } catch (e: any) {
            setPending((p) => p.map((x) => (x.id === a.key ? { ...x, status: "upload_failed", error: e.message } : x)));
          }
          refresh(id).catch(() => {});
        }
      };
      await Promise.all(Array.from({ length: Math.min(PARALLEL, assets.length) }, worker));
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(false);
    }
  };

  const toCreate = items.filter((i) => i.status === "ready" && !i.duplicate);
  const confirm = async () => {
    if (!bid) return;
    setBusy(true);
    try {
      const r = await api<{ created: unknown[]; skipped: unknown[] }>(`/admin/bulk-imports/${bid}/confirm`, { method: "POST" });
      ["projects", "dashboard", "tracker"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      toast(`${r.created.length} project${r.created.length === 1 ? "" : "s"} created${r.skipped.length ? `, ${r.skipped.length} skipped` : ""}`);
      await refresh(bid);
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(false);
    }
  };

  const rows: Row[] = [...items, ...pending];
  return (
    <View style={s.root}>
      <ScreenHeader title="Bulk import" sub="TrustMark PDFs become Unassigned projects" back />
      <FlatList
        data={rows}
        keyExtractor={(r) => r.id}
        contentContainerStyle={[s.list, { paddingBottom: insets.bottom + 140 }]}
        ListHeaderComponent={
          <View style={s.intro}>
            <Text style={s.introText}>Choose up to 50 PDFs. Each report is read and shown here first. Nothing is created until you confirm. Reports matching an existing project are skipped.</Text>
            <Btn title={rows.length ? "Add more PDFs" : "Choose PDFs"} icon="upload" variant="dark" onPress={pick} disabled={busy} testID="bulk-pick" />
          </View>
        }
        ListEmptyComponent={<Empty icon="file-text" text="No PDFs chosen yet" />}
        renderItem={({ item }) => <RowCard row={item} onOpen={(pid) => router.push(`/project/${pid}`)} colors={colors} />}
      />
      {items.length ? (
        <View style={[s.footer, { paddingBottom: insets.bottom + space.md }]}>
          <Text style={s.summary} testID="bulk-summary">
            {processing || pending.some((p) => p.status === "uploading") ? "Reading reports…  " : ""}
            {toCreate.length} ready · {items.filter((i) => i.status === "skipped" || (i.status === "ready" && i.duplicate)).length} skipped · {items.filter((i) => i.status === "created").length} created
          </Text>
          <Btn title={`Create ${toCreate.length} project${toCreate.length === 1 ? "" : "s"}`} icon="check" variant="dark" onPress={confirm}
            loading={busy && !pending.length} disabled={busy || processing || !toCreate.length} testID="bulk-confirm" />
        </View>
      ) : null}
    </View>
  );
}

function RowCard({ row, onOpen, colors }: { row: Row; onOpen: (pid: string) => void; colors: ReturnType<typeof useTheme>["colors"] }) {
  const s = useStyles();
  const it = row as Item;
  const dup = it.duplicate;
  const tone =
    row.status === "created" ? { label: "Created", icon: "check-circle" as const, color: colors.onSuccess }
    : row.status === "failed" || row.status === "upload_failed" ? { label: "Could not read", icon: "alert-circle" as const, color: colors.onError }
    : row.status === "skipped" || (row.status === "ready" && dup) ? { label: "Skipped", icon: "skip-forward" as const, color: colors.onWarning }
    : row.status === "ready" ? { label: "Ready", icon: "file-text" as const, color: colors.onBrandSecondary }
    : { label: row.status === "uploading" ? "Uploading" : "Reading", icon: "loader" as const, color: colors.muted };
  const open = row.status === "created" ? it.project_id : dup?.project_id;
  return (
    <Pressable style={s.card} onPress={open ? () => onOpen(open) : undefined} disabled={!open} testID={`bulk-row-${row.filename}`}>
      <View style={s.cardTop}>
        <Feather name={tone.icon} size={18} color={tone.color} />
        <Text style={s.file} numberOfLines={1}>{row.filename}</Text>
        <Text style={[s.chip, { color: tone.color }]} testID={`bulk-status-${row.filename}`}>{tone.label}</Text>
      </View>
      {it.ref ? <Text style={s.ref}>{it.ref}</Text> : null}
      {it.address ? <Text style={s.addr}>{it.address}{it.postcode ? `, ${it.postcode}` : ""}</Text> : null}
      {it.status === "ready" || it.status === "created" ? (
        <Text style={s.meta}>{it.nc_count} NC{it.nc_count === 1 ? "" : "s"}{it.method === "ai" ? " · read with AI (non-standard layout)" : ""}</Text>
      ) : null}
      {dup ? <Text style={s.meta}>{dup.reason}{dup.ref ? `: ${dup.ref}${dup.address ? `, ${dup.address}` : ""}` : ""}{dup.project_id ? " · tap to open" : ""}</Text> : null}
      {row.error ? <Text style={s.err}>{row.error}</Text> : null}
    </Pressable>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  list: { padding: space.lg, gap: space.sm },
  intro: { gap: space.md, marginBottom: space.sm },
  introText: { fontFamily: fonts.text, fontSize: 14, color: c.muted },
  card: { borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, padding: space.md, gap: space.xs },
  cardTop: { flexDirection: "row", alignItems: "center", gap: space.sm },
  file: { flex: 1, fontSize: 13, color: c.muted },
  chip: { fontSize: 12, fontWeight: "700" },
  ref: { fontFamily: fonts.display, fontWeight: "800", fontSize: 16, color: c.onSurface },
  addr: { fontSize: 14, color: c.onSurface },
  meta: { fontSize: 13, color: c.muted },
  err: { fontSize: 13, color: c.onError },
  footer: { position: "absolute", left: 0, right: 0, bottom: 0, padding: space.md, gap: space.sm, backgroundColor: c.surface, borderTopWidth: BORDER, borderColor: c.border },
  summary: { fontSize: 13, color: c.muted, textAlign: "center" },
}));
