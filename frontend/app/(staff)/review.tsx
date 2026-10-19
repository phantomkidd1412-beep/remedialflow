import { useEffect, useState } from "react";
import { FlatList, Pressable, RefreshControl, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { useAuth } from "@/src/auth";
import { usesNativeTabs } from "@/src/navigation";
import { Badge, Empty, ErrorBlock, Feather, Loading } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

type Group = { project_id: string; ref: string; address: string; postcode?: string; pending: number; safety: boolean; oldest: string };

export default function Review() {
  const s = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user } = useAuth();
  const [text, setText] = useState("");
  const [search, setSearch] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setSearch(text.trim()), 300);
    return () => clearTimeout(t);
  }, [text]);
  const q = useQuery({ queryKey: ["review-groups", search], queryFn: () => api<Group[]>(`/review-groups?search=${encodeURIComponent(search)}`), refetchInterval: 20000 });
  const total = (q.data ?? []).reduce((a, g) => a + g.pending, 0);
  return (
    <View style={s.root}>
      <ScreenHeader title={user?.role === "admin" ? "Admin review" : "Review"} sub={q.data ? `${total} NC(s) across ${q.data.length} propert${q.data.length === 1 ? "y" : "ies"}` : " "}>
        <View style={s.search}>
          <Feather name="search" size={18} color={colors.onSurface} />
          <TextInput value={text} onChangeText={setText} placeholder="Search address or reference" placeholderTextColor={colors.muted} style={s.input} testID="review-search-input" autoCapitalize="none" />
        </View>
      </ScreenHeader>
      {q.isLoading ? (
        <Loading />
      ) : q.isError ? (
        <View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error).message} onRetry={() => q.refetch()} /></View>
      ) : (
        <FlatList
          testID="review-list"
          data={q.data}
          keyExtractor={(g) => g.project_id}
          contentContainerStyle={[s.list, { paddingBottom: (usesNativeTabs ? insets.bottom : 0) + space.xl }]}
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => q.refetch()} />}
          renderItem={({ item }) => (
            <Pressable style={({ pressed }) => [s.card, pressed && { backgroundColor: colors.surfaceSecondary }]} onPress={() => router.push(`/review/${item.project_id}`)} testID={`review-group-${item.ref}`}>
              <View style={{ flex: 1, gap: 4 }}>
                <Text style={s.addr}>{item.address}{item.postcode ? `, ${item.postcode}` : ""}</Text>
                <Text style={s.ref}>{item.ref}</Text>
                <View style={{ flexDirection: "row", gap: 4 }}>
                  <Badge status="employee_review" label={`${item.pending} awaiting`} />
                  {item.safety ? <Badge status="rejected" label="Safety" /> : null}
                </View>
              </View>
              <Feather name="chevron-right" size={22} color={colors.onSurface} />
            </Pressable>
          )}
          ListEmptyComponent={<Empty icon="check-circle" text="Nothing awaiting review" />}
          initialNumToRender={12}
          removeClippedSubviews
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  search: { flexDirection: "row", alignItems: "center", gap: space.sm, borderWidth: BORDER, borderColor: c.border, paddingHorizontal: space.md, height: 44 },
  input: { flex: 1, fontSize: 15, color: c.onSurface, height: 44 },
  list: { padding: space.lg, gap: space.md },
  card: { flexDirection: "row", alignItems: "center", gap: space.md, borderWidth: BORDER, borderColor: c.border, padding: space.md },
  addr: { fontFamily: fonts.display, fontWeight: "800", fontSize: 17, lineHeight: 22, color: c.onSurface },
  ref: { fontFamily: fonts.mono, fontSize: 13, color: c.muted },
}));
