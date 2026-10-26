import { useEffect, useState } from "react";
import { Pressable, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { Btn, ErrorBlock, Field, Label, Loading, fmtDate, useToast } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { DOC_STATUS, DocMeta, EMPTY_META, MetaFields, TechDoc, openDoc, pickAndUpload, ver } from "@/src/components/DocUpload";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

type Detail = TechDoc & { history: { at: string; change: string; by_name?: string }[]; revisions: { id: string; rev: number; version: string; status: string; created_at: string }[] };
type Hit = { page: number; excerpt: string; clause: string | null };

/** Admin: one technical document - details, approval, replacement (new revision), version history, page search, AI-drafted guidance. */
export default function DocumentDetail() {
  const s = useStyles();
  const { colors } = useTheme();
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const toast = useToast();
  const insets = useSafeAreaInsets();
  const q = useQuery({ queryKey: ["document", id], queryFn: () => api<Detail>(`/admin/documents/${id}`) });
  const d = q.data;
  const [meta, setMeta] = useState<DocMeta>(EMPTY_META);
  const [newVer, setNewVer] = useState<DocMeta>(EMPTY_META);
  const [busy, setBusy] = useState<string | null>(null);
  const [term, setTerm] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [pages, setPages] = useState<number[]>([]);
  const [focus, setFocus] = useState("");
  useEffect(() => { if (d) setMeta({ title: d.title, publisher: d.publisher, version: d.version, pub_date: d.pub_date, category: d.category, notes: d.notes || "" }); }, [d]);

  const refresh = () => { qc.invalidateQueries({ queryKey: ["documents"] }); qc.invalidateQueries({ queryKey: ["document", id] }); };
  const act = async (key: string, fn: () => Promise<any>, ok: string) => {
    setBusy(key);
    try {
      const r = await fn();
      refresh();
      if (r !== null) toast(ok);
      return r;
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(null);
    }
  };
  if (q.isError) return <View style={s.root}><ScreenHeader title="Document" back /><View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error).message} onRetry={() => q.refetch()} /></View></View>;
  if (!d) return <View style={s.root}><ScreenHeader title="Document" back /><Loading /></View>;
  const status = (st: string) => act(st, () => api(`/admin/documents/${id}/status`, { method: "PUT", body: { status: st } }), st === "approved" ? "Approved: now used for NC assessments" : `Marked ${st}`);
  const save = () => act("save", () => api(`/admin/documents/${id}`, { method: "PUT", body: meta }), "Details saved");
  const replace = () => act("replace", async () => {
    const nd = await pickAndUpload(`/admin/documents/${id}/replace`, { ...newVer, title: "", category: "" });
    if (nd) { setNewVer(EMPTY_META); router.replace(`/admin/documents/${nd.id}`); }
    return nd;
  }, "New revision uploaded as Draft");
  const search = () => act("search", async () => { setPages([]); setHits(await api<Hit[]>(`/admin/documents/${id}/search?q=${encodeURIComponent(term)}`)); return null; }, "");
  const draftGuidance = () => act("draft", async () => {
    const r = await api<{ id: string }>(`/admin/documents/${id}/draft-guidance`, { method: "POST", body: { pages, focus } });
    qc.invalidateQueries({ queryKey: ["guidance"] });
    router.push(`/admin/guidance/${r.id}`);
    return r;
  }, "Draft guidance created — review and approve it");
  const toggle = (p: number) => setPages(pages.includes(p) ? pages.filter((x) => x !== p) : pages.length < 6 ? [...pages, p] : pages);

  return (
    <View style={s.root}>
      <ScreenHeader title={d.title} back>
        <Text style={s.meta} testID="doc-detail-status">{DOC_STATUS[d.status]} · {ver(d.version)} · revision {d.rev} · {d.pages} pages ({d.text_pages} with text)</Text>
      </ScreenHeader>
      <KeyboardAwareScrollView bottomOffset={24} contentContainerStyle={[s.content, { paddingBottom: insets.bottom + space.xl }]} keyboardShouldPersistTaps="handled">
        {d.extract_status !== "ok" ? <Text style={[s.small, { color: colors.error }]}>Little readable text was found (possibly a scanned PDF). Text recognition is not supported, so retrieval may miss this document.</Text> : null}
        <View style={s.row}>
          {d.file_url ? <Btn title="Open PDF" icon="external-link" small variant="outline" onPress={() => openDoc(d.file_url)} style={s.act} testID="doc-open" /> : null}
          {d.status !== "approved" ? <Btn title={d.status === "superseded" ? "Re-approve" : "Approve for use"} small onPress={() => status("approved")} loading={busy === "approved"} style={s.act} testID="doc-approve" /> : null}
          {d.status === "approved" ? <Btn title="Back to draft" small variant="outline" onPress={() => status("draft")} loading={busy === "draft"} style={s.act} testID="doc-unapprove" /> : null}
          {d.status !== "retired" ? <Btn title="Retire" small variant="outline" onPress={() => status("retired")} loading={busy === "retired"} style={s.act} testID="doc-retire" /> : null}
        </View>

        <View style={s.box}>
          <Label>Details</Label>
          <MetaFields value={meta} onChange={setMeta} prefix="doc-edit" />
          <Btn title="Save details" small variant="outline" onPress={save} loading={busy === "save"} disabled={!meta.title.trim() || !meta.version.trim()} testID="doc-save" />
        </View>

        <View style={s.box}>
          <Label>Replace with a newer version</Label>
          <Text style={s.small}>Uploads a new revision as Draft. This revision stays in use until you approve the new one; it is then kept as Superseded.</Text>
          <MetaFields value={newVer} onChange={setNewVer} prefix="doc-replace" hideTitle />
          <Btn title="Choose new PDF" icon="upload" small onPress={replace} loading={busy === "replace"} disabled={!newVer.version.trim()} testID="doc-replace-button" />
        </View>

        <View style={s.box}>
          <Label>Draft guidance from this document (AI)</Label>
          <Text style={s.small}>Search for the pages you need, tick up to 6, then draft. One AI call; the entry is a Draft in the AI Guidance Library until approved.</Text>
          <View style={s.row}>
            <View style={s.flex}><Field placeholder="e.g. gas entry tee clearance" value={term} onChangeText={setTerm} onSubmitEditing={search} returnKeyType="search" testID="doc-search-input" /></View>
            <Btn title="Find pages" small variant="outline" onPress={search} loading={busy === "search"} disabled={term.trim().length < 3} testID="doc-search-button" />
          </View>
          {hits?.length === 0 ? <Text style={s.small} testID="doc-search-empty">No pages matched.</Text> : null}
          {(hits ?? []).map((h) => (
            <Pressable key={h.page} onPress={() => toggle(h.page)} style={[s.hit, pages.includes(h.page) && { borderColor: colors.brandPrimary }]} testID={`doc-hit-${h.page}`}>
              <Text style={s.hitHead}>{pages.includes(h.page) ? "☑" : "☐"} Page {h.page}{h.clause ? ` · clause ${h.clause}` : ""}</Text>
              <Text style={s.small} numberOfLines={3}>{h.excerpt}</Text>
            </Pressable>
          ))}
          <Field placeholder="Focus (optional), e.g. evidence of clearance" value={focus} onChangeText={setFocus} maxLength={200} testID="doc-draft-focus" />
          <Btn title={`Draft guidance from ${pages.length ? `page${pages.length > 1 ? "s" : ""} ${[...pages].sort((a, b) => a - b).join(", ")}` : "selected pages"}`} small onPress={draftGuidance} loading={busy === "draft"} disabled={!pages.length} testID="doc-draft-guidance" />
        </View>

        <View style={s.box}>
          <Label>Versions</Label>
          {d.revisions.map((r) => (
            <Pressable key={r.id} onPress={() => r.id !== d.id && router.replace(`/admin/documents/${r.id}`)} style={s.line} testID={`doc-rev-${r.rev}`}>
              <Text style={[s.item, r.id === d.id && { fontWeight: "700" }]}>Revision {r.rev} · {ver(r.version)} · {DOC_STATUS[r.status]} · {fmtDate(r.created_at)}</Text>
            </Pressable>
          ))}
          <Label>History</Label>
          {d.history.map((h, i) => <Text key={i} style={s.small}>{fmtDate(h.at)} · {h.change}{h.by_name ? ` · ${h.by_name}` : ""}</Text>)}
        </View>
      </KeyboardAwareScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  content: { padding: space.lg, gap: space.md },
  meta: { fontFamily: fonts.text, fontSize: 12, color: c.muted },
  small: { fontFamily: fonts.text, fontSize: 11, color: c.muted },
  item: { fontFamily: fonts.text, fontSize: 12, color: c.onSurface },
  row: { flexDirection: "row", flexWrap: "wrap", gap: space.sm, alignItems: "flex-end" },
  flex: { flex: 1 },
  act: { flexGrow: 1, minWidth: 140 },
  box: { gap: space.sm, borderWidth: BORDER, borderColor: c.border, borderRadius: radius.md, padding: space.md },
  hit: { borderWidth: BORDER, borderColor: c.border, borderRadius: radius.md, padding: space.sm, gap: 2, minHeight: 44 },
  hitHead: { fontFamily: fonts.text, fontSize: 12, fontWeight: "700", color: c.onSurface },
  line: { minHeight: 44, justifyContent: "center" },
}));
