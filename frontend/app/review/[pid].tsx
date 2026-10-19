import { useState } from "react";
import { ScrollView, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { useProject } from "@/src/projects";
import { Empty, ErrorBlock, Loading } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { StaffNCCard } from "@/src/components/StaffNCCard";
import { fonts, makeStyles, space } from "@/src/theme";

/** Reviews one property's pending NCs in order, auto-advancing; returns to the queue when done. */
export default function ReviewProject() {
  const s = useStyles();
  const router = useRouter();
  const qc = useQueryClient();
  const insets = useSafeAreaInsets();
  const { pid } = useLocalSearchParams<{ pid: string }>();
  const pending = useQuery({ queryKey: ["review-group", pid], queryFn: () => api<{ nc_ids: string[] }>(`/review-groups/${pid}`), staleTime: 0 });
  const proj = useProject(pid);
  const [doneIds, setDoneIds] = useState<string[]>([]);

  if (pending.isLoading || proj.isLoading) return <Loading />;
  if (pending.isError || proj.isError || !proj.data)
    return (
      <View style={s.root}>
        <ScreenHeader title="Review" back />
        <View style={{ padding: space.lg }}><ErrorBlock message="Failed to load" onRetry={() => { pending.refetch(); proj.refetch(); }} /></View>
      </View>
    );
  const queue = (pending.data?.nc_ids ?? []).filter((id) => !doneIds.includes(id));
  const current = proj.data.ncs.find((n) => n.id === queue[0]);
  const total = pending.data?.nc_ids.length ?? 0;
  const p = proj.data.project;

  const next = () => {
    const left = queue.slice(1);
    setDoneIds((d) => [...d, queue[0]]);
    if (!left.length) {
      qc.invalidateQueries({ queryKey: ["review-groups"] });
      router.back();
    }
  };

  return (
    <View style={s.root}>
      <ScreenHeader title={p.address} sub={`${p.ref} · ${total ? `${Math.min(doneIds.length + 1, total)} of ${total}` : "no pending NCs"}`} back />
      <ScrollView contentContainerStyle={[s.content, { paddingBottom: insets.bottom + space.xl }]} keyboardShouldPersistTaps="handled">
        {current ? (
          <>
            <Text style={s.hint} testID="review-progress">Pending NC {doneIds.length + 1} of {total}</Text>
            <StaffNCCard key={current.id} nc={current} projectId={pid} expanded onToggle={() => {}} onDone={next} />
          </>
        ) : (
          <Empty icon="check-circle" text="All NCs reviewed" />
        )}
      </ScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  content: { padding: space.lg, gap: space.md },
  hint: { fontFamily: fonts.mono, fontSize: 13, color: c.muted },
}));
