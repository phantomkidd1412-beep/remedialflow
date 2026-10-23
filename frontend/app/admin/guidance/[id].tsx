import { useEffect, useState } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { Image } from "expo-image";
import * as ImagePicker from "expo-image-picker";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { absUrl, api, authed, BASE, readError } from "@/src/api";
import { uploadForm } from "@/src/utils/upload";
import { Btn, ErrorBlock, Feather, Field, Label, Loading, fmtDate, useToast } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";
import type { Guidance } from "./index";
import { G_STATUS } from "./index";

const FIELDS: [keyof Guidance, string, string][] = [
  ["title", "NC category / defect type", "e.g. EWI thermal bridging around a bracket"],
  ["measure", "Measure", "e.g. EWI, loft insulation, ventilation, heating, condensate"],
  ["defect", "Description of the defect", "e.g. Insulation continuity around an external wall bracket"],
  ["evidence_required", "Acceptable remediation method / evidence requirements", ""],
  ["photos_to_collect", "Photographs that should be collected", "One per line"],
  ["photo_details", "Details that must be visible in photographs", ""],
  ["video_guidance", "When video evidence is useful or necessary", ""],
  ["insufficient_examples", "Common examples of insufficient evidence", ""],
  ["request_example", "Example request to the installer when evidence is insufficient", ""],
  ["references", "Technical references / guidance documents (optional)", "Only real, named references"],
  ["review_notes", "When employee review is required", ""],
];
type Form = Record<string, string>;
const EMPTY: Form = Object.fromEntries([...FIELDS.map(([k]) => [k, ""]), ["keywords", ""]]);

export default function GuidanceEdit() {
  const s = useStyles();
  const { colors } = useTheme();
  const { id } = useLocalSearchParams<{ id: string }>();
  const isNew = id === "new";
  const router = useRouter();
  const qc = useQueryClient();
  const toast = useToast();
  const insets = useSafeAreaInsets();
  const q = useQuery({ queryKey: ["guidance-item", id], queryFn: () => api<Guidance>(`/admin/guidance/${id}`), enabled: !isNew });
  const [form, setForm] = useState<Form>(EMPTY);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const [armed, setArmed] = useState(false);
  const g = q.data;

  useEffect(() => {
    if (g) setForm({ ...Object.fromEntries(FIELDS.map(([k]) => [k, String(g[k] ?? "")])), keywords: g.keywords.join(", ") });
  }, [g]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["guidance"] });
    qc.invalidateQueries({ queryKey: ["guidance-item", id] });
  };
  const body = () => ({ ...form, keywords: form.keywords.split(",").map((k) => k.trim()).filter(Boolean) });

  const save = async () => {
    setBusy("save");
    setErr("");
    try {
      if (isNew) {
        const r = await api<{ id: string }>("/admin/guidance", { method: "POST", body: body() });
        qc.invalidateQueries({ queryKey: ["guidance"] });
        toast("Draft saved. Approve it before the AI uses it");
        router.replace(`/admin/guidance/${r.id}`);
      } else {
        const r = await api<{ version: number }>(`/admin/guidance/${id}`, { method: "PUT", body: body() });
        refresh();
        toast(`Saved · version ${r.version}`);
      }
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  };
  const setStatus = async (status: string) => {
    setBusy(status);
    try {
      await api(`/admin/guidance/${id}/status`, { method: "POST", body: { status } });
      refresh();
      toast(status === "approved" ? "Approved · AI will use this entry" : status === "disabled" ? "Disabled · AI will no longer use it" : "Moved back to draft");
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(null);
    }
  };
  const remove = async () => {
    if (!armed) return setArmed(true);
    setBusy("delete");
    try {
      await api(`/admin/guidance/${id}`, { method: "DELETE" });
      qc.invalidateQueries({ queryKey: ["guidance"] });
      toast("Guidance entry deleted");
      router.back();
    } catch (e: any) {
      toast(e.message);
      setBusy(null);
    }
  };
  const addPhotos = async () => {
    const r = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ["images"], quality: 0.9, allowsMultipleSelection: true, selectionLimit: 6 });
    if (r.canceled) return;
    setBusy("photo");
    try {
      for (const a of r.assets) {
        const res = await authed((t) => uploadForm(`${BASE}/api/admin/guidance/${id}/photos`, { headers: t ? { Authorization: `Bearer ${t}` } : {}, fields: {}, files: { file: { uri: a.uri, name: a.fileName ?? "example.jpg", mimeType: a.mimeType ?? "image/jpeg" } } }));
        if (!res.ok) throw new Error(await readError(res));
      }
      refresh();
      toast("Example photo(s) added");
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(null);
    }
  };
  const removePhoto = async (fid: string) => {
    try {
      await api(`/admin/guidance/${id}/photos/${fid}`, { method: "DELETE" });
      refresh();
    } catch (e: any) {
      toast(e.message);
    }
  };

  if (!isNew && q.isLoading) return <Loading />;
  if (!isNew && (q.isError || !g))
    return (
      <View style={s.root}>
        <ScreenHeader title="Guidance" back />
        <View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error)?.message || "Not found"} onRetry={() => q.refetch()} /></View>
      </View>
    );

  return (
    <View style={s.root}>
      <ScreenHeader title={isNew ? "New guidance" : "Edit guidance"} back />
      <KeyboardAwareScrollView bottomOffset={24} contentContainerStyle={[s.content, { paddingBottom: insets.bottom + space.xl }]} keyboardShouldPersistTaps="handled">
        {g ? (
          <View style={s.info} testID="guidance-info">
            <Text style={s.status} testID="guidance-current-status">{G_STATUS[g.status]} · v{g.version}</Text>
            <Text style={s.meta}>Created {fmtDate(g.created_at)}{g.created_by_name ? ` by ${g.created_by_name}` : ""}{g.source?.type === "employee_feedback" ? ` · suggested from an employee ${g.source.action?.replace("_", " ")} decision` : ""}</Text>
            {g.approved_at ? <Text style={s.meta}>Approved {fmtDate(g.approved_at)}{g.approved_by_name ? ` by ${g.approved_by_name}` : ""}</Text> : null}
            {g.source?.reason ? <Text style={s.meta}>Employee reason: {g.source.reason}</Text> : null}
          </View>
        ) : (
          <Text style={s.meta}>New entries start as drafts. Only approved entries are used by the AI.</Text>
        )}
        {FIELDS.map(([k, label, ph]) => (
          <Field key={k} label={label} placeholder={ph} value={form[k]} onChangeText={(v) => setForm((f) => ({ ...f, [k]: v }))} multiline={k !== "title" && k !== "measure"} testID={`guidance-field-${k}`} />
        ))}
        <Field label="Matching keywords (comma separated)" placeholder="e.g. bracket, thermal bridging, ewi" value={form.keywords} onChangeText={(v) => setForm((f) => ({ ...f, keywords: v }))} autoCapitalize="none" testID="guidance-field-keywords" />
        <Text style={s.meta}>An approved entry is used when one of its keywords appears in the NC wording. With no keywords, the measure is used instead. Up to 3 entries per NC.</Text>
        {err ? <ErrorBlock message={err} /> : null}
        <Btn title={isNew ? "Save draft" : "Save changes"} icon="save" onPress={save} loading={busy === "save"} disabled={!form.title.trim() || !!busy} testID="guidance-save" />

        {g ? (
          <>
            <Label>Example photos of acceptable evidence ({g.example_photos.length})</Label>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space.sm }}>
              {g.example_photos.map((p) => (
                <View key={p.id} style={{ gap: 4 }}>
                  <Image source={{ uri: absUrl(p.url) }} style={s.photo} contentFit="cover" />
                  <Pressable onPress={() => removePhoto(p.id)} style={s.remove} testID={`guidance-photo-remove-${p.id}`}><Text style={s.meta}>REMOVE</Text></Pressable>
                </View>
              ))}
              <Pressable style={[s.photo, s.add]} onPress={addPhotos} disabled={!!busy} testID="guidance-photo-add">
                <Feather name="plus" size={20} color={colors.onSurface} />
              </Pressable>
            </ScrollView>
            <Text style={s.meta}>Photos are re-saved without location data. Do not use photos that show customer details.</Text>

            <Label>Status</Label>
            {g.status !== "approved" ? <Btn title="Approve for AI use" icon="check" variant="success" onPress={() => setStatus("approved")} loading={busy === "approved"} disabled={!!busy} testID="guidance-approve" /> : null}
            {g.status !== "disabled" ? <Btn title="Disable now" icon="slash" variant="outline" onPress={() => setStatus("disabled")} loading={busy === "disabled"} disabled={!!busy} testID="guidance-disable" /> : null}
            {g.status === "disabled" ? <Btn title="Move back to draft" icon="edit-3" variant="outline" onPress={() => setStatus("draft")} loading={busy === "draft"} disabled={!!busy} testID="guidance-redraft" /> : null}
            <Btn title={armed ? "Tap again to delete" : "Delete entry"} icon="trash-2" variant={armed ? "danger" : "outline"} onPress={remove} loading={busy === "delete"} disabled={!!busy && busy !== "delete"} testID="guidance-delete" />
            <Text style={s.meta}>Deleted entries stop being used straight away. Their record stays so past AI decisions can still be audited.</Text>

            {g.history?.length ? (
              <>
                <Label>Version history</Label>
                {g.history.map((h, i) => (
                  <Text key={i} style={s.meta}>v{h.version} · {h.change} · {fmtDate(h.at)}{h.by_name ? ` · ${h.by_name}` : ""}</Text>
                ))}
              </>
            ) : null}
          </>
        ) : null}
      </KeyboardAwareScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  content: { padding: space.lg, gap: space.md },
  info: { borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, padding: space.md, gap: 4 },
  status: { fontFamily: fonts.text, fontSize: 13, fontWeight: "700", color: c.onSurface },
  meta: { fontSize: 12, color: c.muted },
  photo: { width: 88, height: 88, borderRadius: radius.md, borderWidth: BORDER, borderColor: c.border },
  add: { borderStyle: "dashed", alignItems: "center", justifyContent: "center" },
  remove: { minHeight: 32, alignItems: "center", justifyContent: "center", borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border },
}));
