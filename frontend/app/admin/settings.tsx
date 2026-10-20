import { useState } from "react";
import { Pressable, Text, View } from "react-native";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { Badge, Btn, Field, Label, Loading, money, useToast } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

type PayGroup = { installer_id: string; installer_name: string; week: string; unpaid: number; paid: number; jobs: { ref: string; amount: number; status: string; overridden?: boolean }[] };
type S = { auto_approve_low_risk: boolean; auto_verify: boolean; confidence_threshold: number; location_policy: string; gallery_policy: string; default_job_rate: number; currency: string; payment_terms: string };

function Choice({ value, options, onChange, testID }: { value: string; options: [string, string][]; onChange: (v: string) => void; testID: string }) {
  const s = useStyles();
  const { colors } = useTheme();
  return (
    <View style={s.choices}>
      {options.map(([v, l]) => (
        <Pressable key={v} onPress={() => onChange(v)} style={[s.choice, value === v && { backgroundColor: colors.brandSecondary }]} testID={`${testID}-${v}`}>
          <Text style={[s.choiceText, value === v && { color: colors.onBrandSecondary }]}>{l}</Text>
        </Pressable>
      ))}
    </View>
  );
}

export default function SettingsScreen() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api<S>("/settings") });
  const earn = useQuery({ queryKey: ["admin-payments"], queryFn: () => api<PayGroup[]>("/admin/payments") });
  const [draft, setF] = useState<S | null>(null);
  const [busy, setBusy] = useState(false);
  const f = draft ?? q.data ?? null;
  if (!f) return <Loading />;
  const set = (p: Partial<S>) => setF({ ...f, ...p });

  const save = async () => {
    setBusy(true);
    try {
      await api("/settings", { method: "PUT", body: { ...f, confidence_threshold: Number(f.confidence_threshold), default_job_rate: Number(f.default_job_rate) } });
      qc.invalidateQueries({ queryKey: ["settings"] });
      toast("Settings saved");
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(false);
    }
  };

  const payWeek = async (g: PayGroup) => {
    const r = await api<{ count: number }>("/admin/payments/mark-paid", { method: "POST", body: { installer_id: g.installer_id, week: g.week } });
    toast(`${r.count} job(s) marked paid for ${g.installer_name}`);
    earn.refetch();
  };
  const groups = earn.data ?? [];

  return (
    <View style={s.root}>
      <ScreenHeader title="RULES & PAYMENTS" back />
      <KeyboardAwareScrollView bottomOffset={24} contentContainerStyle={[s.content, { paddingBottom: insets.bottom + space.xl }]} keyboardShouldPersistTaps="handled">
        <Text style={s.section}>AI ASSESSMENT</Text>
        <Label>AI internal approval of low-risk NCs</Label>
        <Choice value={f.auto_verify && f.auto_approve_low_risk ? "on" : "off"} options={[["on", "On"], ["off", "Off (all to employee)"]]} onChange={(v) => set({ auto_verify: v === "on", auto_approve_low_risk: v === "on" })} testID="setting-auto-verify" />
        <Text style={s.mono}>Safety-critical NCs (gas, electrical, fire, structural, flues) always go to Employee Review.</Text>
        <Field label="Min AI confidence to auto-verify (0–1)" value={String(f.confidence_threshold)} onChangeText={(v) => set({ confidence_threshold: v as any })} keyboardType="decimal-pad" testID="setting-confidence" />

        <Text style={s.section}>EVIDENCE ACCEPTANCE</Text>
        <Label>Photos with Location Not Verified</Label>
        <Choice value={f.location_policy} options={[["accept_flag", "Accept + flag"], ["reject", "Reject"], ["accept", "Accept"]]} onChange={(v) => set({ location_policy: v })} testID="setting-location-policy" />
        <Label>Gallery uploads</Label>
        <Choice value={f.gallery_policy} options={[["manual_review", "Employee review"], ["accept", "Treat normally"]]} onChange={(v) => set({ gallery_policy: v })} testID="setting-gallery-policy" />

        <Text style={s.section}>PAYMENT CONFIGURATION</Text>
        <View style={{ flexDirection: "row", gap: space.sm }}>
          <View style={{ flex: 1 }}><Field label="Default rate per job" value={String(f.default_job_rate)} onChangeText={(v) => set({ default_job_rate: v as any })} keyboardType="decimal-pad" testID="setting-job-rate" /></View>
          <View style={{ width: 100 }}><Field label="Currency" value={f.currency} onChangeText={(v) => set({ currency: v.toUpperCase() })} autoCapitalize="characters" testID="setting-currency" /></View>
        </View>
        <Field label="Payment terms (shown to installers)" value={f.payment_terms} onChangeText={(v) => set({ payment_terms: v })} testID="setting-payment-terms" />
        <Btn title="Save settings" onPress={save} loading={busy} testID="settings-save-button" />

        <Text style={s.section}>PAYMENTS BY INSTALLER & WEEK</Text>
        {groups.length === 0 ? <Text style={s.mono}>No earnings yet.</Text> : null}
        {groups.map((g) => (
          <View key={`${g.installer_id}-${g.week}`} style={s.payRow} testID={`pay-group-${g.installer_name.replace(/\s+/g, "-")}-${g.week}`}>
            <View style={{ flex: 1, gap: 2 }}>
              <Text style={s.payName}>{g.installer_name}</Text>
              <Text style={s.mono}>Week of {g.week.split("-").reverse().join("/")} · {g.jobs.length} job(s)</Text>
              <Text style={s.mono}>{g.jobs.map((j) => `${j.ref} ${money(j.amount)}${j.overridden ? " (rate changed)" : ""}`).join(", ")}</Text>
              <Text style={s.mono}>Unpaid {money(g.unpaid, f.currency)} · Paid {money(g.paid, f.currency)}</Text>
            </View>
            {g.unpaid > 0 ? <Btn title="Mark week paid" small variant="success" onPress={() => payWeek(g)} testID={`pay-week-${g.installer_name.replace(/\s+/g, "-")}-${g.week}`} /> : <Badge status="paid" />}
          </View>
        ))}
      </KeyboardAwareScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  content: { padding: space.lg, gap: space.md },
  section: { fontFamily: fonts.display, fontWeight: "900", fontSize: 16, color: c.onSurface, marginTop: space.md, borderBottomWidth: BORDER, borderColor: c.border, paddingBottom: 4 },
  choices: { flexDirection: "row", borderWidth: BORDER, borderColor: c.border },
  choice: { flex: 1, minHeight: 44, alignItems: "center", justifyContent: "center", paddingHorizontal: 4 },
  choiceText: { fontFamily: fonts.mono, fontSize: 11, color: c.onSurface, textAlign: "center" },
  payRow: { flexDirection: "row", alignItems: "center", gap: space.sm, borderWidth: BORDER, borderColor: c.border, padding: space.md },
  mono: { fontFamily: fonts.mono, fontSize: 12, color: c.onSurface },
  payName: { fontFamily: fonts.display, fontWeight: "800", fontSize: 15, color: c.onSurface },
}));
