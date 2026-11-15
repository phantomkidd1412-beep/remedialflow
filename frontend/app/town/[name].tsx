import { useCallback, useMemo } from "react";
import { FlatList, RefreshControl, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useQueue } from "@/src/queue";
import { useJobs } from "@/src/useJobs";
import { townOf, jobStatus } from "@/src/towns";
import { Empty, ErrorBlock, Loading } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { TownProjectCard } from "@/src/components/TownProjectCard";
import { makeStyles, space } from "@/src/theme";

const RANK: Record<string, number> = { "Revisit required": 0, "Not started": 1, "In progress": 2, "In review": 3, Completed: 4 };

/** Projects in one town/city, assigned to the signed-in installer only. */
export default function TownJobs() {
  const s = useStyles();
  const router = useRouter();
  const { name } = useLocalSearchParams<{ name: string }>();
  const { q, rows } = useJobs();
  const { items } = useQueue();
  const open = useCallback((id: string) => router.push(`/job/${id}`), [router]);
  const jobs = useMemo(() => rows.filter((r) => townOf(r.address) === name)
    .sort((a, b) => RANK[jobStatus(a.my_statuses ?? []).label] - RANK[jobStatus(b.my_statuses ?? []).label]), [rows, name]);
  return (
    <View style={s.root}>
      <ScreenHeader title={String(name ?? "")} sub={q.data ? `${jobs.length} job${jobs.length === 1 ? "" : "s"}` : " "} back />
      {q.isLoading ? <Loading /> : q.isError ? (
        <View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error).message} onRetry={() => q.refetch()} /></View>
      ) : (
        <FlatList
          testID="town-jobs-list"
          data={jobs}
          keyExtractor={(p) => p.id}
          renderItem={({ item }) => <TownProjectCard p={item} onOpen={open} queued={items.filter((i) => i.projectId === item.id && i.status !== "uploaded").length} />}
          contentContainerStyle={s.list}
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => { q.refetch(); }} />}
          ListEmptyComponent={<Empty icon="map-pin" text={`No jobs in ${name} right now`} />}
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  list: { padding: space.lg, gap: space.md, paddingBottom: space.xl, width: "100%", maxWidth: 820, alignSelf: "center" },
}));
