import { FlatList, Pressable, RefreshControl, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { usesNativeTabs } from "@/src/navigation";
import { Badge, Empty, ErrorBlock, Loading, fmtDate } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, fonts, makeStyles, space } from "@/src/theme";

type Item = { id: string; project_id: string; project_ref: string; address: string; number: string; description: string; status: string; updated_at: string; safety_critical: boolean; ai?: { feedback: string; concerns: string[] } | null };

export default function Review() {
  const s = useStyles();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const q = useQuery({ queryKey: ["review-queue"], queryFn: () => api<Item[]>("/review-queue"), refetchInterval: 15000 });
  return (
    <View style={s.root}>
      <ScreenHeader title="REVIEW QUEUE" sub={q.data ? `${q.data.length} NC(s) need a decision` : " "} />
      {q.isLoading ? (
        <Loading />
      ) : q.isError ? (
        <View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error).message} onRetry={() => q.refetch()} /></View>
      ) : (
        <FlatList
          testID="review-list"
          data={q.data}
          keyExtractor={(i) => i.id}
          contentContainerStyle={[s.list, { paddingBottom: (usesNativeTabs ? insets.bottom : 0) + space.xl }]}
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => q.refetch()} />}
          renderItem={({ item }) => (
            <Pressable style={s.card} onPress={() => router.push(`/project/${item.project_id}?nc=${item.id}`)} testID={`review-item-${item.project_ref}-${item.number}`}>
              <View style={s.top}>
                <Text style={s.ref}>{item.project_ref} · {item.number}</Text>
                <Badge status={item.status} />
              </View>
              <Text style={s.desc} numberOfLines={2}>{item.description}</Text>
              {item.ai?.concerns?.length ? <Text style={s.concern} numberOfLines={2}>⚑ {item.ai.concerns.join(" · ")}</Text> : null}
              <Text style={s.meta}>{item.address} · {fmtDate(item.updated_at)}</Text>
            </Pressable>
          )}
          ListEmptyComponent={<Empty icon="check-circle" text="Nothing awaiting review" />}
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  list: { padding: space.lg, gap: space.md },
  card: { borderWidth: BORDER, borderColor: c.border, padding: space.md, gap: 6 },
  top: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  ref: { fontFamily: fonts.mono, fontSize: 12, color: c.onSurface },
  desc: { fontFamily: fonts.display, fontWeight: "700", fontSize: 15, color: c.onSurface },
  concern: { fontFamily: fonts.mono, fontSize: 11, color: c.onBrandTertiary },
  meta: { fontFamily: fonts.mono, fontSize: 11, color: c.muted },
}));
