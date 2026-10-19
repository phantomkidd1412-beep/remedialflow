import { memo, useCallback } from "react";
import { FlatList, Pressable, RefreshControl, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useQueue } from "@/src/queue";
import { ProjectRow, useProjects } from "@/src/projects";
import { Badge, Btn, Empty, ErrorBlock, Loading, Progress } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { Bell } from "@/src/components/Bell";
import { SyncBar } from "@/src/components/SyncBar";
import { BORDER, fonts, makeStyles, space } from "@/src/theme";

const JobCard = memo(function JobCard({ p, queued, onOpen }: { p: ProjectRow; queued: number; onOpen: (id: string) => void }) {
  const s = useStyles();
  return (
    <Pressable style={s.card} onPress={() => onOpen(p.id)} testID={`job-card-${p.ref}`}>
      <View style={s.cardTop}>
        <Text style={s.ref}>{p.ref}</Text>
        <Badge status={p.status} testID={`job-status-${p.ref}`} />
      </View>
      <Text style={s.addr} numberOfLines={2}>{p.address}</Text>
      <Text style={s.meta}>{p.postcode} · {p.nc_total} NC{p.nc_total === 1 ? "" : "s"} · {p.nc_done}/{p.nc_total} done</Text>
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
  const router = useRouter();
  const q = useProjects();
  const { items } = useQueue();
  const open = useCallback((id: string) => router.push(`/job/${id}`), [router]);
  const data = q.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <View style={s.root}>
      <ScreenHeader title="JOBS" sub={q.data ? `${q.data.pages[0].total} assigned` : " "} right={<Bell />} />
      {q.isLoading ? (
        <Loading />
      ) : q.isError ? (
        <View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error).message} onRetry={() => q.refetch()} /></View>
      ) : (
        <FlatList
          testID="jobs-list"
          data={data}
          keyExtractor={(p) => p.id}
          renderItem={({ item }) => <JobCard p={item} queued={items.filter((i) => i.projectId === item.id).length} onOpen={open} />}
          contentContainerStyle={s.list}
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => q.refetch()} />}
          onEndReached={() => q.hasNextPage && q.fetchNextPage()}
          ListEmptyComponent={<Empty icon="clipboard" text="No active jobs" />}
          initialNumToRender={8}
          windowSize={7}
          removeClippedSubviews
        />
      )}
      <SyncBar />
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  list: { padding: space.lg, gap: space.lg, paddingBottom: space.xl },
  card: { borderWidth: BORDER, borderColor: c.border, backgroundColor: c.surface, padding: space.lg, gap: space.sm },
  cardTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  ref: { fontFamily: fonts.mono, fontSize: 13, color: c.onSurface },
  addr: { fontFamily: fonts.display, fontWeight: "800", fontSize: 20, color: c.onSurface, letterSpacing: -0.3 },
  meta: { fontFamily: fonts.mono, fontSize: 12, color: c.muted },
  cardBottom: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: space.sm },
}));
