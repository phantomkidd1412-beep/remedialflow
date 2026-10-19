import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Text, View } from "react-native";
import * as DocumentPicker from "expo-document-picker";
import { api, authed, BASE, readError } from "@/src/api";
import { uploadForm } from "@/src/utils/upload";
import { Btn, Feather } from "@/src/ui";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

export type PdfNc = { description: string; original_text?: string; required_work: string; safety_critical: boolean; pages: number[]; photo_count?: number };
export type PdfDraft = { ref: string | null; address: string | null; postcode: string | null; nc_count_stated: number | null; ncs: PdfNc[]; confidence: number; notes: string };
export type PhotoStats = { found: number; kept: number; filtered: number };
export type Duplicate = { project_id: string; ref: string; address: string; postcode: string; reason: string };
export type ImportInfo = { photo_stats?: PhotoStats | null; duplicate?: Duplicate | null };

/** Pick an inspection PDF, upload it, and poll the background AI extraction. */
export function PdfImport({ onResult }: { onResult: (draft: PdfDraft, importId: string, filename: string, info: ImportInfo) => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const [phase, setPhase] = useState<"idle" | "uploading" | "reading" | "done" | "failed">("idle");
  const [msg, setMsg] = useState("");
  const [name, setName] = useState("");
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => () => { if (timer.current) clearInterval(timer.current); }, []);

  const pick = async () => {
    const r = await DocumentPicker.getDocumentAsync({ type: "application/pdf", copyToCacheDirectory: true });
    if (r.canceled) return;
    const a = r.assets[0];
    setName(a.name);
    setPhase("uploading");
    setMsg("");
    try {
      const res = await authed((t) =>
        uploadForm(`${BASE}/api/projects/import-pdf`, {
          headers: t ? { Authorization: `Bearer ${t}` } : {},
          files: { file: { uri: a.uri, name: a.name || "report.pdf", mimeType: "application/pdf" } },
        }),
      );
      if (!res.ok) throw new Error(await readError(res));
      const { id } = await res.json();
      setPhase("reading");
      const started = Date.now();
      timer.current = setInterval(async () => {
        try {
          const d = await api<{ status: string; draft?: PdfDraft; error?: string } & ImportInfo>(`/imports/${id}`);
          if (d.status === "ready" && d.draft) {
            clearInterval(timer.current!);
            setPhase("done");
            onResult(d.draft, id, a.name, { photo_stats: d.photo_stats, duplicate: d.duplicate });
          } else if (d.status === "failed" || Date.now() - started > 180000) {
            clearInterval(timer.current!);
            setPhase("failed");
            setMsg(d.error || "Could not read this PDF. Fill the form manually.");
          }
        } catch {}
      }, 2500);
    } catch (e: any) {
      setPhase("failed");
      setMsg(e.message);
    }
  };

  return (
    <View style={[s.box, phase === "failed" && { backgroundColor: colors.error }]} testID="pdf-import-box">
      <View style={s.row}>
        {phase === "uploading" || phase === "reading" ? <ActivityIndicator color={colors.onSurface} /> : <Feather name={phase === "done" ? "check-circle" : "file-text"} size={22} color={phase === "failed" ? colors.onError : colors.onSurface} />}
        <Text style={[s.text, phase === "failed" && { color: colors.onError }]} testID="pdf-import-status">
          {phase === "idle" && "Create from inspection PDF: reference, address and NCs are filled in for you."}
          {phase === "uploading" && `Uploading ${name}…`}
          {phase === "reading" && "Reading report with AI… you can keep editing below."}
          {phase === "done" && `Filled from ${name}. Check the details before creating.`}
          {phase === "failed" && msg}
        </Text>
      </View>
      {phase !== "uploading" && phase !== "reading" ? (
        <Btn title={phase === "idle" ? "Import PDF" : "Choose another PDF"} icon="upload" variant="dark" small onPress={pick} testID="pdf-import-button" />
      ) : null}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  box: { borderWidth: BORDER, borderColor: c.border, backgroundColor: c.brandTertiary, padding: space.md, gap: space.md },
  row: { flexDirection: "row", gap: space.sm, alignItems: "center" },
  text: { flex: 1, fontFamily: fonts.mono, fontSize: 12, color: c.onSurface },
}));
