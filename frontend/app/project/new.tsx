import { useState } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { Btn, ErrorBlock, Feather, Field, Label } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { Duplicate, ImportInfo, PdfDraft, PdfImport } from "@/src/components/PdfImport";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

type NcDraft = { description: string; original_text?: string; required_work: string; safety_critical: boolean; pages?: number[]; photo_count?: number };

export default function NewProject() {
  const s = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const qc = useQueryClient();
  const insets = useSafeAreaInsets();
  const installers = useQuery({ queryKey: ["installers"], queryFn: () => api<{ id: string; name: string }[]>("/installers") });
  const [ref, setRef] = useState("");
  const [address, setAddress] = useState("");
  const [postcode, setPostcode] = useState("");
  const [rate, setRate] = useState("");
  const [installer, setInstaller] = useState<string | null>(null);
  const [ncs, setNcs] = useState<NcDraft[]>([{ description: "", required_work: "", safety_critical: false }]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [importId, setImportId] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const [summary, setSummary] = useState("");
  const [dup, setDup] = useState<Duplicate | null>(null);
  const [restoring, setRestoring] = useState(false);

  /** Restores only the deleted project matching this import/reference (checked server-side), then continues. */
  const restore = async (body: { import_id?: string; ref?: string }) => {
    setRestoring(true);
    setErr("");
    try {
      const r = await api<{ id: string; ref: string }>("/projects/restore-match", { method: "POST", body });
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      if (body.import_id) router.replace({ pathname: "/project/merge", params: { importId: body.import_id, pid: r.id } });
      else router.replace(`/project/${r.id}`);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setRestoring(false);
    }
  };

  const applyPdf = (d: PdfDraft, id: string, _name: string, info: ImportInfo) => {
    setImportId(id);
    setDup(info.duplicate ?? null);
    const st = info.photo_stats;
    setSummary(`${d.ncs.length} NC${d.ncs.length === 1 ? "" : "s"} detected${d.nc_count_stated ? ` (report states ${d.nc_count_stated})` : ""}${st ? ` · ${st.kept} report photo${st.kept === 1 ? "" : "s"} kept${st.filtered ? ` · ${st.filtered} logo/decoration image${st.filtered === 1 ? "" : "s"} skipped` : ""}` : ""}`);
    if (d.ref) setRef(d.ref);
    if (d.address) setAddress(d.address);
    if (d.postcode) setPostcode(d.postcode);
    if (d.ncs.length) setNcs(d.ncs);
    const warn = [d.nc_count_stated && d.nc_count_stated !== d.ncs.length ? `Report states ${d.nc_count_stated} NCs but ${d.ncs.length} were found.` : "", !d.ref ? "No project reference found." : "", !d.address ? "No address found." : "", d.notes].filter(Boolean);
    setNotes(warn.join(" "));
  };

  const setNc = (i: number, patch: Partial<NcDraft>) => setNcs((l) => l.map((n, j) => (j === i ? { ...n, ...patch } : n)));
  const valid = ref && address && ncs.every((n) => n.description && n.required_work);

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      const r = await api<{ id: string }>("/projects", {
        method: "POST",
        body: { ref: ref.trim(), address: address.trim(), postcode: postcode.trim(), installer_id: installer, rate: rate ? Number(rate) : null, ncs, import_id: importId },
      });
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      router.replace(`/project/${r.id}`);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={s.root}>
      <ScreenHeader title="NEW PROJECT" back />
      <KeyboardAwareScrollView bottomOffset={24} contentContainerStyle={[s.content, { paddingBottom: insets.bottom + space.xl }]} keyboardShouldPersistTaps="handled">
        <PdfImport onResult={applyPdf} />
        {summary ? <Text style={s.summary} testID="pdf-import-summary">{summary}</Text> : null}
        {dup && importId && dup.deleted ? (
          <View style={s.dup} testID="pdf-deleted-card">
            <Text style={s.dupTitle}>This project is in Deleted Projects</Text>
            <Text style={s.dupBody}>{dup.ref} · {dup.address}{dup.postcode ? `, ${dup.postcode}` : ""}</Text>
            <Text style={s.muted}>{dup.reason}. Restoring brings back the original project with all its NCs, evidence, assignments, payments and history, then continues this import as an update.</Text>
            <Btn title="Restore and continue import" icon="rotate-ccw" variant="dark" small onPress={() => restore({ import_id: importId })} loading={restoring} testID="pdf-deleted-restore" />
            <Btn title="Create a new project anyway" variant="outline" small onPress={() => setDup(null)} testID="pdf-duplicate-dismiss" />
          </View>
        ) : dup && importId ? (
          <View style={s.dup} testID="pdf-duplicate-card">
            <Text style={s.dupTitle}>This report looks like an existing project</Text>
            <Text style={s.dupBody}>{dup.ref} · {dup.address}{dup.postcode ? `, ${dup.postcode}` : ""}</Text>
            <Text style={s.muted}>{dup.reason}. Updating keeps all existing NCs, evidence, statuses and history.</Text>
            <Btn title={`Update ${dup.ref}`} icon="git-merge" variant="dark" small onPress={() => router.push({ pathname: "/project/merge", params: { importId, pid: dup.project_id } })} testID="pdf-duplicate-update" />
            <Btn title="Create a new project anyway" variant="outline" small onPress={() => setDup(null)} testID="pdf-duplicate-dismiss" />
          </View>
        ) : null}
        {notes ? <Text style={s.warn} testID="pdf-import-notes">{notes}</Text> : null}
        <Field label="Project reference" value={ref} onChangeText={setRef} placeholder="EF-1030" autoCapitalize="characters" testID="new-project-ref" />
        <Field label="Property address" value={address} onChangeText={setAddress} testID="new-project-address" />
        <View style={{ flexDirection: "row", gap: space.sm }}>
          <View style={{ flex: 1 }}><Field label="Postcode" value={postcode} onChangeText={setPostcode} autoCapitalize="characters" testID="new-project-postcode" /></View>
          <View style={{ flex: 1 }}><Field label="Job rate (blank = default)" value={rate} onChangeText={setRate} keyboardType="decimal-pad" testID="new-project-rate" /></View>
        </View>
        <Label>Assign installer</Label>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space.sm }}>
          {(installers.data ?? []).map((i) => (
            <Pressable key={i.id} onPress={() => setInstaller(installer === i.id ? null : i.id)} style={[s.chip, installer === i.id && { backgroundColor: colors.brandSecondary }]} testID={`new-project-installer-${i.id}`}>
              <Text style={[s.chipText, installer === i.id && { color: colors.onBrandSecondary }]}>{i.name}</Text>
            </Pressable>
          ))}
        </ScrollView>
        {ncs.map((n, i) => (
          <View key={i} style={s.nc}>
            <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
              <Text style={s.ncTitle}>NC-{String(i + 1).padStart(2, "0")}{n.photo_count ? ` · ${n.photo_count} REPORT PHOTO${n.photo_count === 1 ? "" : "S"} → TRUSTMARK` : ""}</Text>
              {ncs.length > 1 ? (
                <Pressable onPress={() => setNcs((l) => l.filter((_, j) => j !== i))} hitSlop={8} testID={`remove-nc-${i}`}>
                  <Feather name="trash-2" size={18} color={colors.onSurface} />
                </Pressable>
              ) : null}
            </View>
            <Field placeholder="NC description" value={n.description} onChangeText={(v) => setNc(i, { description: v })} multiline testID={`nc-${i}-description`} />
            <Field placeholder="Required remedial work" value={n.required_work} onChangeText={(v) => setNc(i, { required_work: v })} multiline testID={`nc-${i}-work`} />
            <Pressable onPress={() => setNc(i, { safety_critical: !n.safety_critical })} style={s.check} testID={`nc-${i}-safety`}>
              <View style={[s.box, n.safety_critical && { backgroundColor: colors.error }]} />
              <Text style={s.mono}>Safety-critical</Text>
            </Pressable>
          </View>
        ))}
        <Btn title="Add another NC" icon="plus" variant="outline" small onPress={() => setNcs((l) => [...l, { description: "", required_work: "", safety_critical: false }])} testID="new-project-add-nc" />
        {err ? <ErrorBlock message={err} /> : null}
        {err.includes("Deleted Projects") ? (
          <Btn title="Restore deleted project" icon="rotate-ccw" variant="dark" onPress={() => restore(importId ? { import_id: importId } : { ref: ref.trim() })} loading={restoring} testID="create-deleted-restore" />
        ) : null}
        <Btn title="Create project" onPress={save} loading={busy} disabled={!valid} testID="new-project-submit" />
        <Text style={s.muted}>{importId ? "Photos found on each NC's report pages are attached as TrustMark reference photos. The PDF is stored with the project." : "TrustMark photos can be added per NC after creation."}</Text>
      </KeyboardAwareScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  content: { padding: space.lg, gap: space.md },
  chip: { height: 36, flexShrink: 0, paddingHorizontal: space.md, borderWidth: BORDER, borderColor: c.border, justifyContent: "center" },
  chipText: { fontFamily: fonts.mono, fontSize: 12, color: c.onSurface },
  nc: { borderWidth: BORDER, borderColor: c.border, padding: space.md, gap: space.sm },
  ncTitle: { fontFamily: fonts.mono, fontSize: 13, color: c.onSurface },
  check: { flexDirection: "row", gap: space.sm, alignItems: "center", minHeight: 44 },
  box: { width: 22, height: 22, borderWidth: BORDER, borderColor: c.border },
  mono: { fontFamily: fonts.mono, fontSize: 12, color: c.onSurface },
  warn: { fontFamily: fonts.mono, fontSize: 12, color: c.onWarning, backgroundColor: c.warning, borderWidth: BORDER, borderColor: c.border, padding: space.md },
  muted: { fontFamily: fonts.mono, fontSize: 11, color: c.muted },
  summary: { fontSize: 14, fontWeight: "600", color: c.onSurface, borderWidth: BORDER, borderColor: c.border, padding: space.md },
  dup: { borderWidth: BORDER, borderColor: c.border, backgroundColor: c.warning, padding: space.md, gap: space.sm },
  dupTitle: { fontFamily: fonts.display, fontWeight: "700", fontSize: 16, color: c.onWarning },
  dupBody: { fontSize: 14, color: c.onWarning },
}));
