import { FlatList, RefreshControl, Text, View } from "react-native";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { Btn, Empty, ErrorBlock, Loading, fmtDate, useToast } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { ITEM_STAGE } from "@/src/components/ChecklistPanel";
import { BORDER, radius, fonts, makeStyles, space } from "@/src/theme";

type Profile = { id: string; key: string; version: number; status: "proposed" | "approved" | "superseded" | "retired"; name: string;
  items: { id: string; title: string; stage: string }[]; examples: Record<string, string[]>; bad_examples?: Record<string, string[]>; learning_examples: number; created_at: string;
  signals: Record<string, string[]>; change?: string; based_on?: { version: number } | null };

/** Admin: reusable NC-type evidence profiles. Proposed versions need approval; earlier approved versions are kept as superseded. */
export default function Profiles() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["profiles"], queryFn: () => api<Profile[]>("/admin/profiles") });
  const setStatus = async (p: Profile, status: "approved" | "retired") => {
    try {
      await api(`/admin/profiles/${p.id}/status`, { method: "PUT", body: { status } });
      qc.invalidateQueries({ queryKey: ["profiles"] });
      toast(status === "approved" ? `Version ${p.version} approved` : "Profile retired");
    } catch (e: any) {
      toast(e.message);
    }
  };
  return (
    <View style={s.root}>
      <ScreenHeader title="NC Evidence Profiles" back>
        <Text style={s.hint}>Staff-confirmed checklists saved from real NCs. Approved profiles pre-fill the checklist for matching new NCs. Staff corrections become new proposed versions; nothing changes until you approve.</Text>
      </ScreenHeader>
      {q.isError ? (
        <View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error).message} onRetry={() => q.refetch()} /></View>
      ) : (
        <FlatList
          data={q.data ?? []}
          keyExtractor={(p) => p.id}
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => q.refetch()} />}
          contentContainerStyle={[s.list, { paddingBottom: insets.bottom + space.xl }]}
          ListEmptyComponent={q.isLoading ? <Loading /> : <Empty icon="layers" text="No profiles yet. Staff can save a confirmed NC checklist as a profile." />}
          renderItem={({ item: p }) => (
            <View style={s.card} testID={`profile-${p.id}`}>
              <Text style={s.title}>{p.name} · v{p.version}</Text>
              <Text style={s.meta} testID={`profile-status-${p.id}`}>{p.status.toUpperCase()} · {fmtDate(p.created_at)} · {p.learning_examples} staff markings · {(p.signals?.component ?? []).join(", ")}</Text>
              {p.based_on ? <Text style={s.meta} testID={`profile-change-${p.id}`}>Update of v{p.based_on.version}{p.change ? ` · ${p.change}` : ""}</Text> : null}
              {p.items.map((i) => (
                <Text key={i.id} style={s.item}>• {ITEM_STAGE[i.stage]} · {i.title} ({(p.examples?.[i.id] ?? []).length} good{(p.bad_examples?.[i.id] ?? []).length ? `, ${(p.bad_examples?.[i.id] ?? []).length} rejected` : ""} example{(p.examples?.[i.id] ?? []).length === 1 ? "" : "s"})</Text>
              ))}
              <View style={s.actions}>
                {p.status === "proposed" ? <Btn title="Approve" small onPress={() => setStatus(p, "approved")} style={{ flex: 1 }} testID={`profile-approve-${p.id}`} /> : null}
                {p.status === "approved" || p.status === "proposed" ? <Btn title="Retire" small variant="outline" onPress={() => setStatus(p, "retired")} style={{ flex: 1 }} testID={`profile-retire-${p.id}`} /> : null}
              </View>
            </View>
          )}
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  hint: { fontFamily: fonts.text, fontSize: 12, color: c.muted },
  list: { padding: space.lg, gap: space.md },
  card: { borderWidth: BORDER, borderColor: c.border, borderRadius: radius.md, padding: space.md, gap: 4 },
  title: { fontFamily: fonts.text, fontSize: 15, fontWeight: "700", color: c.onSurface },
  meta: { fontFamily: fonts.text, fontSize: 11, color: c.muted },
  item: { fontFamily: fonts.text, fontSize: 12, color: c.onSurface },
  actions: { flexDirection: "row", gap: space.sm, marginTop: space.xs },
}));
