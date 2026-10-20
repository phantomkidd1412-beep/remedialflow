import { useState } from "react";
import { FlatList, RefreshControl, Text, View } from "react-native";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { Btn, Empty, ErrorBlock, Loading, fmtDate, useToast } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, fonts, makeStyles, space } from "@/src/theme";

type Deleted = { id: string; ref: string; address: string; postcode: string; nc_total: number; deleted: { at: string; by_name: string; by_role: string } };

/** Admin recycle bin: soft-deleted projects, restorable with all their data. */
export default function DeletedProjects() {
  const s = useStyles();
  const qc = useQueryClient();
  const toast = useToast();
  const insets = useSafeAreaInsets();
  const q = useQuery({ queryKey: ["deleted-projects"], queryFn: () => api<Deleted[]>("/admin/deleted-projects") });
  const [busy, setBusy] = useState<string | null>(null);

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
      <ScreenHeader title="Deleted projects" back />
      {q.isError ? (
        <View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error).message} onRetry={() => q.refetch()} /></View>
      ) : (
        <FlatList
          data={q.data ?? []}
          keyExtractor={(d) => d.id}
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => q.refetch()} />}
          contentContainerStyle={[s.list, { paddingBottom: insets.bottom + space.xl }]}
          ListEmptyComponent={q.isLoading ? <Loading /> : <Empty icon="trash-2" text="No deleted projects" />}
          renderItem={({ item: d }) => (
            <View style={s.card} testID={`deleted-row-${d.ref}`}>
              <Text style={s.ref}>{d.ref}</Text>
              <Text style={s.addr}>{d.address}{d.postcode ? `, ${d.postcode}` : ""}</Text>
              <Text style={s.meta}>Deleted {fmtDate(d.deleted.at)} by {d.deleted.by_name} ({d.deleted.by_role}) · {d.nc_total} NC{d.nc_total === 1 ? "" : "s"}</Text>
              <Btn title="Restore project" icon="rotate-ccw" small variant="dark" onPress={() => restore(d)} loading={busy === d.id} disabled={!!busy} testID={`restore-${d.ref}`} />
            </View>
          )}
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  list: { padding: space.lg, gap: space.sm },
  card: { borderWidth: BORDER, borderColor: c.border, padding: space.md, gap: space.xs },
  ref: { fontFamily: fonts.display, fontWeight: "800", fontSize: 16, color: c.onSurface },
  addr: { fontSize: 14, color: c.onSurface },
  meta: { fontSize: 13, color: c.muted, marginBottom: space.xs },
}));
