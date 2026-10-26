import { useState } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/src/api";
import { Btn, Label, Loading, Sheet, fmtDate, useToast } from "@/src/ui";
import { DOC_CATS, openDoc, ver } from "@/src/components/DocUpload";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

type Passage = { doc_id: string; title: string; publisher: string; version: string; category: string; page: number; clause: string | null; excerpt: string; doc_status: string };
type Refs = { at: string | null; method: "keyword" | "ai" | null; passages: Passage[]; flags: { type: "missing" | "conflict"; text: string }[] };
type PageView = { doc: { title: string; version: string; status: string; file_url: string | null }; page: number; text: string };

/** Staff-only: passages from APPROVED technical documents relevant to this NC, with title, version, clause and page. */
export function ReferencesPanel({ ncId, number }: { ncId: string; number: string }) {
  const s = useStyles();
  const { colors } = useTheme();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["nc-refs", ncId], queryFn: () => api<Refs>(`/ncs/${ncId}/references`), staleTime: 60000 });
  const [busy, setBusy] = useState<string | null>(null);
  const [view, setView] = useState<PageView | null>(null);
  const refresh = async (ai: boolean) => {
    setBusy(ai ? "ai" : "kw");
    try {
      qc.setQueryData(["nc-refs", ncId], await api<Refs>(`/ncs/${ncId}/references/refresh`, { method: "POST", body: { ai } }));
      toast(ai ? "AI picked the most relevant passages" : "References refreshed");
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(null);
    }
  };
  const open = async (p: Passage) => {
    try {
      setView(await api<PageView>(`/documents/${p.doc_id}/pages/${p.page}`));
    } catch (e: any) {
      toast(e.message);
    }
  };
  if (q.isLoading) return <Loading />;
  if (!q.data) return null;
  const r = q.data;
  return (
    <View style={s.box} testID={`refs-${number}`}>
      <Label>Technical references</Label>
      <Text style={s.small}>From approved documents only{r.at ? ` · ${r.method === "ai" ? "AI-selected" : "word match"} · ${fmtDate(r.at)}` : ""}. Check the source before relying on it.</Text>
      {r.flags.map((f, i) => (
        <Text key={i} style={[s.flag, { backgroundColor: f.type === "conflict" ? colors.error : colors.warning, color: f.type === "conflict" ? colors.onError : colors.onWarning }]} testID={`refs-flag-${f.type}-${i}`}>
          {f.type === "conflict" ? "CONFLICT · " : "MISSING · "}{f.text}
        </Text>
      ))}
      {r.passages.map((p, i) => (
        <Pressable key={`${p.doc_id}-${p.page}`} style={s.card} onPress={() => open(p)} testID={`refs-passage-${i}`}>
          <Text style={s.head}>{p.title} · {ver(p.version)} · page {p.page}{p.clause ? ` · clause ${p.clause}` : ""}</Text>
          <Text style={s.small}>{DOC_CATS[p.category] ?? p.category}{p.publisher ? ` · ${p.publisher}` : ""}{p.doc_status !== "approved" ? ` · now ${p.doc_status}` : ""}</Text>
          <Text style={s.excerpt} numberOfLines={4}>“{p.excerpt}”</Text>
          <Text style={[s.small, { color: colors.brandPrimary }]}>View page</Text>
        </Pressable>
      ))}
      <View style={s.row}>
        <Btn title="Refresh" small variant="outline" icon="refresh-cw" onPress={() => refresh(false)} loading={busy === "kw"} style={{ flex: 1 }} testID={`refs-refresh-${number}`} />
        <Btn title="AI search" small variant="outline" icon="search" onPress={() => refresh(true)} loading={busy === "ai"} style={{ flex: 1 }} testID={`refs-ai-${number}`} />
      </View>
      <Text style={s.small}>AI search makes one AI call to pick the most relevant of the matched passages.</Text>
      <Sheet visible={!!view} onClose={() => setView(null)} title={view ? `${view.doc.title} ${ver(view.doc.version)} · page ${view.page}` : ""} testID="refs-page-sheet">
        <ScrollView style={{ maxHeight: 420 }}>
          <Text style={s.pageText} selectable testID="refs-page-text">{view?.text}</Text>
        </ScrollView>
        {view?.doc.file_url ? <Btn title="Open PDF at this page" icon="external-link" small variant="outline" onPress={() => openDoc(view.doc.file_url, view.page)} testID="refs-open-pdf" /> : null}
      </Sheet>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  box: { gap: space.xs, borderTopWidth: BORDER, borderColor: c.border, paddingTop: space.sm },
  small: { fontFamily: fonts.text, fontSize: 11, color: c.muted },
  flag: { fontFamily: fonts.text, fontSize: 11, fontWeight: "700", padding: space.sm, borderRadius: radius.md, overflow: "hidden" },
  card: { borderWidth: BORDER, borderColor: c.border, borderRadius: radius.md, padding: space.sm, gap: 2, minHeight: 44 },
  head: { fontFamily: fonts.text, fontSize: 12, fontWeight: "700", color: c.onSurface },
  excerpt: { fontFamily: fonts.text, fontSize: 12, color: c.onSurface, fontStyle: "italic" },
  row: { flexDirection: "row", gap: space.sm },
  pageText: { fontFamily: fonts.text, fontSize: 12, color: c.onSurface, lineHeight: 18 },
}));
