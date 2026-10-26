import { useState } from "react";
import { Pressable, Text, View } from "react-native";
import * as DocumentPicker from "expo-document-picker";
import * as WebBrowser from "expo-web-browser";
import { absUrl, authed, BASE, readError } from "@/src/api";
import { uploadForm } from "@/src/utils/upload";
import { Btn, Field, Label } from "@/src/ui";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

export const DOC_STATUS: Record<string, string> = { draft: "DRAFT", approved: "APPROVED", superseded: "SUPERSEDED", retired: "RETIRED" };
export const ver = (v: string) => (/^v/i.test(v || "") ? v : `v${v}`);
export const DOC_CATS: Record<string, string> = { pas: "PAS standard", trustmark: "TrustMark", building_regs: "Building Regulations", manufacturer: "Manufacturer", other: "Other" };
export type TechDoc = { id: string; key: string; rev: number; title: string; publisher: string; version: string; pub_date: string; category: string;
  status: "draft" | "approved" | "superseded" | "retired"; pages: number; text_pages: number; extract_status: "ok" | "little_text"; replaces: string | null;
  size: number; created_at: string; approved_at: string | null; notes: string; file_url: string | null };
export type DocMeta = { title: string; publisher: string; version: string; pub_date: string; category: string; notes: string };
export const EMPTY_META: DocMeta = { title: "", publisher: "", version: "", pub_date: "", category: "pas", notes: "" };

export const openDoc = (url: string | null, page?: number) => { if (url) WebBrowser.openBrowserAsync(absUrl(url) + (page ? `#page=${page}` : "")); };

/** Pick a PDF and send it with its details (admin only). Text is extracted on the server; no AI runs on upload. */
export async function pickAndUpload(path: string, meta: Partial<DocMeta>): Promise<TechDoc | null> {
  const r = await DocumentPicker.getDocumentAsync({ type: "application/pdf", copyToCacheDirectory: true });
  if (r.canceled) return null;
  const a = r.assets[0];
  const fields = Object.fromEntries(Object.entries(meta).filter(([, v]) => v !== undefined && v !== "")) as Record<string, string>;
  const res = await authed((t) => uploadForm(`${BASE}/api${path}`, {
    headers: t ? { Authorization: `Bearer ${t}` } : {}, fields,
    files: { file: { uri: a.uri, name: a.name || "document.pdf", mimeType: "application/pdf" } },
  }));
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

/** Title / publisher / version / date / category fields shared by upload and edit. */
export function MetaFields({ value, onChange, prefix, hideTitle }: { value: DocMeta; onChange: (m: DocMeta) => void; prefix: string; hideTitle?: boolean }) {
  const s = useStyles();
  const { colors } = useTheme();
  const set = (k: keyof DocMeta) => (v: string) => onChange({ ...value, [k]: v });
  return (
    <View style={s.gap}>
      {hideTitle ? null : <Field label="Title" placeholder="e.g. PAS 2035:2023 Retrofitting dwellings" value={value.title} onChangeText={set("title")} maxLength={200} testID={`${prefix}-title`} />}
      <View style={s.row}>
        <View style={s.flex}><Field label="Version" placeholder="e.g. 2023 / Rev C" value={value.version} onChangeText={set("version")} maxLength={60} testID={`${prefix}-version`} /></View>
        <View style={s.flex}><Field label="Publication date" placeholder="YYYY-MM-DD" value={value.pub_date} onChangeText={set("pub_date")} maxLength={10} testID={`${prefix}-date`} /></View>
      </View>
      <Field label="Publisher" placeholder="e.g. BSI, TrustMark, manufacturer name" value={value.publisher} onChangeText={set("publisher")} maxLength={120} testID={`${prefix}-publisher`} />
      <Label>Category</Label>
      <View style={s.chips}>
        {Object.entries(DOC_CATS).map(([k, t]) => (
          <Pressable key={k} onPress={() => set("category")(k)} style={[s.chip, value.category === k && { borderColor: colors.brandPrimary, backgroundColor: colors.brandSecondary }]} testID={`${prefix}-cat-${k}`}>
            <Text style={[s.chipText, value.category === k && { color: colors.onBrandSecondary }]}>{t}</Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

export function UploadBox({ onDone }: { onDone: (d: TechDoc) => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const [meta, setMeta] = useState<DocMeta>(EMPTY_META);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const ok = meta.title.trim() && meta.version.trim() && /^(|\d{4}(-\d{2}(-\d{2})?)?)$/.test(meta.pub_date);
  const go = async () => {
    setBusy(true);
    setErr("");
    try {
      const d = await pickAndUpload("/admin/documents", meta);
      if (d) { setMeta(EMPTY_META); onDone(d); }
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <View style={s.box} testID="doc-upload-box">
      <Label>Add a technical document</Label>
      <MetaFields value={meta} onChange={setMeta} prefix="doc-new" />
      {err ? <Text style={[s.err, { backgroundColor: colors.error, color: colors.onError }]} testID="doc-upload-error">{err}</Text> : null}
      <Btn title="Choose PDF and upload" icon="upload" onPress={go} loading={busy} disabled={!ok} testID="doc-upload-button" />
      <Text style={s.small}>Text is read page by page on upload (no AI). New documents start as Draft and are not used until you approve them.</Text>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  box: { gap: space.sm, borderWidth: BORDER, borderColor: c.border, borderRadius: radius.md, padding: space.md },
  gap: { gap: space.sm },
  row: { flexDirection: "row", gap: space.sm },
  flex: { flex: 1 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: space.xs },
  chip: { minHeight: 44, borderWidth: BORDER, borderColor: c.border, borderRadius: radius.md, paddingHorizontal: space.sm, justifyContent: "center" },
  chipText: { fontFamily: fonts.text, fontSize: 12, color: c.onSurface },
  small: { fontFamily: fonts.text, fontSize: 11, color: c.muted },
  err: { fontFamily: fonts.text, fontSize: 12, fontWeight: "700", padding: space.sm, borderRadius: radius.md, overflow: "hidden" },
}));
