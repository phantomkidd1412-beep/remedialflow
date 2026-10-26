import { memo, useCallback, useMemo, useState } from "react";
import { RefreshControl, SectionList, Pressable, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useQueue } from "@/src/queue";
import { ProjectRow, useProjects } from "@/src/projects";
import { Badge, Btn, Empty, ErrorBlock, Loading, Progress } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { Bell } from "@/src/components/Bell";
import { SyncBar } from "@/src/components/SyncBar";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

type Row = ProjectRow & { my_statuses?: string[]; my_nc_numbers?: string[]; my_schedule?: string | null };
type Key = "action" | "this_week" | "next_week" | "upcoming" | "unscheduled" | "completed";
type Filter = "all" | "action" | "this_week" | "next_week" | "unscheduled" | "completed";
const TITLES: Record<Key, string> = { action: "Revisit required", this_week: "This week", next_week: "Next week", upcoming: "Upcoming", unscheduled: "Unscheduled", completed: "Completed" };
const ORDER: Key[] = ["action", "this_week", "next_week", "upcoming", "unscheduled", "completed"];
const CARDS: Key[] = ["action", "this_week", "next_week", "unscheduled", "completed"];

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
function weekBounds() {
  const now = new Date();
  const mon = new Date(now.getFullYear(), now.getMonth(), now.getDate() - ((now.getDay() + 6) % 7));
  const add = (n: number) => iso(new Date(mon.getFullYear(), mon.getMonth(), mon.getDate() + n));
  return { nextMon: add(7), afterNext: add(14) };
}

/** Exactly one section per job, from the installer's own NC statuses and their own scheduled date only. */
function sectionOf(p: Row, w: { nextMon: string; afterNext: string }): Key {
  const st = p.my_statuses ?? [];
  if (st.some((x) => x === "rejected" || x === "more_evidence")) return "action";
  if (st.length && st.every((x) => x === "approved")) return "completed";
  const d = p.my_schedule;
  if (!d) return "unscheduled";
  if (d < w.nextMon) return "this_week"; // includes overdue visits from earlier weeks
  if (d < w.afterNext) return "next_week";
  return "upcoming";
}

const JobCard = memo(function JobCard({ p, queued, onOpen, section }: { p: Row; queued: number; onOpen: (id: string) => void; section: Key }) {
  const s = useStyles();
  const { colors } = useTheme();
  const attention = (p.my_statuses ?? []).filter((x) => x === "rejected" || x === "more_evidence").length;
  const overdue = section === "this_week" && !!p.my_schedule && p.my_schedule < iso(new Date());
  const statusLabel = section === "completed" ? "Completed" : (p.my_statuses ?? []).some((x) => x === "assessing" || x === "employee_review" || x === "admin_review") ? "In review" : "To do";
  return (
    <Pressable style={[s.card, section === "action" && { borderColor: colors.onError, borderLeftWidth: 4 }]} onPress={() => onOpen(p.id)} testID={`job-card-${p.ref}`}>
      <View style={s.cardTop}>
        <Text style={s.ref}>{p.ref}</Text>
        {section === "action" ? <Badge status="rejected" label={`${attention} need action`} testID={`job-status-${p.ref}`} /> : <Badge status={section === "completed" ? "approved" : statusLabel === "In review" ? "employee_review" : "in_progress"} label={statusLabel} testID={`job-status-${p.ref}`} />}
      </View>
      <Text style={s.addr}>{p.address}{p.postcode ? `, ${p.postcode}` : ""}</Text>
      <Text style={s.meta} testID={`job-ncs-${p.ref}`}>{(p.my_nc_numbers ?? []).length} NC{(p.my_nc_numbers ?? []).length === 1 ? "" : "s"} assigned ({(p.my_nc_numbers ?? []).join(", ") || "—"}) · {(p.my_statuses ?? []).filter((x) => x === "approved").length}/{(p.my_statuses ?? []).length} done</Text>
      <Text style={[s.meta, overdue && { color: colors.onError, fontWeight: "700" }]} testID={`job-schedule-${p.ref}`}>
        {p.my_schedule ? `Scheduled ${p.my_schedule.split("-").reverse().join("/")}${overdue ? " · overdue" : ""}` : "Not yet scheduled"}
      </Text>
      <Progress value={(p.my_statuses ?? []).length ? (p.my_statuses ?? []).filter((x) => x === "approved").length / (p.my_statuses ?? []).length : 0} testID={`job-progress-${p.ref}`} />
      <View style={s.cardBottom}>
        {queued > 0 ? <Badge status="pending" label={`${queued} unsynced`} /> : <View />}
        <Btn title="Open job" small icon="arrow-right" variant="dark" onPress={() => onOpen(p.id)} testID={`open-job-${p.ref}`} />
      </View>
    </Pressable>
  );
});

export default function Jobs() {
  const s = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const q = useProjects();
  const { items } = useQueue();
  const open = useCallback((id: string) => router.push(`/job/${id}`), [router]);
  const [filter, setFilter] = useState<Filter>("all");
  const { by, sections } = useMemo(() => {
    const rows = (q.data?.pages.flatMap((p) => p.items) ?? []) as Row[];
    const w = weekBounds();
    const by = Object.fromEntries(ORDER.map((k) => [k, [] as Row[]])) as Record<Key, Row[]>;
    rows.forEach((r) => by[sectionOf(r, w)].push(r)); // one project = one card, in exactly one group
    (["this_week", "next_week", "upcoming"] as Key[]).forEach((k) => by[k].sort((a, b) => (a.my_schedule ?? "").localeCompare(b.my_schedule ?? "")));
    const keys = filter === "all" ? ORDER : [filter as Key];
    return { by, sections: keys.filter((k) => by[k].length).map((k) => ({ key: k, title: TITLES[k], data: by[k] })) };
  }, [q.data, filter]);

  const summary = (
    <View style={{ gap: space.sm, marginBottom: space.sm }}>
      <View style={s.cards} testID="jobs-summary">
        {CARDS.map((k) => {
          const on = filter === k;
          const alert = k === "action" && by[k].length > 0;
          return (
            <Pressable key={k} onPress={() => setFilter(on ? "all" : (k as Filter))} style={[s.sCard, alert && { backgroundColor: colors.error }, on && { backgroundColor: colors.brandSecondary, borderColor: colors.brandPrimary }]} testID={`jobs-card-${k}`} accessibilityState={{ selected: on }}>
              <Text style={[s.sVal, alert && !on && { color: colors.onError }, on && { color: colors.onBrandSecondary }]}>{by[k].length}</Text>
              <Text style={[s.sLabel, alert && !on && { color: colors.onError }, on && { color: colors.onBrandSecondary }]} numberOfLines={2}>{TITLES[k]}</Text>
            </Pressable>
          );
        })}
      </View>
      {filter !== "all" ? (
        <Pressable onPress={() => setFilter("all")} style={s.clear} testID="jobs-filter-clear">
          <Text style={s.clearText}>Showing {TITLES[filter as Key].toLowerCase()} · show all</Text>
        </Pressable>
      ) : null}
    </View>
  );

  return (
    <View style={s.root}>
      <ScreenHeader title="Jobs" sub={q.data ? `${q.data.pages[0].total} assigned` : " "} right={<Bell />} />
      {q.isLoading ? (
        <Loading />
      ) : q.isError ? (
        <View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error).message} onRetry={() => q.refetch()} /></View>
      ) : (
        <SectionList
          testID="jobs-list"
          sections={sections}
          keyExtractor={(p) => p.id}
          stickySectionHeadersEnabled={false}
          ListHeaderComponent={summary}
          renderSectionHeader={({ section }) => (
            <View style={[s.sHead, section.key === "action" && { backgroundColor: colors.error }]} testID={`jobs-section-${section.key}`}>
              <Text style={[s.sTitle, section.key === "action" && { color: colors.onError }]}>{section.title} · {section.data.length}</Text>
            </View>
          )}
          renderItem={({ item, section }) => <JobCard p={item} section={section.key as Key} queued={items.filter((i) => i.projectId === item.id && i.status !== "uploaded").length} onOpen={open} />}
          contentContainerStyle={s.list}
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => { q.refetch(); }} />}
          onEndReached={() => q.hasNextPage && q.fetchNextPage()}
          ListEmptyComponent={<Empty icon="clipboard" text={filter === "all" ? "No jobs assigned" : `No ${TITLES[filter as Key].toLowerCase()} jobs`} />}
          initialNumToRender={8}
          windowSize={7}
        />
      )}
      <SyncBar />
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  list: { padding: space.lg, gap: space.md, paddingBottom: space.xl, width: "100%", maxWidth: 820, alignSelf: "center" },
  sHead: { backgroundColor: c.surfaceSecondary, borderRadius: radius.sm, paddingHorizontal: space.md, paddingVertical: 6, marginTop: space.md },
  sTitle: { fontFamily: fonts.text, fontSize: 13, fontWeight: "700", color: c.onSurfaceSecondary },
  card: { borderWidth: BORDER, borderRadius: radius.lg, borderColor: c.border, backgroundColor: c.surface, padding: space.lg, gap: space.sm },
  cardTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  ref: { fontFamily: fonts.text, fontSize: 13, fontWeight: "600", color: c.muted, letterSpacing: 0.3 },
  addr: { fontFamily: fonts.display, fontWeight: "700", fontSize: 18, lineHeight: 24, color: c.onSurface },
  meta: { fontSize: 13, color: c.muted },
  cardBottom: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: space.sm },
  cards: { flexDirection: "row", gap: 6 },
  sCard: { flex: 1, borderWidth: BORDER, borderRadius: radius.lg, borderColor: c.border, backgroundColor: c.surfaceSecondary, paddingVertical: space.sm, paddingHorizontal: 4, minHeight: 64, alignItems: "center", justifyContent: "center", gap: 2 },
  sVal: { fontFamily: fonts.display, fontWeight: "800", fontSize: 20, color: c.onSurface },
  sLabel: { fontFamily: fonts.text, fontSize: 11, fontWeight: "600", color: c.onSurfaceSecondary, textAlign: "center" },
  clear: { minHeight: 36, justifyContent: "center" },
  clearText: { fontFamily: fonts.text, fontSize: 13, color: c.onBrandSecondary, fontWeight: "600" },
}));
