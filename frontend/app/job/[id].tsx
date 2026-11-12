import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FlatList, RefreshControl, Text, View } from "react-native";
import { useLocalSearchParams, useNavigation } from "expo-router";
import { useProject, DONE, SIMPLE, Simple, simpleStatus } from "@/src/projects";
import { useQueue } from "@/src/queue";
import { Btn, ErrorBlock, Loading, Progress, Sheet } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { NCCard } from "@/src/components/NCCard";
import { SyncBar } from "@/src/components/SyncBar";
import { BORDER, fonts, makeStyles, radius, space, useTheme } from "@/src/theme";

export default function JobDetail() {
  const s = useStyles();
  const { colors } = useTheme();
  const { id } = useLocalSearchParams<{ id: string }>();
  const navigation = useNavigation();
  const q = useProject(id);
  const { items } = useQueue();
  const [open, setOpen] = useState<string | null | undefined>(undefined);
  const [leave, setLeave] = useState<any>(null);
  const warned = useRef(false);
  const toggle = useCallback((nid: string) => setOpen((o) => (o === nid ? null : nid)), []);

  const ncs = q.data?.ncs;
  const rows = useMemo(() => {
    const list = (ncs ?? []).map((n) => ({ n, st: simpleStatus(n, items.filter((i) => i.ncId === n.id && i.type === "upload").length) as Simple }));
    return list.sort((a, b) => SIMPLE[a.st].rank - SIMPLE[b.st].rank); // outstanding NCs first (stable: NC order kept within a status)
  }, [ncs, items]);
  const count = (...k: Simple[]) => rows.filter((r) => k.includes(r.st)).length;
  const unsubmitted = count("action");
  const outstanding = count("action", "revisit", "not_started");

  // Warn once before leaving with unfinished work. Drafts stay saved; navigation is never blocked.
  useEffect(() => {
    return (navigation as any).addListener("beforeRemove", (e: any) => {
      if (warned.current || !outstanding) return;
      e.preventDefault();
      setLeave(e.data.action);
    });
  }, [navigation, outstanding]);

  if (q.isLoading) return <Loading />;
  if (q.isError || !q.data)
    return (
      <View style={s.root}>
        <ScreenHeader title="JOB" back />
        <View style={{ padding: space.lg }}><ErrorBlock message={q.fetchStatus === "paused" ? "Not available offline. Open this job once while connected." : (q.error as Error)?.message || "Failed to load job data"} onRetry={() => q.refetch()} /></View>
      </View>
    );
  const p = q.data.project;
  const total = rows.length;
  const attempted = count("submitted", "in_review", "approved");
  const firstOpen = open === undefined ? rows.find((r) => !DONE.includes(r.n.status))?.n.id ?? null : open;
  const stats: [string, number, boolean][] = [
    ["Total NCs", total, false], ["Submitted", attempted, false], ["Action required", count("action", "not_started"), count("action", "not_started") > 0],
    ["Revisit", count("revisit"), count("revisit") > 0], ["Approved", count("approved"), false],
  ];

  return (
    <View style={s.root}>
      <ScreenHeader title={p.address} sub={`${p.ref} · ${p.postcode}`} back>
        <View style={s.prog}>
          <View style={{ flex: 1 }}><Progress value={total ? attempted / total : 0} testID="job-detail-progress" /></View>
          <Text style={s.progText} testID="job-detail-progress-text">{attempted} of {total} submitted</Text>
        </View>
      </ScreenHeader>
      <FlatList
        testID="nc-list"
        data={rows}
        keyExtractor={(r) => r.n.id}
        ListHeaderComponent={
          <View style={s.stats} testID="job-detail-stats">
            {stats.map(([l, v, alert]) => (
              <View key={l} style={[s.stat, alert && { backgroundColor: colors.error, borderColor: colors.error }]} testID={`job-stat-${l.toLowerCase().replace(/ /g, "-")}`}>
                <Text style={[s.statVal, alert && { color: colors.onError }]}>{v}</Text>
                <Text style={[s.statLabel, alert && { color: colors.onError }]} numberOfLines={2}>{l}</Text>
              </View>
            ))}
          </View>
        }
        renderItem={({ item }) => (
          <NCCard nc={item.n} project={p} expanded={firstOpen === item.n.id} onToggle={toggle} queued={items.filter((i) => i.ncId === item.n.id)} />
        )}
        contentContainerStyle={s.list}
        refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => { q.refetch(); }} />}
        ListEmptyComponent={<Text style={s.progText}>No NCs present.</Text>}
      />
      <SyncBar projectId={p.id} inTabs={false} />
      <Sheet visible={!!leave} onClose={() => setLeave(null)} title="Unfinished work" testID="leave-warning">
        <Text style={s.warn}>
          {outstanding} NC{outstanding === 1 ? "" : "s"} still need{outstanding === 1 ? "s" : ""} work
          {unsubmitted ? `, and ${unsubmitted} ha${unsubmitted === 1 ? "s" : "ve"} photos that are not submitted yet` : ""}. Everything you added stays saved.
        </Text>
        <Btn title="Stay and finish" variant="dark" onPress={() => setLeave(null)} testID="leave-stay" />
        <Btn title="Leave anyway" variant="outline" onPress={() => { warned.current = true; const a = leave; setLeave(null); navigation.dispatch(a); }} testID="leave-confirm" />
      </Sheet>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  prog: { flexDirection: "row", alignItems: "center", gap: space.md },
  progText: { fontFamily: fonts.text, fontSize: 14, fontWeight: "700", color: c.onSurface },
  list: { padding: space.lg, gap: space.md, paddingBottom: space.xxl },
  stats: { flexDirection: "row", gap: 6, marginBottom: space.xs },
  stat: { flex: 1, borderWidth: BORDER, borderColor: c.border, borderRadius: radius.md, paddingVertical: space.sm, alignItems: "center", gap: 2 },
  statVal: { fontFamily: fonts.display, fontWeight: "800", fontSize: 22, color: c.onSurface },
  statLabel: { fontFamily: fonts.text, fontSize: 11, color: c.muted, textAlign: "center" },
  warn: { fontSize: 15, lineHeight: 21, color: c.onSurface, marginBottom: space.sm },
}));
