import { useState } from "react";
import { Platform, Pressable, ScrollView, Text, View } from "react-native";
import * as WebBrowser from "expo-web-browser";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { Badge, Btn, Feather, Field, Label, Loading, Sheet, money, useToast } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

type PayGroup = { installer_id: string; installer_name: string; week: string; unpaid: number; paid: number; jobs: { ref: string; amount: number; status: string; overridden?: boolean }[] };
type S = { auto_approve_low_risk: boolean; auto_approve_risks: string[]; human_signoff_required: boolean; auto_verify: boolean; confidence_threshold: number; location_policy: string; gallery_policy: string; default_job_rate: number; currency: string; payment_terms: string };

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

type Inst = { id: string; name: string; email: string; role: string; active: boolean; pay_type?: string; default_rate?: number | null };

/** Dropdown of registered installers + that installer's own default per-job rate. Saving only affects jobs not yet
 *  agreed: rates are frozen per project at assignment, and earned/paid amounts never change. */
function InstallerRate({ currency, fallback }: { currency: string; fallback: number }) {
  const s = useStyles();
  const { colors } = useTheme();
  const toast = useToast();
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["admin-users"], queryFn: () => api<Inst[]>("/admin/users") });
  const list = (users.data ?? []).filter((u) => u.role === "installer").sort((a, b) => a.name.localeCompare(b.name));
  const [sel, setSel] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [val, setVal] = useState("");
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const cur = list.find((u) => u.id === sel) ?? null;

  const pick = (u: Inst) => {
    setSel(u.id);
    setVal(u.default_rate != null ? String(u.default_rate) : "");
    setErr("");
    setSaved(false);
    setOpen(false);
  };
  const save = async () => {
    if (!cur) return;
    const t = val.trim().replace(/^£/, "");
    if (!/^\d+(\.\d{1,2})?$/.test(t) || Number(t) > 100000) {
      setErr("Enter a valid amount, e.g. 150 or 150.50");
      return;
    }
    setSaving(true);
    setErr("");
    try {
      await api(`/admin/users/${cur.id}`, { method: "PATCH", body: { default_rate: Number(t) } });
      await qc.invalidateQueries({ queryKey: ["admin-users"] });
      setSaved(true);
      toast(`Rate saved for ${cur.name}`);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <View style={{ gap: space.sm }}>
      <Label>Installer</Label>
      <Pressable onPress={() => setOpen(true)} style={s.dropdown} testID="rate-installer-dropdown">
        <Text style={s.mono}>{cur ? `${cur.name}${cur.active ? "" : " (disabled)"}` : users.isLoading ? "Loading installers…" : "Select an installer"}</Text>
        <Feather name="chevron-down" size={18} color={colors.onSurface} />
      </Pressable>
      {cur ? (
        cur.pay_type === "daily" ? (
          <Text style={s.mono} testID="rate-daily-note">{cur.name} is daily wage, paid outside the app. Change pay type in Users to set a job rate.</Text>
        ) : (
          <>
            <Field label={`Default rate per job (${currency})`} value={val} onChangeText={(t) => { setVal(t); setErr(""); setSaved(false); }} keyboardType="decimal-pad" placeholder={`Uses company default ${money(fallback, currency)}`} testID="installer-rate-input" />
            <Text style={s.mono}>Applies to newly assigned jobs. Already earned or paid amounts and project-specific overrides are not changed.</Text>
            {err ? <Text style={[s.mono, { color: colors.onError }]} testID="installer-rate-error">{err}</Text> : null}
            <Btn title={saved ? "Saved" : "Save rate"} icon={saved ? "check" : "save"} variant={saved ? "success" : "primary"} onPress={save} loading={saving} disabled={saving} testID="installer-rate-save" />
          </>
        )
      ) : null}
      <Sheet visible={open} onClose={() => setOpen(false)} title="Select installer" testID="rate-installer-sheet">
        <ScrollView style={{ maxHeight: 420 }}>
          {list.map((u) => (
            <Pressable key={u.id} onPress={() => pick(u)} style={[s.option, u.id === sel && { backgroundColor: colors.brandSecondary }]} testID={`rate-installer-option-${u.id}`}>
              <Text style={[s.payName, u.id === sel && { color: colors.onBrandSecondary }]}>{u.name}{u.active ? "" : " (disabled)"}</Text>
              <Text style={[s.mono, u.id === sel && { color: colors.onBrandSecondary }]}>{u.pay_type === "daily" ? "Daily wage" : u.default_rate != null ? `${money(u.default_rate, currency)} per job` : "Company default rate"}</Text>
            </Pressable>
          ))}
          {!list.length && !users.isLoading ? <Text style={s.mono}>No installers registered.</Text> : null}
        </ScrollView>
      </Sheet>
    </View>
  );
}

type DbxStatus = { connected: boolean; configured: boolean; connected_at?: string | null; connected_by?: string | null; redirect_uri?: string | null };

/** Admin-only: connect the company Dropbox (OAuth happens on Dropbox's own page; tokens never reach the app). */
function DropboxSection() {
  const s = useStyles();
  const toast = useToast();
  const st = useQuery({ queryKey: ["dropbox-status"], queryFn: () => api<DbxStatus>("/admin/dropbox/status") });
  const [busy, setBusy] = useState(false);
  const connect = async () => {
    setBusy(true);
    try {
      const { authorize_url } = await api<{ authorize_url: string }>("/admin/dropbox/connect", { method: "POST" });
      if (Platform.OS === "web") window.open(authorize_url, "_blank");
      else await WebBrowser.openBrowserAsync(authorize_url);
      st.refetch();
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(false);
    }
  };
  const d = st.data;
  return (
    <View style={{ gap: space.sm }} testID="dropbox-section">
      <Text style={s.mono} testID="dropbox-status">
        {!d ? "Checking…" : d.connected ? `Dropbox connected${d.connected_by ? ` by ${d.connected_by}` : ""}. New photos, videos and PDFs are stored in your Dropbox app folder. Files are never deleted from Dropbox by RemedialFlow.` : "Dropbox not connected. New evidence is stored in the built-in storage until you connect."}
      </Text>
      {d && !d.connected && d.redirect_uri ? (
        <View style={{ gap: 4 }} testID="dropbox-redirect">
          <Text style={s.mono}>Before connecting, add this exact Redirect URI in the Dropbox App Console → Settings → OAuth 2 → Redirect URIs:</Text>
          <Text selectable style={[s.mono, { fontWeight: "700" }]} testID="dropbox-redirect-uri">{d.redirect_uri}</Text>
          {Platform.OS === "web" ? <Btn title="Copy Redirect URI" icon="copy" variant="outline" small onPress={() => navigator.clipboard?.writeText(d.redirect_uri!).then(() => toast("Copied"))} testID="dropbox-copy-uri" /> : null}
        </View>
      ) : null}
      {d?.connected ? <Badge status="approved" label="✓ Dropbox connected" testID="dropbox-connected-badge" /> : null}
      {d ? <Btn title={d.connected ? "Reconnect (only if permissions change)" : "Connect Dropbox"} icon="link" variant={d.connected ? "outline" : "primary"} small={!!d.connected} onPress={connect} loading={busy} disabled={!d.configured} testID="dropbox-connect" /> : null}
      {d ? <Btn title="Refresh status" variant="outline" small onPress={() => st.refetch()} testID="dropbox-refresh" /> : null}
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
        <Choice value={(f.auto_approve_risks ?? []).includes("standard") ? "std" : "low"} options={[["low", "Low-risk NCs only"], ["std", "Low + standard NCs"]]} onChange={(v) => set({ auto_approve_risks: v === "std" ? ["low", "standard"] : ["low"] })} testID="setting-auto-risks" />
        <Choice value={f.human_signoff_required ? "on" : "off"} options={[["off", "AI may approve"], ["on", "AI verifies, employee signs off"]]} onChange={(v) => set({ human_signoff_required: v === "on" })} testID="setting-signoff" />
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

        <Text style={s.section}>EVIDENCE STORAGE</Text>
        <DropboxSection />

        <Text style={s.section}>INSTALLER RATES</Text>
        <InstallerRate currency={f.currency} fallback={Number(f.default_job_rate)} />

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
  section: { fontFamily: fonts.display, fontWeight: "800", fontSize: 16, color: c.onSurface, marginTop: space.md, borderBottomWidth: BORDER, borderColor: c.border, paddingBottom: 4 },
  choices: { flexDirection: "row", borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border },
  choice: { flex: 1, minHeight: 44, alignItems: "center", justifyContent: "center", paddingHorizontal: 4 },
  choiceText: { fontFamily: fonts.text, fontSize: 11, color: c.onSurface, textAlign: "center" },
  payRow: { flexDirection: "row", alignItems: "center", gap: space.sm, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, padding: space.md },
  mono: { fontFamily: fonts.text, fontSize: 12, color: c.onSurface },
  payName: { fontFamily: fonts.display, fontWeight: "800", fontSize: 15, color: c.onSurface },
  dropdown: { minHeight: 48, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, paddingHorizontal: space.md, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  option: { paddingVertical: space.sm, paddingHorizontal: space.md, borderBottomWidth: 1, borderColor: c.border, minHeight: 48, justifyContent: "center" },
}));
