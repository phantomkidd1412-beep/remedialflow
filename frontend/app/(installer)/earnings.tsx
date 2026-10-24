import { useState } from "react";
import { FlatList, Pressable, RefreshControl, Text, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { usesNativeTabs } from "@/src/navigation";
import { Badge, Empty, ErrorBlock, Loading, money, fmtDate } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, radius, fonts, makeStyles, space } from "@/src/theme";

type Earn = { id: string; project_id: string; ref: string; address: string; amount: number; currency: string; status: string; earned_at: string; paid_at?: string | null; ncs?: string[] };
type Potential = { project_id: string; ref: string; address: string; amount: number; ncs: string[]; ncs_done: number };
type Resp = { currency: string; payment_terms: string; items: Earn[]; pending: Potential[]; pay_type: string; earned: number; paid: number; in_progress: number };
type Tab = "potential" | "pending" | "paid";
type Row = { key: string; ref: string; address: string; ncs: string[]; amount: number; badge: { status: string; label: string }; note: string };

/** Installer earnings: three summary cards + three tabs. Figures come straight from the existing /earnings
 *  calculation, which is based only on this installer's own NCs. */
export default function Earnings() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const [tab, setTab] = useState<Tab>("potential");
  const q = useQuery({ queryKey: ["earnings"], queryFn: () => api<Resp>("/earnings") });
  if (q.isLoading) return <Loading />;
  const d = q.data;
  const daily = d?.pay_type === "daily";

  const rows: Record<Tab, Row[]> = { potential: [], pending: [], paid: [] };
  if (d) {
    if (!daily)
      rows.potential = d.pending.map((p) => ({
        key: `p-${p.project_id}`, ref: p.ref, address: p.address, ncs: p.ncs, amount: p.amount,
        badge: { status: "pending", label: "Estimated" }, note: `${p.ncs_done} of ${p.ncs.length} of your NCs approved · not yet earned`,
      }));
    rows.pending = d.items.filter((e) => e.status === "earned").map((e) => ({
      key: e.id, ref: e.ref, address: e.address, ncs: e.ncs ?? [], amount: e.amount,
      badge: { status: "earned", label: "Payment pending" }, note: `Accepted ${fmtDate(e.earned_at).slice(0, 10)}`,
    }));
    rows.paid = d.items.filter((e) => e.status === "paid").map((e) => ({
      key: e.id, ref: e.ref, address: e.address, ncs: e.ncs ?? [], amount: e.amount,
      badge: { status: "paid", label: "Paid" }, note: e.paid_at ? `Paid ${fmtDate(e.paid_at).slice(0, 10)}` : "Paid",
    }));
  }
  const cards: { k: Tab; label: string; value: string }[] = d
    ? [
        { k: "potential", label: "Potential earnings", value: daily ? "—" : money(d.in_progress, d.currency) },
        { k: "pending", label: "Pending payment", value: money(d.earned, d.currency) },
        { k: "paid", label: "Total paid", value: money(d.paid, d.currency) },
      ]
    : [];
  const EMPTY: Record<Tab, string> = {
    potential: daily ? "Daily wage · paid outside the app" : "No assigned work awaiting completion",
    pending: "Nothing awaiting payment",
    paid: "No payments recorded yet",
  };

  return (
    <View style={s.root}>
      <ScreenHeader title="EARNINGS" sub={d?.payment_terms} />
      {q.isError || !d ? (
        <View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error)?.message ?? "Failed"} onRetry={() => q.refetch()} /></View>
      ) : (
        <FlatList
          data={rows[tab]}
          keyExtractor={(r) => r.key}
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => q.refetch()} />}
          contentContainerStyle={[s.list, { paddingBottom: (usesNativeTabs ? insets.bottom : 0) + space.xl }]}
          ListHeaderComponent={
            <View style={{ gap: space.md, marginBottom: space.md }}>
              <View style={s.cards} testID="earnings-summary">
                {cards.map((c) => (
                  <Pressable key={c.k} onPress={() => setTab(c.k)} style={[s.card, tab === c.k && s.cardOn]} testID={`earnings-card-${c.k}`}>
                    <Text style={[s.cLabel, tab === c.k && s.onBrand]}>{c.label}</Text>
                    <Text style={[s.cVal, tab === c.k && s.onBrand]} testID={c.k === "potential" ? "earnings-potential" : c.k === "pending" ? "earnings-unpaid" : "earnings-paid"} numberOfLines={1} adjustsFontSizeToFit>{c.value}</Text>
                  </Pressable>
                ))}
              </View>
              <View style={s.tabs}>
                {(["potential", "pending", "paid"] as Tab[]).map((t) => (
                  <Pressable key={t} onPress={() => setTab(t)} style={[s.tab, tab === t && s.tabOn]} testID={`earnings-tab-${t}`}>
                    <Text style={[s.tabText, tab === t && s.tabTextOn]}>{t === "potential" ? "Potential" : t === "pending" ? "Pending" : "Paid"} ({rows[t].length})</Text>
                  </Pressable>
                ))}
              </View>
              {tab === "potential" && !daily ? <Text style={s.hint}>Estimates only. Earned when all of your NCs on a job are approved.</Text> : null}
            </View>
          }
          renderItem={({ item: r }) => (
            <View style={s.row} testID={`earning-row-${tab}-${r.ref}`}>
              <View style={{ flex: 1, gap: 2 }}>
                <Text style={s.addr} numberOfLines={2}>{r.address}</Text>
                <Text style={s.ref}>{r.ref}{r.ncs.length ? ` · ${r.ncs.join(", ")}` : ""}</Text>
                <Text style={s.meta}>{r.note}</Text>
              </View>
              <View style={{ alignItems: "flex-end", gap: 6 }}>
                <Text style={s.amt}>{tab === "potential" ? "~" : ""}{money(r.amount, d.currency)}</Text>
                <Badge status={r.badge.status} label={r.badge.label} />
              </View>
            </View>
          )}
          ListEmptyComponent={<Empty icon="credit-card" text={EMPTY[tab]} />}
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  list: { padding: space.lg, gap: space.sm },
  cards: { flexDirection: "row", gap: space.sm },
  card: { flex: 1, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, padding: space.sm, gap: 4, minHeight: 72, justifyContent: "space-between", backgroundColor: c.surfaceSecondary },
  cardOn: { backgroundColor: c.brandPrimary },
  onBrand: { color: c.onBrandPrimary },
  cLabel: { fontFamily: fonts.text, fontSize: 10, color: c.onSurface, textTransform: "uppercase" },
  cVal: { fontFamily: fonts.display, fontWeight: "800", fontSize: 18, color: c.onSurface },
  tabs: { flexDirection: "row", borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border },
  tab: { flex: 1, minHeight: 44, alignItems: "center", justifyContent: "center" },
  tabOn: { backgroundColor: c.brandSecondary },
  tabText: { fontFamily: fonts.display, fontWeight: "700", fontSize: 13, color: c.onSurface },
  tabTextOn: { color: c.onBrandSecondary },
  hint: { fontFamily: fonts.text, fontSize: 11, color: c.muted },
  row: { flexDirection: "row", alignItems: "center", gap: space.md, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, padding: space.md, backgroundColor: c.surface },
  addr: { fontFamily: fonts.display, fontWeight: "700", fontSize: 15, color: c.onSurface },
  ref: { fontFamily: fonts.text, fontSize: 12, color: c.muted },
  meta: { fontSize: 12, color: c.muted },
  amt: { fontFamily: fonts.text, fontSize: 15, color: c.onSurface },
}));
