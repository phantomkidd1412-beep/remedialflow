import { RefreshControl, SectionList, Text, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { usesNativeTabs } from "@/src/navigation";
import { Badge, Empty, ErrorBlock, Loading, money, fmtDate } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

type Earn = { id: string; project_id: string; ref: string; address: string; amount: number; currency: string; status: string; earned_at: string; paid_at?: string | null };
type Potential = { project_id: string; ref: string; address: string; amount: number; ncs: string[]; ncs_done: number };
type Resp = { currency: string; payment_terms: string; items: Earn[]; pending: Potential[]; pay_type: string; default_rate?: number | null; earned: number; paid: number; in_progress: number };
type Row = { kind: "potential"; p: Potential } | { kind: "earned" | "paid"; e: Earn };
type Key = "potential" | "earned" | "paid";
const TITLES: Record<Key, string> = { potential: "Potential earnings · estimated", earned: "Completed · payment pending", paid: "Paid jobs" };

/** Installer earnings in three buckets, all driven by the installer's OWN NCs (never whole-project completion). */
export default function Earnings() {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const q = useQuery({ queryKey: ["earnings"], queryFn: () => api<Resp>("/earnings") });
  if (q.isLoading) return <Loading />;
  const d = q.data;
  const daily = d?.pay_type === "daily";
  const sections = d
    ? ([
        { key: "potential" as Key, data: daily ? [] : d.pending.map((p) => ({ kind: "potential", p }) as Row) },
        { key: "earned" as Key, data: d.items.filter((e) => e.status === "earned").map((e) => ({ kind: "earned", e }) as Row) },
        { key: "paid" as Key, data: d.items.filter((e) => e.status === "paid").map((e) => ({ kind: "paid", e }) as Row) },
      ].filter((x) => x.data.length))
    : [];

  return (
    <View style={s.root}>
      <ScreenHeader title="EARNINGS" sub={d?.payment_terms} />
      {q.isError || !d ? (
        <View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error)?.message ?? "Failed"} onRetry={() => q.refetch()} /></View>
      ) : (
        <SectionList
          sections={sections}
          keyExtractor={(r) => (r.kind === "potential" ? `p-${r.p.project_id}` : r.e.id)}
          stickySectionHeadersEnabled={false}
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => q.refetch()} />}
          contentContainerStyle={[s.list, { paddingBottom: (usesNativeTabs ? insets.bottom : 0) + space.xl }]}
          ListHeaderComponent={
            <View style={s.tiles} testID="earnings-summary">
              <View style={s.tile}><Text style={s.tLabel}>Potential (est.)</Text><Text style={s.tVal} testID="earnings-potential">{daily ? "—" : money(d.in_progress, d.currency)}</Text></View>
              <View style={[s.tile, s.tileBrand]}><Text style={[s.tLabel, { color: colors.onBrandPrimary }]}>Payment pending</Text><Text style={[s.tVal, { color: colors.onBrandPrimary }]} testID="earnings-unpaid">{money(d.earned, d.currency)}</Text></View>
              <View style={s.tile}><Text style={s.tLabel}>Total paid</Text><Text style={s.tVal} testID="earnings-paid">{money(d.paid, d.currency)}</Text></View>
              {daily ? <View style={[s.tile, { flexBasis: "100%" }]}><Text style={s.tLabel}>Daily wage · paid outside the app</Text></View> : null}
            </View>
          }
          renderSectionHeader={({ section }) => (
            <View style={s.sHead} testID={`earnings-section-${section.key}`}>
              <Text style={s.sTitle}>{TITLES[section.key as Key].toUpperCase()} · {section.data.length}</Text>
            </View>
          )}
          renderItem={({ item: r }) =>
            r.kind === "potential" ? (
              <View style={s.row} testID={`earning-potential-${r.p.ref}`}>
                <View style={{ flex: 1, gap: 2 }}>
                  <Text style={s.ref}>{r.p.ref}</Text>
                  <Text style={s.addr}>{r.p.address}</Text>
                  <Text style={s.meta}>Your NCs: {r.p.ncs.join(", ")} · {r.p.ncs_done}/{r.p.ncs.length} approved</Text>
                </View>
                <View style={{ alignItems: "flex-end", gap: 4 }}>
                  <Text style={s.amt}>~{money(r.p.amount, d.currency)}</Text>
                  <Badge status="pending" label="Estimated" />
                </View>
              </View>
            ) : (
              <View style={s.row} testID={`earning-row-${r.e.ref}`}>
                <View style={{ flex: 1, gap: 2 }}>
                  <Text style={s.ref}>{r.e.ref}</Text>
                  <Text style={s.addr}>{r.e.address}</Text>
                  <Text style={s.meta}>{r.kind === "paid" && r.e.paid_at ? `Paid ${fmtDate(r.e.paid_at).slice(0, 10)}` : `Completed ${fmtDate(r.e.earned_at).slice(0, 10)}`}</Text>
                </View>
                <View style={{ alignItems: "flex-end", gap: 4 }}>
                  <Text style={s.amt}>{money(r.e.amount, r.e.currency)}</Text>
                  <Badge status={r.kind === "paid" ? "paid" : "earned"} label={r.kind === "paid" ? "Paid" : "Payment pending"} />
                </View>
              </View>
            )
          }
          ListEmptyComponent={<Empty icon="credit-card" text="No jobs yet" />}
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  list: { padding: space.lg },
  tiles: { flexDirection: "row", flexWrap: "wrap", marginBottom: space.md, borderLeftWidth: BORDER, borderTopWidth: BORDER, borderColor: c.border },
  tile: { flexBasis: "33%", flexGrow: 1, padding: space.md, borderRightWidth: BORDER, borderBottomWidth: BORDER, borderColor: c.border, gap: 4 },
  tileBrand: { backgroundColor: c.brandPrimary },
  tLabel: { fontFamily: fonts.mono, fontSize: 10, color: c.onSurface, textTransform: "uppercase" },
  tVal: { fontFamily: fonts.display, fontWeight: "900", fontSize: 18, color: c.onSurface },
  sHead: { backgroundColor: c.surfaceInverse, paddingHorizontal: space.md, paddingVertical: 6, marginTop: space.md },
  sTitle: { fontFamily: fonts.mono, fontSize: 12, color: c.onSurfaceInverse },
  row: { flexDirection: "row", alignItems: "center", gap: space.md, borderWidth: BORDER, borderColor: c.border, padding: space.md, marginTop: -BORDER },
  ref: { fontFamily: fonts.mono, fontSize: 12, color: c.muted },
  addr: { fontFamily: fonts.display, fontWeight: "700", fontSize: 15, color: c.onSurface },
  meta: { fontSize: 12, color: c.muted },
  amt: { fontFamily: fonts.mono, fontSize: 14, color: c.onSurface },
}));
