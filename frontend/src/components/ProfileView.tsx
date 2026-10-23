import { useState } from "react";
import { Linking, Pressable, ScrollView, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useAuth } from "@/src/auth";
import { api, changePassword } from "@/src/api";
import { SUPPORT_EMAIL } from "@/src/support";
import { useQueue } from "@/src/queue";
import { usesNativeTabs } from "@/src/navigation";
import { Btn, ErrorBlock, Feather, Field, IconName, Label, Sheet, useToast } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, radius, fonts, makeStyles, setThemePref, space, ThemePref, useTheme, useThemePref } from "@/src/theme";

export type MenuItem = { label: string; icon: IconName; onPress: () => void; testID: string };

export function ProfileView({ menu = [] }: { menu?: MenuItem[] }) {
  const s = useStyles();
  const { colors } = useTheme();
  const themePref = useThemePref();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const toast = useToast();
  const { user, signOut, offline } = useAuth();
  const { items, syncNow } = useQueue();
  const [warn, setWarn] = useState(false);
  const [pw, setPw] = useState(false);
  const [cur, setCur] = useState("");
  const [nw, setNw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [del, setDel] = useState(false);
  const [delPw, setDelPw] = useState("");
  const unsynced = items.filter((i) => i.status !== "uploaded");

  const doLogout = async (force = false) => {
    if (unsynced.length && !force) return setWarn(true);
    // unsynced evidence is never deleted on logout: it stays on this phone and uploads at this user's next sign-in
    setWarn(false);
    await signOut();
    router.replace("/login");
  };

  const changePw = async () => {
    setBusy(true);
    setErr("");
    try {
      await changePassword(cur, nw);
      setPw(false);
      setCur("");
      setNw("");
      toast("Password updated");
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const deleteAccount = async () => {
    setBusy(true);
    setErr("");
    try {
      await api("/auth/delete-account", { method: "POST", body: { password: delPw } });
      setDel(false);
      await signOut().catch(() => {});
      router.replace("/login");
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const bottom = usesNativeTabs ? insets.bottom : 0;
  return (
    <View style={s.root}>
      <ScreenHeader title="ACCOUNT" />
      <ScrollView contentContainerStyle={[s.content, { paddingBottom: bottom + space.xl }]}>
        <View style={s.idBox}>
          <Text style={s.name} testID="profile-name">{user?.name}</Text>
          <Text style={s.mono} testID="profile-email">{user?.email}</Text>
          <Text style={s.role} testID="profile-role">{user?.role?.toUpperCase()}{offline ? " · OFFLINE MODE" : ""}</Text>
        </View>
        {unsynced.length ? (
          <Pressable style={s.queueBox} onPress={syncNow} testID="profile-queue-status">
            <Feather name="upload-cloud" size={18} color={colors.onWarning} />
            <Text style={s.queueText}>{unsynced.length} item(s) waiting to sync · tap to retry</Text>
          </Pressable>
        ) : null}
        {menu.length ? <Label>Manage</Label> : null}
        {menu.map((m) => (
          <Pressable key={m.testID} style={s.row} onPress={m.onPress} testID={m.testID}>
            <Feather name={m.icon} size={20} color={colors.onSurface} />
            <Text style={s.rowText}>{m.label}</Text>
            <Feather name="chevron-right" size={20} color={colors.onSurface} />
          </Pressable>
        ))}
        <Label>Appearance</Label>
        <View style={s.seg} testID="theme-setting">
          {(["light", "dark", "auto"] as ThemePref[]).map((t) => (
            <Pressable key={t} onPress={() => setThemePref(t)} style={[s.segBtn, themePref === t && { backgroundColor: colors.brandSecondary }]} testID={`theme-${t}`} accessibilityState={{ selected: themePref === t }}>
              <Feather name={t === "light" ? "sun" : t === "dark" ? "moon" : "smartphone"} size={16} color={themePref === t ? colors.onBrandSecondary : colors.onSurface} />
              <Text style={[s.segText, themePref === t && { color: colors.onBrandSecondary }]}>{t === "auto" ? "Auto" : t === "light" ? "Light" : "Dark"}</Text>
            </Pressable>
          ))}
        </View>
        <Text style={s.hint}>Auto follows your device setting.</Text>
        <Label>Security</Label>
        <Pressable style={s.row} onPress={() => setPw(true)} testID="change-password-button">
          <Feather name="lock" size={20} color={colors.onSurface} />
          <Text style={s.rowText}>Change password</Text>
          <Feather name="chevron-right" size={20} color={colors.onSurface} />
        </Pressable>
        <Label>Help & legal</Label>
        <Pressable style={s.row} onPress={() => Linking.openURL(`mailto:${SUPPORT_EMAIL}?subject=RemedialFlow%20support`).catch(() => toast(`Email us at ${SUPPORT_EMAIL}`))} testID="help-contact-button">
          <Feather name="help-circle" size={20} color={colors.onSurface} />
          <View style={{ flex: 1 }}>
            <Text style={s.rowText}>Help & contact</Text>
            <Text style={s.hint}>{SUPPORT_EMAIL}</Text>
          </View>
          <Feather name="mail" size={20} color={colors.onSurface} />
        </Pressable>
        <Pressable style={s.row} onPress={() => router.push("/privacy")} testID="privacy-policy-button">
          <Feather name="shield" size={20} color={colors.onSurface} />
          <Text style={s.rowText}>Privacy policy</Text>
          <Feather name="chevron-right" size={20} color={colors.onSurface} />
        </Pressable>
        <Pressable style={s.row} onPress={() => setDel(true)} testID="delete-account-button">
          <Feather name="user-x" size={20} color={colors.onSurface} />
          <Text style={s.rowText}>Delete account</Text>
          <Feather name="chevron-right" size={20} color={colors.onSurface} />
        </Pressable>
        <Btn title="Log out" icon="log-out" variant="outline" onPress={() => doLogout()} testID="logout-button" style={{ marginTop: space.lg }} />
      </ScrollView>

      <Sheet visible={warn} onClose={() => setWarn(false)} title="Evidence not uploaded yet" testID="logout-warning-sheet">
        <Text style={s.mono}>{unsynced.length} item(s) on this phone have NOT reached the server. If you log out they stay safely on this phone and upload the next time you sign in, but the office cannot see them until then.</Text>
        <Btn title="Stay and sync now" icon="upload-cloud" onPress={() => { syncNow(); setWarn(false); }} testID="logout-sync-first" />
        <Btn title="Log out anyway (keep evidence)" icon="log-out" variant="outline" onPress={() => doLogout(true)} testID="logout-force-button" />
      </Sheet>

      <Sheet visible={pw} onClose={() => setPw(false)} title="Change password" testID="change-password-sheet">
        <KeyboardAwareScrollView bottomOffset={24} contentContainerStyle={{ gap: space.md }} keyboardShouldPersistTaps="handled">
          <Field label="Current password" secureTextEntry value={cur} onChangeText={setCur} testID="current-password-input" />
          <Field label="New password (min 8)" secureTextEntry value={nw} onChangeText={setNw} testID="new-password-input" />
          {err ? <ErrorBlock message={err} /> : null}
          <Btn title="Update" onPress={changePw} loading={busy} disabled={!cur || nw.length < 8} testID="change-password-submit" />
        </KeyboardAwareScrollView>
      </Sheet>

      <Sheet visible={del} onClose={() => { setDel(false); setErr(""); }} title="Delete account" testID="delete-account-sheet">
        <KeyboardAwareScrollView bottomOffset={24} contentContainerStyle={{ gap: space.md }} keyboardShouldPersistTaps="handled">
          <Text style={s.mono}>This permanently removes your name, email address, password, sign-in sessions and notification devices, and closes your account. It cannot be undone.</Text>
          <Text style={s.mono}>Work evidence you have submitted stays as part of the property&apos;s compliance record, labelled &quot;Deleted user&quot;.</Text>
          {unsynced.length ? <Text style={[s.mono, { color: colors.onWarning }]}>{unsynced.length} item(s) on this phone have not uploaded yet and will not be sent after deletion.</Text> : null}
          <Field label="Enter your password to confirm" secureTextEntry value={delPw} onChangeText={setDelPw} testID="delete-account-password" />
          {err ? <ErrorBlock message={err} /> : null}
          <Btn title="Delete my account permanently" icon="trash-2" variant="danger" onPress={deleteAccount} loading={busy} disabled={!delPw || busy} testID="delete-account-confirm" />
        </KeyboardAwareScrollView>
      </Sheet>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  content: { padding: space.lg, gap: space.sm },
  idBox: { borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, padding: space.lg, gap: 4, backgroundColor: c.surfaceSecondary, marginBottom: space.md },
  name: { fontFamily: fonts.display, fontWeight: "800", fontSize: 22, color: c.onSurface },
  mono: { fontFamily: fonts.text, fontSize: 13, color: c.onSurface },
  role: { fontFamily: fonts.text, fontSize: 12, color: c.onBrandTertiary, marginTop: 4 },
  queueBox: { flexDirection: "row", gap: space.sm, alignItems: "center", backgroundColor: c.warning, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, padding: space.md, marginBottom: space.md },
  queueText: { fontFamily: fonts.text, fontSize: 12, color: c.onWarning, flex: 1 },
  row: { flexDirection: "row", alignItems: "center", gap: space.md, minHeight: 56, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, paddingHorizontal: space.lg, marginBottom: -BORDER },
  rowText: { flex: 1, fontFamily: fonts.display, fontWeight: "700", fontSize: 16, color: c.onSurface },
  seg: { flexDirection: "row", borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border },
  segBtn: { flex: 1, minHeight: 48, flexDirection: "row", gap: 6, alignItems: "center", justifyContent: "center" },
  segText: { fontFamily: fonts.display, fontWeight: "700", fontSize: 14, color: c.onSurface },
  hint: { fontFamily: fonts.text, fontSize: 11, color: c.muted, marginBottom: space.sm },
}));
