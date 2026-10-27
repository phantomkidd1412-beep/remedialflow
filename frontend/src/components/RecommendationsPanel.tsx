import { useState } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/src/api";
import { EvidenceFile } from "@/src/projects";
import { Btn, Field, Label, Sheet, fmtDate, useToast } from "@/src/ui";
import { Thumb } from "@/src/components/Media";
import { ITEM_STAGE } from "@/src/components/ChecklistPanel";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

type Rec = { item_id: string; title: string; stage: string; measurement: string; file_id: string | null; source: "staff" | "rule" | "ai" | "none";
  reason: string; uncertain: boolean; state: "pending" | "accepted" | "rejected" | "replaced"; flags: string[] };
type Recs = { available: boolean; at: string | null; ai_used: boolean; profile: { name: string; version: number } | null; items: Rec[];
  photos: { id: string; stage: string | null; checklist_item: string | null }[] };
const SRC: Record<string, string> = { staff: "STAFF", rule: "SUGGESTED (tags)", ai: "SUGGESTED (AI)", none: "NO SUGGESTION" };

/** Staff-only photo recommendations per checklist requirement (Knowledge Index Phase 3). Runs only when staff press the button. */
export function RecommendationsPanel({ ncId, number, photos }: { ncId: string; number: string; photos: Record<string, EvidenceFile> }) {
  const s = useStyles();
  const { colors } = useTheme();
  const qc = useQueryClient();
  const toast = useToast();
  const key = ["photo-recs", ncId];
  const q = useQuery({ queryKey: key, queryFn: () => api<Recs>(`/ncs/${ncId}/recommendations`), staleTime: 30000 });
  const [busy, setBusy] = useState<string | null>(null);
  const [replacing, setReplacing] = useState<Rec | null>(null);
  const [pname, setPname] = useState("");
  const call = async (tag: string, path: string, body: any, ok: string) => {
    setBusy(tag);
    try {
      const res = await api<any>(path, { method: body.method ?? "POST", body: body.data });
      if (res?.items) qc.setQueryData(key, res);
      if (body.method === "PUT") qc.invalidateQueries({ queryKey: ["project"] }); // Reject/Replace update the PDF selection
      qc.invalidateQueries({ queryKey: ["checklist", ncId] });
      qc.invalidateQueries({ queryKey: ["evidence-review", ncId] });
      toast(ok);
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(null);
    }
  };
  const r = q.data;
  if (!r?.available) return null;
  const act = (it: Rec, action: string, file_id?: string) =>
    call(`${action}-${it.item_id}`, `/ncs/${ncId}/recommendations/${it.item_id}`, { method: "PUT", data: { action, file_id } },
      action === "accept" ? "Photo marked for this requirement" : action === "reject" ? "Suggestion rejected" : "Photo replaced");
  const decided = r.items.some((i) => i.state !== "pending");
  return (
    <View style={s.box} testID={`recs-${number}`}>
      <Label>Photo recommendations</Label>
      <Text style={s.small}>
        {r.at ? `${r.ai_used ? "Used 1 AI call" : "No AI used"} · ${fmtDate(r.at)}` : "Suggests one photo per requirement. Uses stage tags first; AI only for what is left"}
        {r.profile ? ` · Profile: ${r.profile.name} v${r.profile.version}` : ""}. A suggestion is not proof the requirement is met.
      </Text>
      {r.at ? r.items.map((it) => {
        const f = it.file_id ? photos[it.file_id] : undefined;
        const pending = it.state === "pending" && it.source !== "staff" && !!it.file_id;
        return (
          <View key={it.item_id} style={s.card} testID={`rec-${it.item_id}`}>
            <View style={s.row}>
              {f ? <Thumb f={f} size={64} /> : <View style={[s.empty, { borderColor: colors.border }]}><Text style={s.small}>No photo</Text></View>}
              <View style={{ flex: 1, gap: 2 }}>
                <Text style={s.head}>{ITEM_STAGE[it.stage]} · {it.title}</Text>
                <Text style={[s.tag, it.source === "staff" && { color: colors.onSuccess, backgroundColor: colors.success, alignSelf: "flex-start", paddingHorizontal: 4, borderRadius: radius.sm, overflow: "hidden" }]} testID={`rec-source-${it.item_id}`}>{SRC[it.source]}{it.state !== "pending" ? ` · ${it.state.toUpperCase()}` : ""}</Text>
                {it.reason ? <Text style={s.small}>{it.reason}</Text> : null}
              </View>
            </View>
            {it.flags.map((x, i) => <Text key={i} style={[s.flag, { color: colors.onWarning, backgroundColor: colors.warning }]} testID={`rec-flag-${it.item_id}-${i}`}>{x}</Text>)}
            <View style={s.row}>
              {pending || it.state === "replaced" ? <Btn title={pending ? "Accept" : "Confirm"} small onPress={() => act(it, "accept")} loading={busy === `accept-${it.item_id}`} style={s.flex} testID={`rec-accept-${it.item_id}`} /> : null}
              <Btn title={it.file_id ? "Replace" : "Choose photo"} small variant="outline" onPress={() => setReplacing(it)} style={s.flex} testID={`rec-replace-${it.item_id}`} />
              {pending ? <Btn title="Reject" small variant="outline" onPress={() => act(it, "reject")} loading={busy === `reject-${it.item_id}`} style={s.flex} testID={`rec-reject-${it.item_id}`} /> : null}
            </View>
          </View>
        );
      }) : null}
      <Btn title={r.at ? "Recommend again" : "Recommend photos"} small variant={r.at ? "outline" : "primary"} icon="star"
        onPress={() => call("run", `/ncs/${ncId}/recommendations`, { data: { force: false } }, "Recommendations ready")} loading={busy === "run"} testID={`recs-run-${number}`} />
      {decided ? (
        <View style={s.row}>
          <View style={s.flex}><Field placeholder={r.profile ? `Profile name (default: ${r.profile.name})` : "Profile name"} value={pname} onChangeText={setPname} maxLength={120} testID={`recs-profile-name-${number}`} /></View>
          <Btn title="Propose profile update" small variant="outline" onPress={() => call("propose", `/ncs/${ncId}/recommendations/propose-profile`, { data: { name: pname } }, "Profile update proposed for admin approval")}
            loading={busy === "propose"} disabled={!r.profile && pname.trim().length < 3} testID={`recs-propose-${number}`} />
        </View>
      ) : null}
      <Sheet visible={!!replacing} onClose={() => setReplacing(null)} title={replacing ? `Best photo for: ${replacing.title}` : ""} testID="recs-replace-sheet">
        <Text style={s.small}>A photo already used for another requirement moves to this one.</Text>
        <ScrollView horizontal contentContainerStyle={s.pick}>
          {r.photos.filter((p) => photos[p.id]).map((p) => (
            <View key={p.id} style={{ gap: 2, alignItems: "center" }}>
              <Thumb f={photos[p.id]} size={88} testID={`recs-pick-${p.id}`} onPress={() => { const it = replacing!; setReplacing(null); act(it, "replace", p.id); }} />
              <Text style={s.tag}>{p.stage ? ITEM_STAGE[p.stage] ?? p.stage.toUpperCase() : "STAGE ?"}{p.checklist_item ? " · USED" : ""}</Text>
            </View>
          ))}
        </ScrollView>
      </Sheet>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  box: { gap: space.xs, borderTopWidth: BORDER, borderColor: c.border, paddingTop: space.sm },
  small: { fontFamily: fonts.text, fontSize: 11, color: c.muted },
  card: { borderWidth: BORDER, borderColor: c.border, borderRadius: radius.md, padding: space.sm, gap: space.xs },
  row: { flexDirection: "row", alignItems: "center", gap: space.sm },
  flex: { flex: 1 },
  head: { fontFamily: fonts.text, fontSize: 12, fontWeight: "700", color: c.onSurface },
  tag: { fontFamily: fonts.text, fontSize: 10, fontWeight: "700", color: c.muted },
  flag: { fontFamily: fonts.text, fontSize: 11, padding: space.xs, borderRadius: radius.sm, overflow: "hidden" },
  empty: { width: 64, height: 64, borderWidth: BORDER, borderRadius: radius.md, alignItems: "center", justifyContent: "center" },
  pick: { gap: space.sm, paddingVertical: space.sm },
}));
