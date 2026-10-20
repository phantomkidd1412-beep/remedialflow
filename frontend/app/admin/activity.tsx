import { useEffect, useState } from "react";
import { FlatList, Pressable, RefreshControl, ScrollView, Text, TextInput, View } from "react-native";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { Empty, ErrorBlock, Feather, Loading, fmtDate } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

type Item = { seq: number; at: string; event: string; record_type: string; record_id: string; reference: string | null; actor_id: string | null; actor_name: string; actor_role: string | null; data: Record<string, any> };
type Page = { items: Item[]; has_more: boolean; page: number };
const KINDS = [["", "All"], ["deletion", "Deletions & restores"], ["project", "Projects"], ["nc", "NCs"], ["user", "Users"]] as const;
const label = (e: string) => e.replace(/[._]/g, " ");

/** Admin-only, read-only view of the hash-chained audit log. */
export default function Activity() {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [text, setText] = useState("");
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setSearch(text.trim()), 300);
    return () => clearTimeout(t);
  }, [text]);
  const q = useInfiniteQuery({
    queryKey: ["activity", search, kind],
    initialPageParam: 1,
    queryFn: ({ pageParam }) => api<Page>(`/admin/activity?${new URLSearchParams({ page: String(pageParam), search, kind })}`),
    getNextPageParam: (p) => (p.has_more ? p.page + 1 : undefined),
  });
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];

  const header = (
    <View style={{ gap: space.sm, marginBottom: space.sm }}>
      <View style={s.search}>
        <Feather name="search" size={18} color={colors.onSurface} />
        <TextInput value={text} onChangeText={setText} placeholder="User, action, project ref or address" placeholderTextColor={colors.muted} style={s.searchInput} autoCapitalize="none" testID="activity-search" />
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space.sm }}>
        {KINDS.map(([v, l]) => (
          <Pressable key={l} onPress={() => setKind(v)} style={[s.chip, kind === v && { backgroundColor: colors.brandSecondary }]} testID={`activity-kind-${v || "all"}`}>
            <Text style={[s.chipText, kind === v && { color: colors.onBrandSecondary }]}>{l}</Text>
          </Pressable>
        ))}
      </ScrollView>
    </View>
  );

  return (
    <View style={s.root}>
      <ScreenHeader title="Activity log" back />
      {q.isError ? (
        <View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error).message} onRetry={() => q.refetch()} /></View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={(i) => String(i.seq)}
          ListHeaderComponent={header}
          keyboardShouldPersistTaps="handled"
          onEndReached={() => q.hasNextPage && !q.isFetchingNextPage && q.fetchNextPage()}
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => q.refetch()} />}
          contentContainerStyle={[s.list, { paddingBottom: insets.bottom + space.xl }]}
          ListEmptyComponent={q.isLoading ? <Loading /> : <Empty icon="activity" text="No activity found" />}
          renderItem={({ item: i }) => (
            <View style={s.row} testID={`activity-${i.seq}`}>
              <View style={s.top}>
                <Text style={s.event}>{label(i.event)}</Text>
                <Text style={s.meta}>{fmtDate(i.at)}</Text>
              </View>
              <Text style={s.body}>{i.actor_name}{i.actor_role ? ` · ${i.actor_role}` : ""}{i.actor_id ? ` · ID ${i.actor_id.slice(-6)}` : ""}</Text>
              <Text style={s.meta}>{i.record_type}{i.reference ? ` · ${i.reference}` : ""}{i.data?.ref && i.record_type !== "project" ? ` · ${i.data.ref}` : ""}</Text>
            </View>
          )}
          ListFooterComponent={q.isFetchingNextPage ? <Loading /> : null}
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  list: { padding: space.lg, gap: space.sm },
  search: { flexDirection: "row", alignItems: "center", gap: space.sm, borderWidth: BORDER, borderColor: c.border, paddingHorizontal: space.md, minHeight: 48 },
  searchInput: { flex: 1, fontSize: 15, color: c.onSurface, minHeight: 44 },
  chip: { minHeight: 40, paddingHorizontal: space.md, borderWidth: BORDER, borderColor: c.border, justifyContent: "center" },
  chipText: { fontSize: 13, fontWeight: "600", color: c.onSurface },
  row: { borderWidth: BORDER, borderColor: c.border, padding: space.md, gap: 2 },
  top: { flexDirection: "row", justifyContent: "space-between", gap: space.sm },
  event: { flex: 1, fontFamily: fonts.display, fontWeight: "700", fontSize: 15, color: c.onSurface, textTransform: "capitalize" },
  body: { fontSize: 14, color: c.onSurface },
  meta: { fontSize: 13, color: c.muted },
}));
