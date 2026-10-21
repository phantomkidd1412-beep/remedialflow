import { memo, useEffect, useState } from "react";
import { FlatList, Pressable, RefreshControl, ScrollView, Text, TextInput, useWindowDimensions, View } from "react-native";
import { useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { ProjectRow, useProjects } from "@/src/projects";
import { usesNativeTabs } from "@/src/navigation";
import { Badge, Empty, ErrorBlock, Feather, Loading, Progress } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { Bell } from "@/src/components/Bell";
import { BORDER, CONTENT_MAX, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

type Counts = { unassigned: number; in_progress: number; awaiting_review: number; completed: number; trustmark_resolved: number };
const COUNTERS: [keyof Counts, string][] = [
  ["unassigned", "Unassigned"], ["in_progress", "In progress"], ["awaiting_review", "Review"], ["completed", "Completed"], ["trustmark_resolved", "Resolved"],
];
const FILTERS = [["", "All"], ["unassigned", "Unassigned"], ["in_progress", "Work in progress"], ["awaiting_review", "Awaiting review"], ["completed", "Completed"], ["trustmark_resolved", "TrustMark resolved"]] as const;

const installersOf = (p: ProjectRow) => (p.installer_names?.length ? p.installer_names.join(", ") : "Unassigned");

const Card = memo(function Card({ p, onOpen }: { p: ProjectRow; onOpen: (id: string) => void }) {
  const s = useStyles();
  return (
    <Pressable style={({ pressed }) => [s.card, pressed && s.pressed]} onPress={() => onOpen(p.id)} testID={`project-row-${p.ref}`}>
      <View style={s.cardTop}>
        <Text style={s.ref}>{p.ref}</Text>
        <View style={s.badges}>
          {p.needs_review ? <Badge status="employee_review" label="Review" /> : null}
          <Badge status={p.status} />
        </View>
      </View>
      <Text style={s.addr}>{p.address}{p.postcode ? `, ${p.postcode}` : ""}</Text>
      <Text style={s.meta}>{installersOf(p)}</Text>
      <View style={s.progRow}>
        <View style={{ flex: 1 }}><Progress value={p.nc_total ? p.nc_done / p.nc_total : 0} /></View>
        <Text style={s.progText}>{p.nc_done}/{p.nc_total} NCs</Text>
      </View>
    </Pressable>
  );
});

const Row = memo(function Row({ p, onOpen }: { p: ProjectRow; onOpen: (id: string) => void }) {
  const s = useStyles();
  return (
    <Pressable style={({ pressed }) => [s.row, pressed && s.pressed]} onPress={() => onOpen(p.id)} testID={`project-row-${p.ref}`}>
      <Text style={[s.cell, { width: 110 }]}>{p.ref}</Text>
      <Text style={[s.cell, { flex: 2 }]}>{p.address}{p.postcode ? `, ${p.postcode}` : ""}</Text>
      <Text style={[s.cell, { flex: 1 }]}>{installersOf(p)}</Text>
      <View style={{ width: 120, gap: 2 }}>
        <Text style={s.cell}>{p.nc_done}/{p.nc_total}</Text>
        <Progress value={p.nc_total ? p.nc_done / p.nc_total : 0} />
      </View>
      <View style={{ width: 180, flexDirection: "row", gap: 4, justifyContent: "flex-end" }}>
        {p.needs_review ? <Badge status="employee_review" label="Review" /> : null}
        <Badge status={p.status} />
      </View>
    </Pressable>
  );
});

export default function Dashboard() {
  const s = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const wide = useWindowDimensions().width >= 768;
  const [text, setText] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setSearch(text.trim()), 300);
    return () => clearTimeout(t);
  }, [text]);
  const counts = useQuery({ queryKey: ["dashboard"], queryFn: () => api<Counts>("/dashboard") });
  const q = useProjects(search, status);
  const data = q.data?.pages.flatMap((p) => p.items) ?? [];
  const open = (id: string) => router.push(`/project/${id}`);

  const header = (
    <View style={s.counters}>
      {COUNTERS.map(([k, label]) => (
        <Pressable key={k} style={[s.counter, status === k && { backgroundColor: colors.brandSecondary, borderColor: colors.brandPrimary }]} onPress={() => setStatus(status === k ? "" : k)} testID={`tile-${k}`}>
          <Text style={[s.cVal, status === k && { color: colors.onBrandSecondary }]}>{counts.data?.[k] ?? "–"}</Text>
          <Text style={[s.cLabel, status === k && { color: colors.onBrandSecondary }]} numberOfLines={1} adjustsFontSizeToFit>{label}</Text>
        </Pressable>
      ))}
    </View>
  );

  return (
    <View style={s.root}>
      <ScreenHeader
        title="Projects"
        right={
          <View style={{ flexDirection: "row", gap: space.sm }}>
          <Bell />
          <Pressable style={s.newBtn} onPress={() => router.push("/project/new")} testID="new-project-button">
            <Feather name="plus" size={20} color={colors.onBrandPrimary} />
            <Text style={s.newText}>New</Text>
          </Pressable>
          </View>
        }
      >
        <View style={s.search}>
          <Feather name="search" size={18} color={colors.muted} />
          <TextInput value={text} onChangeText={setText} placeholder="Search address or reference" placeholderTextColor={colors.muted} style={s.searchInput} testID="project-search-input" autoCapitalize="none" />
        </View>
      </ScreenHeader>
      <View style={s.chipRow}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.chips}>
          {FILTERS.map(([v, l]) => (
            <Pressable key={l} onPress={() => setStatus(v)} style={[s.chip, status === v && { backgroundColor: colors.brandSecondary, borderColor: colors.brandPrimary }]} testID={`filter-chip-${v || "all"}`}>
              <Text style={[s.chipText, status === v && { color: colors.onBrandSecondary }]}>{l}</Text>
            </Pressable>
          ))}
        </ScrollView>
      </View>
      {q.isError ? (
        <View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error).message} onRetry={() => q.refetch()} /></View>
      ) : (
        <FlatList
          testID="project-table"
          data={data}
          keyExtractor={(p) => p.id}
          ListHeaderComponent={header}
          renderItem={({ item }) => (wide ? <Row p={item} onOpen={open} /> : <Card p={item} onOpen={open} />)}
          onEndReached={() => q.hasNextPage && q.fetchNextPage()}
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => { q.refetch(); counts.refetch(); }} />}
          contentContainerStyle={[s.list, { paddingBottom: (usesNativeTabs ? insets.bottom : 0) + space.xl }]}
          ListEmptyComponent={q.isLoading ? <Loading /> : <Empty icon="inbox" text="No projects match" />}
          initialNumToRender={10}
          windowSize={7}
          removeClippedSubviews
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  newBtn: { flexDirection: "row", alignItems: "center", gap: 4, height: 44, paddingHorizontal: space.lg, backgroundColor: c.brandPrimary, borderRadius: radius.pill },
  newText: { fontFamily: fonts.text, fontWeight: "700", fontSize: 15, color: c.onBrandPrimary },
  search: { flexDirection: "row", alignItems: "center", gap: space.sm, borderRadius: radius.md, backgroundColor: c.surfaceSecondary, paddingHorizontal: space.md, height: 44 },
  searchInput: { flex: 1, fontSize: 15, color: c.onSurface, height: 44 },
  chipRow: { height: 56, justifyContent: "center", borderBottomWidth: BORDER, borderColor: c.divider },
  chips: { gap: space.sm, paddingHorizontal: space.lg, alignItems: "center" },
  chip: { height: 36, flexShrink: 0, paddingHorizontal: space.lg, borderWidth: BORDER, borderRadius: radius.pill, borderColor: c.border, justifyContent: "center", backgroundColor: c.surface },
  chipText: { fontSize: 13, fontWeight: "600", color: c.onSurface },
  list: { padding: space.lg, gap: space.md, width: "100%", maxWidth: CONTENT_MAX, alignSelf: "center" },
  counters: { flexDirection: "row", gap: space.sm, marginBottom: space.sm },
  counter: { flex: 1, minHeight: 68, paddingVertical: space.sm, alignItems: "center", justifyContent: "center", borderRadius: radius.lg, borderWidth: BORDER, borderColor: c.border, backgroundColor: c.surfaceSecondary },
  cVal: { fontFamily: fonts.display, fontWeight: "800", fontSize: 22, color: c.onSurface },
  cLabel: { fontSize: 12, fontWeight: "600", color: c.muted },
  card: { borderWidth: BORDER, borderRadius: radius.lg, borderColor: c.border, padding: space.lg, gap: space.sm, backgroundColor: c.surface },
  pressed: { backgroundColor: c.surfaceSecondary },
  cardTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: space.sm },
  badges: { flexDirection: "row", gap: 4, flexShrink: 1, flexWrap: "wrap", justifyContent: "flex-end" },
  ref: { fontFamily: fonts.text, fontSize: 13, fontWeight: "600", color: c.muted, letterSpacing: 0.3 },
  addr: { fontFamily: fonts.display, fontWeight: "700", fontSize: 17, lineHeight: 23, color: c.onSurface },
  meta: { fontSize: 13, color: c.muted },
  progRow: { flexDirection: "row", alignItems: "center", gap: space.sm },
  progText: { fontSize: 13, fontWeight: "600", color: c.onSurface },
  row: { flexDirection: "row", alignItems: "center", paddingHorizontal: space.lg, minHeight: 60, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, backgroundColor: c.surface, gap: space.md },
  cell: { fontSize: 14, color: c.onSurface },
}));
