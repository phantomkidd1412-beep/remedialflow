import { useState } from "react";
import { FlatList, RefreshControl, Text, View } from "react-native";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { Btn, Empty, ErrorBlock, Field, Loading, Sheet, fmtDate, useToast } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, radius, fonts, makeStyles, space } from "@/src/theme";

type Deleted = { id: string; ref: string; address: string; postcode: string; nc_total: number; deleted: { at: string; by_name: string; by_role: string } };

/** Admin recycle bin: soft-deleted projects, restorable with all their data. */
export default function DeletedProjects() {
  const s = useStyles();
  const qc = useQueryClient();
  const toast = useToast();
  const insets = useSafeAreaInsets();
  const [search, setSearch] = useState("");
  const term = search.trim();
  const q = useQuery({ queryKey: ["deleted-projects", term], queryFn: () => api<Deleted[]>(`/admin/deleted-projects${term ? `?q=${encodeURIComponent(term)}` : ""}`) });
  const [busy, setBusy] = useState<string | null>(null);
  const [purge, setPurge] = useState<Deleted | null>(null);
  const [typed, setTyped] = useState("");
  const [purgeErr, setPurgeErr] = useState("");

  const doPurge = async () => {
    if (!purge) return;
    setBusy(purge.id);
    setPurgeErr("");
    try {
      await api(`/admin/projects/${purge.id}/purge`, { method: "POST", body: { confirm_ref: typed.trim() } });
      qc.invalidateQueries({ queryKey: ["deleted-projects"] });
      toast(`${purge.ref} permanently deleted`);
      setPurge(null);
    } catch (e: any) {
      setPurgeErr(e.message);
    } finally {
      setBusy(null);
    }
  };

  const restore = async (d: Deleted) => {
    setBusy(d.id);
    try {
      await api(`/admin/projects/${d.id}/restore`, { method: "POST" });
      ["deleted-projects", "projects", "dashboard", "review-groups", "tracker"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      toast(`${d.ref} restored`);
    } catch (e: any) {
      toast(e.message);
      q.refetch();
    } finally {
      setBusy(null);
    }
  };

  return (
    <View style={s.root}>
      <ScreenHeader title="Deleted projects" back>
        <Field placeholder="Search by address or reference" value={search} onChangeText={setSearch} autoCorrect={false} testID="deleted-search" />
      </ScreenHeader>
      {q.isError ? (
        <View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error).message} onRetry={() => q.refetch()} /></View>
      ) : (
        <FlatList
          data={q.data ?? []}
          keyExtractor={(d) => d.id}
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => { q.refetch(); }} />}
          contentContainerStyle={[s.list, { paddingBottom: insets.bottom + space.xl }]}
          keyboardShouldPersistTaps="handled"
          ListEmptyComponent={q.isLoading ? <Loading /> : <Empty icon="trash-2" text={term ? "No deleted projects match your search" : "No deleted projects"} />}
          renderItem={({ item: d }) => (
            <View style={s.card} testID={`deleted-row-${d.ref}`}>
              <Text style={s.ref}>{d.ref}</Text>
              <Text style={s.addr}>{d.address}{d.postcode ? `, ${d.postcode}` : ""}</Text>
              <Text style={s.meta}>Deleted {fmtDate(d.deleted.at)} by {d.deleted.by_name} ({d.deleted.by_role}) · {d.nc_total} NC{d.nc_total === 1 ? "" : "s"}</Text>
              <View style={{ flexDirection: "row", gap: space.sm }}>
                <Btn title="Restore" icon="rotate-ccw" small variant="dark" onPress={() => restore(d)} loading={busy === d.id && !purge} disabled={!!busy} style={{ flex: 1 }} testID={`restore-${d.ref}`} />
                <Btn title="Delete forever" icon="trash-2" small variant="danger" onPress={() => { setPurge(d); setTyped(""); setPurgeErr(""); }} disabled={!!busy} style={{ flex: 1 }} testID={`purge-${d.ref}`} />
              </View>
            </View>
          )}
        />
      )}
      <Sheet visible={!!purge} onClose={() => !busy && setPurge(null)} title="Permanently delete?" testID="purge-sheet" dismissOnBackdrop={false}>
        <Text style={s.ref}>{purge?.ref}</Text>
        <Text style={s.addr}>{purge?.address}</Text>
        <Text style={s.warn}>This cannot be undone. The project, its NCs, evidence records, assignments and notifications will be removed. Payment records are kept for your payment history.</Text>
        <Field label={`Type ${purge?.ref ?? ""} to confirm`} value={typed} onChangeText={(t) => { setTyped(t); setPurgeErr(""); }} autoCapitalize="characters" autoCorrect={false} testID="purge-confirm-input" />
        {purgeErr ? <Text style={s.warn} testID="purge-error">{purgeErr}</Text> : null}
        <Btn title="Permanently delete" icon="trash-2" variant="danger" onPress={doPurge} loading={!!busy} disabled={typed.trim() !== purge?.ref} testID="purge-confirm" />
        <Btn title="Cancel" variant="outline" onPress={() => setPurge(null)} disabled={!!busy} testID="purge-cancel" />
      </Sheet>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  list: { padding: space.lg, gap: space.sm },
  card: { borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, padding: space.md, gap: space.xs },
  ref: { fontFamily: fonts.display, fontWeight: "800", fontSize: 16, color: c.onSurface },
  addr: { fontSize: 14, color: c.onSurface },
  meta: { fontSize: 13, color: c.muted, marginBottom: space.xs },
  warn: { fontSize: 14, fontWeight: "600", color: c.onError },
}));
