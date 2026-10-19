import { useState } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { Btn, ErrorBlock, Label, Loading, useToast } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { PdfNc } from "@/src/components/PdfImport";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

type Item = { index: number; draft: PdfNc; score: number; kind: "match" | "ambiguous" | "new"; nc: { id: string; number: string; original_text: string; status: string } | null };
type Preview = { project: { id: string; ref: string; address: string }; items: Item[]; untouched: { id: string; number: string; description: string }[] };
type Action = "update" | "new" | "skip";

/** Re-imported report → existing project. Clear matches default to Update; ambiguous ones must be chosen. */
export default function MergeImport() {
  const s = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const qc = useQueryClient();
  const toast = useToast();
  const insets = useSafeAreaInsets();
  const { importId, pid } = useLocalSearchParams<{ importId: string; pid: string }>();
  const q = useQuery({ queryKey: ["merge-preview", importId, pid], queryFn: () => api<Preview>(`/imports/${importId}/merge-preview?project_id=${pid}`) });
  const [choice, setChoice] = useState<Record<number, Action>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  if (q.isLoading) return <View style={s.root}><ScreenHeader title="UPDATE PROJECT" back /><Loading /></View>;
  if (q.error || !q.data) return <View style={s.root}><ScreenHeader title="UPDATE PROJECT" back /><ErrorBlock message={(q.error as Error)?.message ?? "Could not load"} onRetry={q.refetch} /></View>;

  const { project, items, untouched } = q.data;
  const pick = (it: Item): Action | undefined => choice[it.index] ?? (it.kind === "match" ? "update" : it.kind === "new" ? "new" : undefined);
  const pending = items.filter((it) => !pick(it)).length;

  const apply = async () => {
    setBusy(true);
    setErr("");
    try {
      const r = await api<{ update: number; new: number; skip: number }>(`/projects/${pid}/merge-import`, {
        method: "POST",
        body: { import_id: importId, items: items.map((it) => ({ index: it.index, action: pick(it), nc_id: pick(it) === "update" ? it.nc?.id : null })) },
      });
      qc.invalidateQueries({ queryKey: ["project", pid] });
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      toast(`${project.ref} updated · ${r.update} updated · ${r.new} added · ${r.skip} skipped`);
      router.replace(`/project/${pid}`);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const opt = (it: Item, a: Action, title: string) => {
    const on = pick(it) === a;
    return (
      <Pressable key={a} onPress={() => setChoice((c) => ({ ...c, [it.index]: a }))} style={[s.opt, on && { backgroundColor: colors.brandSecondary }]} testID={`merge-${it.index}-${a}`}>
        <Text style={[s.optText, on && { color: colors.onBrandSecondary }]}>{title}</Text>
      </Pressable>
    );
  };

  return (
    <View style={s.root}>
      <ScreenHeader title="UPDATE PROJECT" back />
      <ScrollView contentContainerStyle={[s.content, { paddingBottom: insets.bottom + space.xl }]}>
        <Text style={s.title} testID="merge-project">{project.ref}</Text>
        <Text style={s.body}>{project.address}</Text>
        <Text style={s.muted}>
          The updated report has {items.length} NC{items.length === 1 ? "" : "s"}. Existing NCs, evidence, statuses and history are never removed; replaced wording is kept in history.
        </Text>
        {items.map((it) => (
          <View key={it.index} style={[s.card, it.kind === "ambiguous" && !choice[it.index] && { borderColor: colors.warning, borderWidth: 2 }]} testID={`merge-item-${it.index}`}>
            <Text style={s.kind}>
              REPORT NC {it.index + 1} · {it.kind === "match" ? `MATCHES ${it.nc?.number}` : it.kind === "ambiguous" ? `POSSIBLE MATCH ${it.nc?.number} (${Math.round(it.score * 100)}%) · PLEASE CHOOSE` : "NEW"}
            </Text>
            <Label>In the updated report</Label>
            <Text style={s.body}>{it.draft.original_text || it.draft.description}</Text>
            {it.nc ? (
              <>
                <Label>Current {it.nc.number} ({it.nc.status.replace(/_/g, " ")})</Label>
                <Text style={[s.body, { color: colors.muted }]}>{it.nc.original_text}</Text>
              </>
            ) : null}
            <View style={s.opts}>
              {it.nc ? opt(it, "update", `Update ${it.nc.number}`) : null}
              {opt(it, "new", "Add as new NC")}
              {opt(it, "skip", "Skip")}
            </View>
          </View>
        ))}
        {untouched.length ? (
          <View style={s.card}>
            <Label>Kept as they are (not in the updated report)</Label>
            {untouched.map((u) => <Text key={u.id} style={s.body}>{u.number} · {u.description}</Text>)}
          </View>
        ) : null}
        {err ? <ErrorBlock message={err} /> : null}
        <Btn title={pending ? `Choose ${pending} more` : "Apply update"} icon="check" onPress={apply} loading={busy} disabled={pending > 0} testID="merge-apply" />
      </ScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  content: { padding: space.lg, gap: space.md },
  title: { fontFamily: fonts.display, fontWeight: "700", fontSize: 22, color: c.onSurface },
  body: { fontSize: 15, lineHeight: 21, color: c.onSurface },
  muted: { fontSize: 13, color: c.muted },
  card: { borderWidth: BORDER, borderColor: c.border, padding: space.md, gap: space.sm },
  kind: { fontFamily: fonts.mono, fontSize: 12, color: c.onSurface },
  opts: { flexDirection: "row", flexWrap: "wrap", gap: space.sm },
  opt: { minHeight: 44, paddingHorizontal: space.md, borderWidth: BORDER, borderColor: c.border, justifyContent: "center" },
  optText: { fontSize: 14, fontWeight: "600", color: c.onSurface },
}));
