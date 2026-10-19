import { FlatList, RefreshControl, Text, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { usesNativeTabs } from "@/src/navigation";
import { Badge, Empty, ErrorBlock, Loading, money, fmtDate } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, fonts, makeStyles, space } from "@/src/theme";

type Earn = { id: string; ref: string; address: string; amount: number; currency: string; status: string; earned_at: string; paid_at?: string | null };
type Resp = { currency: string; payment_terms: string; items: Earn[]; earned: number; paid: number; in_progress: number; jobs_in_progress: number };

export default function Earnings() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const q = useQuery({ queryKey: ["earnings"], queryFn: () => api<Resp>("/earnings") });
  if (q.isLoading) return <Loading />;
  const d = q.data;
  return (
    <View style={s.root}>
      <ScreenHeader title="EARNINGS" sub={d?.payment_terms} />
      {q.isError || !d ? (
        <View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error)?.message ?? "Failed"} onRetry={() => q.refetch()} /></View>
      ) : (
        <FlatList
          data={d.items}
          keyExtractor={(i) => i.id}
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => q.refetch()} />}
          contentContainerStyle={[s.list, { paddingBottom: (usesNativeTabs ? insets.bottom : 0) + space.xl }]}
          ListHeaderComponent={
            <View style={s.tiles}>
              <View style={[s.tile, s.tileBrand]}><Text style={s.tLabel}>Unpaid</Text><Text style={s.tVal} testID="earnings-unpaid">{money(d.earned, d.currency)}</Text></View>
              <View style={s.tile}><Text style={s.tLabel}>Paid</Text><Text style={s.tVal} testID="earnings-paid">{money(d.paid, d.currency)}</Text></View>
              <View style={[s.tile, { flexBasis: "100%" }]}>
                <Text style={s.tLabel}>In progress · {d.jobs_in_progress} job(s)</Text>
                <Text style={s.tValSm} testID="earnings-in-progress">{money(d.in_progress, d.currency)} on completion</Text>
              </View>
            </View>
          }
          renderItem={({ item }) => (
            <View style={s.row} testID={`earning-row-${item.ref}`}>
              <View style={{ flex: 1 }}>
                <Text style={s.ref}>{item.ref} · {fmtDate(item.earned_at).slice(0, 10)}</Text>
                <Text style={s.addr} numberOfLines={1}>{item.address}</Text>
              </View>
              <View style={{ alignItems: "flex-end", gap: 4 }}>
                <Text style={s.amt}>{money(item.amount, item.currency)}</Text>
                <Badge status={item.status} />
              </View>
            </View>
          )}
          ListEmptyComponent={<Empty icon="credit-card" text="No completed jobs yet" />}
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  list: { padding: space.lg },
  tiles: { flexDirection: "row", flexWrap: "wrap", marginBottom: space.lg, borderLeftWidth: BORDER, borderTopWidth: BORDER, borderColor: c.border },
  tile: { flexBasis: "50%", flexGrow: 1, padding: space.lg, borderRightWidth: BORDER, borderBottomWidth: BORDER, borderColor: c.border, gap: 4 },
  tileBrand: { backgroundColor: c.brandPrimary },
  tLabel: { fontFamily: fonts.mono, fontSize: 11, color: c.onSurface, textTransform: "uppercase" },
  tVal: { fontFamily: fonts.display, fontWeight: "900", fontSize: 24, color: c.onSurface },
  tValSm: { fontFamily: fonts.mono, fontSize: 14, color: c.onSurface },
  row: { flexDirection: "row", alignItems: "center", gap: space.md, borderWidth: BORDER, borderColor: c.border, padding: space.md, marginBottom: -BORDER },
  ref: { fontFamily: fonts.mono, fontSize: 12, color: c.muted },
  addr: { fontFamily: fonts.display, fontWeight: "700", fontSize: 15, color: c.onSurface },
  amt: { fontFamily: fonts.mono, fontSize: 14, color: c.onSurface },
}));
