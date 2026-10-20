import { memo, useCallback, useMemo } from "react";
import { RefreshControl, SectionList, Pressable, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useQueue } from "@/src/queue";
import { ProjectRow, useProjects } from "@/src/projects";
import { Badge, Btn, Empty, ErrorBlock, Loading, Progress } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { Bell } from "@/src/components/Bell";
import { SyncBar } from "@/src/components/SyncBar";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

type Row = ProjectRow & { my_statuses?: string[] };
type Key = "action" | "new" | "active" | "completed";
const TITLES: Record<Key, string> = { action: "Action required", new: "New", active: "Active", completed: "Completed" };

/** Section from the installer's own NC statuses only. */
function sectionOf(p: Row): Key {
  const st = p.my_statuses ?? [];
  if (st.some((x) => x === "rejected" || x === "more_evidence")) return "action";
  if (st.length && st.every((x) => x === "approved")) return "completed";
  if (st.every((x) => x === "open") && p.nc_done === 0) return "new";
  return "active";
}

const JobCard = memo(function JobCard({ p, queued, onOpen, section }: { p: Row; queued: number; onOpen: (id: string) => void; section: Key }) {
  const s = useStyles();
  const { colors } = useTheme();
  const attention = (p.my_statuses ?? []).filter((x) => x === "rejected" || x === "more_evidence").length;
  return (
    <Pressable style={[s.card, section === "action" && { borderColor: colors.error, borderWidth: 3 }]} onPress={() => onOpen(p.id)} testID={`job-card-${p.ref}`}>
      <View style={s.cardTop}>
        <Text style={s.ref}>{p.ref}</Text>
        {section === "action" ? <Badge status="rejected" label={`${attention} need action`} testID={`job-status-${p.ref}`} /> : <Badge status={section === "completed" ? "approved" : section === "new" ? "open" : "in_progress"} label={TITLES[section]} testID={`job-status-${p.ref}`} />}
      </View>
      <Text style={s.addr}>{p.address}{p.postcode ? `, ${p.postcode}` : ""}</Text>
      <Text style={s.meta}>Your NCs: {p.nc_done}/{p.nc_total} approved</Text>
      <Progress value={p.nc_total ? p.nc_done / p.nc_total : 0} testID={`job-progress-${p.ref}`} />
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
  const sections = useMemo(() => {
    const rows = (q.data?.pages.flatMap((p) => p.items) ?? []) as Row[];
    const by: Record<Key, Row[]> = { action: [], new: [], active: [], completed: [] };
    rows.forEach((r) => by[sectionOf(r)].push(r));
    return (Object.keys(by) as Key[]).filter((k) => by[k].length).map((k) => ({ key: k, title: TITLES[k], data: by[k] }));
  }, [q.data]);

  return (
    <View style={s.root}>
      <ScreenHeader title="JOBS" sub={q.data ? `${q.data.pages[0].total} assigned` : " "} right={<Bell />} />
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
          renderSectionHeader={({ section }) => (
            <View style={[s.sHead, section.key === "action" && { backgroundColor: colors.error }]} testID={`jobs-section-${section.key}`}>
              <Text style={[s.sTitle, section.key === "action" && { color: colors.onError }]}>{section.title.toUpperCase()} · {section.data.length}</Text>
            </View>
          )}
          renderItem={({ item, section }) => <JobCard p={item} section={section.key as Key} queued={items.filter((i) => i.projectId === item.id && i.status !== "uploaded").length} onOpen={open} />}
          contentContainerStyle={s.list}
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => q.refetch()} />}
          onEndReached={() => q.hasNextPage && q.fetchNextPage()}
          ListEmptyComponent={<Empty icon="clipboard" text="No jobs assigned" />}
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
  list: { padding: space.lg, gap: space.md, paddingBottom: space.xl },
  sHead: { backgroundColor: c.surfaceInverse, paddingHorizontal: space.md, paddingVertical: 6, marginTop: space.sm },
  sTitle: { fontFamily: fonts.mono, fontSize: 12, color: c.onSurfaceInverse },
  card: { borderWidth: BORDER, borderColor: c.border, backgroundColor: c.surface, padding: space.lg, gap: space.sm },
  cardTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  ref: { fontFamily: fonts.mono, fontSize: 13, color: c.onSurface },
  addr: { fontFamily: fonts.display, fontWeight: "800", fontSize: 18, lineHeight: 23, color: c.onSurface },
  meta: { fontSize: 13, color: c.muted },
  cardBottom: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: space.sm },
}));
