import { useEffect, useState } from "react";
import { ScrollView, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { useProject } from "@/src/projects";
import { Btn, Empty, ErrorBlock, Loading } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { StaffNCCard } from "@/src/components/StaffNCCard";
import { fonts, makeStyles, space } from "@/src/theme";

type Group = { project_id: string; ref: string; address: string };

/** Continuous review: each decision saves and shows the next pending NC; after the last NC of this
 *  property it moves straight on to the next property in the review queue. */
export default function ReviewProject() {
  const s = useStyles();
  const router = useRouter();
  const qc = useQueryClient();
  const insets = useSafeAreaInsets();
  const { pid } = useLocalSearchParams<{ pid: string }>();
  const pending = useQuery({ queryKey: ["review-group", pid], queryFn: () => api<{ nc_ids: string[] }>(`/review-groups/${pid}`), staleTime: 0 });
  const proj = useProject(pid);
  const groups = useQuery({ queryKey: ["review-groups", ""], queryFn: () => api<Group[]>("/review-groups") });
  const [doneIds, setDoneIds] = useState<string[]>([]);
  const [pos, setPos] = useState(0);

  const ids = pending.data?.nc_ids ?? [];
  const nextGroup = (groups.data ?? []).find((g) => g.project_id !== pid);
  // prefetch the next property so moving on is instant
  useEffect(() => {
    if (nextGroup) qc.prefetchQuery({ queryKey: ["project", nextGroup.project_id], queryFn: () => api(`/projects/${nextGroup.project_id}`) });
  }, [nextGroup?.project_id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (pending.isLoading || proj.isLoading) return <Loading />;
  if (pending.isError || proj.isError || !proj.data)
    return (
      <View style={s.root}>
        <ScreenHeader title="Review" back />
        <View style={{ padding: space.lg }}><ErrorBlock message="Failed to load" onRetry={() => { pending.refetch(); proj.refetch(); }} /></View>
      </View>
    );

  const p = proj.data.project;
  // NCs that no longer need review (decided here or elsewhere) are skipped
  const open = ids.filter((id) => !doneIds.includes(id) && ["employee_review", "admin_review"].includes(proj.data!.ncs.find((n) => n.id === id)?.status ?? ""));
  const idx = Math.min(pos, Math.max(open.length - 1, 0));
  const current = proj.data.ncs.find((n) => n.id === open[idx]);
  const total = ids.length;
  const reviewed = total - open.length;

  const goNextProperty = () => {
    qc.invalidateQueries({ queryKey: ["review-groups"] });
    if (nextGroup) router.replace(`/review/${nextGroup.project_id}`);
  };
  // decision saved (StaffNCCard only calls onDone on success): drop it and stay at the same position = next NC
  const decided = () => {
    setDoneIds((d) => [...d, open[idx]]);
    if (open.length === 1) goNextProperty();
  };

  return (
    <View style={s.root}>
      <ScreenHeader title={p.address} sub={`${p.ref} · ${open.length ? `NC ${reviewed + 1} of ${total}` : "reviews complete"}`} back />
      <ScrollView contentContainerStyle={[s.content, { paddingBottom: insets.bottom + space.xl }]} keyboardShouldPersistTaps="handled">
        {current ? (
          <>
            <View style={s.nav}>
              <Btn title="Previous" icon="chevron-left" variant="outline" small disabled={idx === 0} onPress={() => setPos(idx - 1)} testID="review-prev" />
              <Text style={s.hint} testID="review-progress">NC {reviewed + 1} of {total}{open.length > 1 ? ` · ${open.length} left` : ""}</Text>
              <Btn title="Next" icon="chevron-right" variant="outline" small disabled={idx >= open.length - 1} onPress={() => setPos(idx + 1)} testID="review-next" />
            </View>
            <StaffNCCard key={current.id} nc={current} projectId={pid} expanded onToggle={() => {}} onDone={decided} />
          </>
        ) : (
          <View style={{ gap: space.lg }}>
            <Empty icon="check-circle" text={nextGroup ? "This property is done. Opening the next one…" : "All reviews completed"} />
            {!nextGroup ? <Btn title="Back to review queue" variant="outline" onPress={() => router.back()} testID="review-done-back" /> : null}
          </View>
        )}
      </ScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  content: { padding: space.lg, gap: space.md, width: "100%", maxWidth: 960, alignSelf: "center" },
  nav: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: space.sm },
  hint: { fontFamily: fonts.text, fontSize: 14, fontWeight: "600", color: c.onSurfaceSecondary },
}));
