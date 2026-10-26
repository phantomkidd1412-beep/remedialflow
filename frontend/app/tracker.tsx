import { useEffect, useState } from "react";
import { FlatList, Pressable, RefreshControl, ScrollView, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { openTrackerExport } from "@/src/projects";
import { Badge, Btn, Empty, ErrorBlock, Feather, Field, Loading, fmtDate, useToast } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

type Row = { id: string; ref: string; address: string; installers: string[]; nc_total: number; nc_done: number; completed_at: string | null; resolved_at: string | null; status: string; reopened: boolean; days_outstanding: number | null };
type Resp = { items: Row[]; count: number; start: string | null; end: string | null };

const VIEWS = [["completed", "Internally completed"], ["resolved", "TrustMark resolved"]] as const;
const RANGES = [["week", "Week"], ["month", "Month"], ["custom", "Custom"], ["all", "All time"]] as const;
const SORTS = [["newest", "Newest"], ["oldest", "Oldest"], ["outstanding", "Longest outstanding"]] as const;
const iso = (d: Date) => d.toISOString().slice(0, 10);
const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Work Tracker: one row per project, keyed on its first recorded completion / resolution date. */
export default function Tracker() {
  const s = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const toast = useToast();
  const insets = useSafeAreaInsets();
  const [view, setView] = useState<string>("completed");
  const [range, setRange] = useState<string>("week");
  const [anchor, setAnchor] = useState(iso(new Date()));
  const [start, setStart] = useState(iso(new Date(Date.now() - 30 * 864e5)));
  const [end, setEnd] = useState(iso(new Date()));
  const [text, setText] = useState("");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<string>("newest");
  const [exporting, setExporting] = useState<string | null>(null);
  useEffect(() => {
    const t = setTimeout(() => setSearch(text.trim()), 300);
    return () => clearTimeout(t);
  }, [text]);

  const customOk = range !== "custom" || (DATE.test(start) && DATE.test(end));
  const params: Record<string, string> = { view, range, sort, ...(search ? { search } : {}), ...(range === "custom" ? { start, end } : range === "all" ? {} : { anchor }) };
  const q = useQuery({ queryKey: ["tracker", params], queryFn: () => api<Resp>(`/tracker?${new URLSearchParams(params)}`), enabled: customOk });

  const shift = (dir: number) => {
    const d = new Date(anchor + "T12:00:00Z");
    if (range === "week") d.setUTCDate(d.getUTCDate() + 7 * dir);
    else d.setUTCMonth(d.getUTCMonth() + dir, 1);
    setAnchor(iso(d));
  };
  const doExport = async (fmt: "csv" | "xlsx") => {
    setExporting(fmt);
    try {
      await openTrackerExport(fmt, params);
    } catch (e: any) {
      toast(e.message);
    } finally {
      setExporting(null);
    }
  };
  const chips = (opts: readonly (readonly [string, string])[], val: string, set: (v: string) => void, prefix: string) => (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.chips}>
      {opts.map(([v, l]) => (
        <Pressable key={v} onPress={() => set(v)} style={[s.chip, val === v && { backgroundColor: colors.brandSecondary }]} testID={`${prefix}-${v}`}>
          <Text style={[s.chipText, val === v && { color: colors.onBrandSecondary }]}>{l}</Text>
        </Pressable>
      ))}
    </ScrollView>
  );
  const period = q.data?.start ? `${q.data.start.split("-").reverse().join("/")} – ${q.data.end?.split("-").reverse().join("/")}` : "All time";

  const header = (
    <View style={{ gap: space.sm, marginBottom: space.sm }}>
      {chips(VIEWS, view, setView, "tracker-view")}
      {chips(RANGES, range, setRange, "tracker-range")}
      {range === "week" || range === "month" ? (
        <View style={s.period}>
          <Pressable onPress={() => shift(-1)} style={s.arrow} testID="tracker-prev" hitSlop={6}><Feather name="chevron-left" size={20} color={colors.onSurface} /></Pressable>
          <Text style={s.periodText} testID="tracker-period">{period}</Text>
          <Pressable onPress={() => shift(1)} style={s.arrow} testID="tracker-next" hitSlop={6}><Feather name="chevron-right" size={20} color={colors.onSurface} /></Pressable>
        </View>
      ) : null}
      {range === "custom" ? (
        <View style={s.dates}>
          <View style={{ flex: 1 }}><Field label="From (YYYY-MM-DD)" value={start} onChangeText={setStart} autoCapitalize="none" testID="tracker-start" /></View>
          <View style={{ flex: 1 }}><Field label="To (YYYY-MM-DD)" value={end} onChangeText={setEnd} autoCapitalize="none" testID="tracker-end" /></View>
        </View>
      ) : null}
      {!customOk ? <Text style={s.err}>Enter both dates as YYYY-MM-DD</Text> : null}
      <View style={s.search}>
        <Feather name="search" size={18} color={colors.onSurface} />
        <TextInput value={text} onChangeText={setText} placeholder="Reference, address or installer" placeholderTextColor={colors.muted} style={s.searchInput} autoCapitalize="none" testID="tracker-search" />
      </View>
      {chips(SORTS, sort, setSort, "tracker-sort")}
      <View style={s.exportRow}>
        <Text style={s.count} testID="tracker-count">{q.data ? `${q.data.count} project${q.data.count === 1 ? "" : "s"}` : " "}</Text>
        <Btn title="CSV" icon="download" small variant="outline" onPress={() => doExport("csv")} loading={exporting === "csv"} disabled={!customOk || !q.data?.count} testID="tracker-export-csv" />
        <Btn title="Excel" icon="download" small variant="outline" onPress={() => doExport("xlsx")} loading={exporting === "xlsx"} disabled={!customOk || !q.data?.count} testID="tracker-export-xlsx" />
      </View>
    </View>
  );

  return (
    <View style={s.root}>
      <ScreenHeader title="Work tracker" back />
      {q.isError ? (
        <View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error).message} onRetry={() => q.refetch()} /></View>
      ) : (
        <FlatList
          data={q.data?.items ?? []}
          keyExtractor={(r) => r.id}
          ListHeaderComponent={header}
          keyboardShouldPersistTaps="handled"
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => { q.refetch(); }} />}
          contentContainerStyle={[s.list, { paddingBottom: insets.bottom + space.xl }]}
          ListEmptyComponent={q.isLoading ? <Loading /> : <Empty icon="bar-chart-2" text="No projects in this period" />}
          renderItem={({ item: r }) => (
            <Pressable style={s.card} onPress={() => router.push(`/project/${r.id}`)} testID={`tracker-row-${r.ref}`}>
              <View style={s.cardTop}>
                <Text style={s.ref}>{r.ref}</Text>
                <Badge status={r.status} />
              </View>
              <Text style={s.addr}>{r.address}</Text>
              <Text style={s.meta}>{r.installers.length ? r.installers.join(", ") : "No installer"} · {r.nc_done}/{r.nc_total} NCs approved</Text>
              <Text style={s.meta}>
                Completed {r.completed_at ? fmtDate(r.completed_at) : "—"} · Resolved {r.resolved_at ? fmtDate(r.resolved_at) : "—"}
                {r.days_outstanding != null ? ` · ${r.days_outstanding}d ${r.resolved_at ? "to resolve" : "open"}` : ""}
                {r.reopened ? " · Reopened" : ""}
              </Text>
            </Pressable>
          )}
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  list: { padding: space.lg, gap: space.sm },
  chips: { gap: space.sm },
  chip: { minHeight: 40, paddingHorizontal: space.md, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, justifyContent: "center" },
  chipText: { fontSize: 13, fontWeight: "600", color: c.onSurface },
  period: { flexDirection: "row", alignItems: "center", gap: space.sm },
  arrow: { width: 44, height: 44, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, alignItems: "center", justifyContent: "center" },
  periodText: { flex: 1, textAlign: "center", fontSize: 15, fontWeight: "700", color: c.onSurface },
  dates: { flexDirection: "row", gap: space.sm },
  err: { fontSize: 13, color: c.onError },
  search: { flexDirection: "row", alignItems: "center", gap: space.sm, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, paddingHorizontal: space.md, minHeight: 48 },
  searchInput: { flex: 1, fontSize: 15, color: c.onSurface, minHeight: 44 },
  exportRow: { flexDirection: "row", alignItems: "center", gap: space.sm },
  count: { flex: 1, fontSize: 14, fontWeight: "700", color: c.onSurface },
  card: { borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, padding: space.md, gap: 4 },
  cardTop: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: space.sm },
  ref: { fontFamily: fonts.display, fontWeight: "800", fontSize: 16, color: c.onSurface },
  addr: { fontSize: 14, color: c.onSurface },
  meta: { fontSize: 13, color: c.muted },
}));
