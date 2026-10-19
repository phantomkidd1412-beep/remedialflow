import { memo, useEffect, useState } from "react";
import { FlatList, Pressable, RefreshControl, ScrollView, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { ProjectRow, useProjects } from "@/src/projects";
import { usesNativeTabs } from "@/src/navigation";
import { Badge, Empty, ErrorBlock, Feather, Loading, Progress } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

type Counts = { active_projects: number; outstanding_ncs: number; awaiting_review: number; completed_projects: number };
const FILTERS = [["", "All"], ["active", "Active"], ["awaiting_review", "Awaiting review"], ["completed", "Completed"]] as const;

const Row = memo(function Row({ p, onOpen }: { p: ProjectRow; onOpen: (id: string) => void }) {
  const s = useStyles();
  return (
    <Pressable style={({ pressed }) => [s.row, pressed && s.rowPressed]} onPress={() => onOpen(p.id)} testID={`project-row-${p.ref}`}>
      <Text style={[s.cell, s.cRef]} numberOfLines={1}>{p.ref}</Text>
      <View style={s.cAddr}>
        <Text style={s.addr} numberOfLines={1}>{p.address}</Text>
        <Text style={s.sub} numberOfLines={1}>{p.installer_name ?? "UNASSIGNED"}</Text>
      </View>
      <View style={s.cProg}>
        <Text style={s.cell}>{p.nc_done}/{p.nc_total}</Text>
        <Progress value={p.nc_total ? p.nc_done / p.nc_total : 0} />
      </View>
      <View style={s.cStatus}><Badge status={p.status} /></View>
    </Pressable>
  );
});

export default function Dashboard() {
  const s = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
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
  const c = counts.data;
  const tiles: [string, number | undefined, () => void, string][] = [
    ["Active projects", c?.active_projects, () => setStatus("active"), "tile-active-projects"],
    ["Outstanding NCs", c?.outstanding_ncs, () => setStatus(""), "tile-outstanding-ncs"],
    ["Awaiting review", c?.awaiting_review, () => router.push("/(staff)/review"), "tile-awaiting-review"],
    ["Completed", c?.completed_projects, () => setStatus("completed"), "tile-completed-projects"],
  ];

  return (
    <View style={s.root}>
      <ScreenHeader
        title="REMEDIALFLOW"
        right={
          <Pressable style={s.newBtn} onPress={() => router.push("/project/new")} testID="new-project-button">
            <Feather name="plus" size={20} color={colors.onBrandPrimary} />
            <Text style={s.newText}>NEW</Text>
          </Pressable>
        }
      >
        <View style={s.grid}>
          {tiles.map(([label, val, onPress, id]) => (
            <Pressable key={id} style={s.tile} onPress={onPress} testID={id}>
              <Text style={s.tVal}>{val ?? "–"}</Text>
              <Text style={s.tLabel}>{label}</Text>
            </Pressable>
          ))}
        </View>
        <View style={s.search}>
          <Feather name="search" size={18} color={colors.onSurface} />
          <TextInput value={text} onChangeText={setText} placeholder="Search ref, address, postcode" placeholderTextColor={colors.muted} style={s.searchInput} testID="project-search-input" autoCapitalize="none" />
        </View>
      </ScreenHeader>
      <View style={s.chipRow}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.chips}>
          {FILTERS.map(([v, l]) => (
            <Pressable key={l} onPress={() => setStatus(v)} style={[s.chip, status === v && { backgroundColor: colors.brandSecondary }]} testID={`filter-chip-${v || "all"}`}>
              <Text style={[s.chipText, status === v && { color: colors.onBrandSecondary }]}>{l}</Text>
            </Pressable>
          ))}
        </ScrollView>
      </View>
      <View style={s.thead}>
        <Text style={[s.th, s.cRef]}>REF</Text>
        <Text style={[s.th, s.cAddr]}>ADDRESS / INSTALLER</Text>
        <Text style={[s.th, s.cProg]}>NCS</Text>
        <Text style={[s.th, s.cStatus]}>STATUS</Text>
      </View>
      {q.isLoading ? (
        <Loading />
      ) : q.isError ? (
        <View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error).message} onRetry={() => q.refetch()} /></View>
      ) : (
        <FlatList
          testID="project-table"
          data={data}
          keyExtractor={(p) => p.id}
          renderItem={({ item }) => <Row p={item} onOpen={open} />}
          onEndReached={() => q.hasNextPage && q.fetchNextPage()}
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => { q.refetch(); counts.refetch(); }} />}
          contentContainerStyle={{ paddingBottom: (usesNativeTabs ? insets.bottom : 0) + space.xl }}
          ListEmptyComponent={<Empty icon="inbox" text="No projects match" />}
          initialNumToRender={15}
          removeClippedSubviews
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  newBtn: { flexDirection: "row", alignItems: "center", gap: 4, height: 44, paddingHorizontal: space.md, backgroundColor: c.brandPrimary, borderWidth: BORDER, borderColor: c.border },
  newText: { fontFamily: fonts.display, fontWeight: "900", fontSize: 14, color: c.onBrandPrimary },
  grid: { flexDirection: "row", flexWrap: "wrap", borderLeftWidth: BORDER, borderTopWidth: BORDER, borderColor: c.border },
  tile: { width: "50%", paddingVertical: space.md, paddingHorizontal: space.md, borderRightWidth: BORDER, borderBottomWidth: BORDER, borderColor: c.border },
  tVal: { fontFamily: fonts.display, fontWeight: "900", fontSize: 28, color: c.onSurface },
  tLabel: { fontFamily: fonts.mono, fontSize: 11, color: c.onSurface, textTransform: "uppercase" },
  search: { flexDirection: "row", alignItems: "center", gap: space.sm, borderWidth: BORDER, borderColor: c.border, paddingHorizontal: space.md, height: 44 },
  searchInput: { flex: 1, fontFamily: fonts.mono, fontSize: 14, color: c.onSurface, height: 44 },
  chipRow: { height: 56, justifyContent: "center", borderBottomWidth: BORDER, borderColor: c.border },
  chips: { gap: space.sm, paddingHorizontal: space.lg, alignItems: "center" },
  chip: { height: 36, flexShrink: 0, paddingHorizontal: space.md, borderWidth: BORDER, borderColor: c.border, justifyContent: "center", backgroundColor: c.surface },
  chipText: { fontFamily: fonts.mono, fontSize: 12, color: c.onSurface },
  thead: { flexDirection: "row", paddingHorizontal: space.lg, paddingVertical: space.sm, backgroundColor: c.surfaceInverse, gap: space.sm },
  th: { fontFamily: fonts.mono, fontSize: 10, color: c.onSurfaceInverse },
  row: { flexDirection: "row", alignItems: "center", paddingHorizontal: space.lg, minHeight: 56, borderBottomWidth: 1, borderColor: c.border, gap: space.sm },
  rowPressed: { backgroundColor: c.surfaceSecondary },
  cell: { fontFamily: fonts.mono, fontSize: 12, color: c.onSurface },
  cRef: { width: 64 },
  cAddr: { flex: 1 },
  cProg: { width: 48, gap: 2 },
  cStatus: { width: 96, alignItems: "flex-end" },
  addr: { fontFamily: fonts.display, fontWeight: "700", fontSize: 14, color: c.onSurface },
  sub: { fontFamily: fonts.mono, fontSize: 10, color: c.muted },
}));
