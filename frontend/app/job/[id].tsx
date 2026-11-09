import { useCallback, useState } from "react";
import { FlatList, RefreshControl, Text, View } from "react-native";
import { useLocalSearchParams } from "expo-router";
import { useProject, DONE, installerState } from "@/src/projects";
import { useQueue } from "@/src/queue";
import { ErrorBlock, Loading, Progress, Badge } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { NCCard } from "@/src/components/NCCard";
import { SyncBar } from "@/src/components/SyncBar";
import { fonts, makeStyles, space } from "@/src/theme";

export default function JobDetail() {
  const s = useStyles();
  const { id } = useLocalSearchParams<{ id: string }>();
  const q = useProject(id);
  const { items } = useQueue();
  const [open, setOpen] = useState<string | null | undefined>(undefined);
  const toggle = useCallback((nid: string) => setOpen((o) => (o === nid ? null : nid)), []);

  if (q.isLoading) return <Loading />;
  if (q.isError || !q.data)
    return (
      <View style={s.root}>
        <ScreenHeader title="JOB" back />
        <View style={{ padding: space.lg }}><ErrorBlock message={q.fetchStatus === "paused" ? "Not available offline. Open this job once while connected." : (q.error as Error)?.message || "Failed to load job data"} onRetry={() => q.refetch()} /></View>
      </View>
    );
  const { project: p, ncs } = q.data;
  const firstOpen = open === undefined ? ncs.find((n) => !DONE.includes(n.status))?.id ?? null : open;
  const states = ncs.map((n) => installerState(n).key);
  const attempted = states.filter((k) => k !== "not_started").length;
  const action = states.filter((k) => k === "not_started" || k === "more_evidence").length;

  return (
    <View style={s.root}>
      <ScreenHeader title={p.address} sub={`${p.ref} · ${p.postcode}`} back>
        <View style={s.prog}>
          <View style={{ flex: 1 }}><Progress value={ncs.length ? attempted / ncs.length : 0} testID="job-detail-progress" /></View>
          <Text style={s.progText} testID="job-detail-progress-text">{attempted} of {ncs.length} NCs attempted{action ? ` · ${action} need action` : ""}</Text>
          <Badge status={p.status} />
        </View>
      </ScreenHeader>
      <FlatList
        testID="nc-list"
        data={ncs}
        keyExtractor={(n) => n.id}
        renderItem={({ item }) => (
          <NCCard nc={item} project={p} expanded={firstOpen === item.id} onToggle={toggle} queued={items.filter((i) => i.ncId === item.id)} />
        )}
        contentContainerStyle={s.list}
        refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => { q.refetch(); }} />}
        ListEmptyComponent={<Text style={s.progText}>No NCs present.</Text>}
      />
      <SyncBar projectId={p.id} inTabs={false} />
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  prog: { flexDirection: "row", alignItems: "center", gap: space.md },
  progText: { fontFamily: fonts.text, fontSize: 12, color: c.onSurface },
  list: { padding: space.lg, gap: space.md, paddingBottom: space.xxl },
}));
