import { useState } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useAuth } from "@/src/auth";
import { api } from "@/src/api";
import { useQueue } from "@/src/queue";
import { usesNativeTabs } from "@/src/navigation";
import { Btn, ErrorBlock, Feather, Field, IconName, Label, Sheet, useToast } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

export type MenuItem = { label: string; icon: IconName; onPress: () => void; testID: string };

export function ProfileView({ menu = [] }: { menu?: MenuItem[] }) {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const toast = useToast();
  const { user, signOut, offline } = useAuth();
  const { items, clearAll, syncNow } = useQueue();
  const [warn, setWarn] = useState(false);
  const [pw, setPw] = useState(false);
  const [cur, setCur] = useState("");
  const [nw, setNw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const doLogout = async (force = false) => {
    if (items.length && !force) return setWarn(true);
    if (force) await clearAll();
    setWarn(false);
    await signOut();
    router.replace("/login");
  };

  const changePw = async () => {
    setBusy(true);
    setErr("");
    try {
      await api("/auth/change-password", { method: "POST", body: { current_password: cur, new_password: nw } });
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
        {items.length ? (
          <Pressable style={s.queueBox} onPress={syncNow} testID="profile-queue-status">
            <Feather name="upload-cloud" size={18} color={colors.onWarning} />
            <Text style={s.queueText}>{items.length} item(s) waiting to sync · tap to retry</Text>
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
        <Label>Security</Label>
        <Pressable style={s.row} onPress={() => setPw(true)} testID="change-password-button">
          <Feather name="lock" size={20} color={colors.onSurface} />
          <Text style={s.rowText}>Change password</Text>
          <Feather name="chevron-right" size={20} color={colors.onSurface} />
        </Pressable>
        <Btn title="Log out" icon="log-out" variant="outline" onPress={() => doLogout()} testID="logout-button" style={{ marginTop: space.lg }} />
      </ScrollView>

      <Sheet visible={warn} onClose={() => setWarn(false)} title="Unsynced evidence" testID="logout-warning-sheet">
        <Text style={s.mono}>{items.length} item(s) on this phone have NOT reached the server. Logging out will permanently delete them from this device.</Text>
        <Btn title="Sync now" icon="upload-cloud" onPress={() => { syncNow(); setWarn(false); }} testID="logout-sync-first" />
        <Btn title="Delete & log out" icon="trash-2" variant="danger" onPress={() => doLogout(true)} testID="logout-force-button" />
      </Sheet>

      <Sheet visible={pw} onClose={() => setPw(false)} title="Change password" testID="change-password-sheet">
        <KeyboardAwareScrollView bottomOffset={24} contentContainerStyle={{ gap: space.md }} keyboardShouldPersistTaps="handled">
          <Field label="Current password" secureTextEntry value={cur} onChangeText={setCur} testID="current-password-input" />
          <Field label="New password (min 8)" secureTextEntry value={nw} onChangeText={setNw} testID="new-password-input" />
          {err ? <ErrorBlock message={err} /> : null}
          <Btn title="Update" onPress={changePw} loading={busy} disabled={!cur || nw.length < 8} testID="change-password-submit" />
        </KeyboardAwareScrollView>
      </Sheet>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  content: { padding: space.lg, gap: space.sm },
  idBox: { borderWidth: BORDER, borderColor: c.border, padding: space.lg, gap: 4, backgroundColor: c.surfaceSecondary, marginBottom: space.md },
  name: { fontFamily: fonts.display, fontWeight: "900", fontSize: 22, color: c.onSurface },
  mono: { fontFamily: fonts.mono, fontSize: 13, color: c.onSurface },
  role: { fontFamily: fonts.mono, fontSize: 12, color: c.onBrandTertiary, marginTop: 4 },
  queueBox: { flexDirection: "row", gap: space.sm, alignItems: "center", backgroundColor: c.warning, borderWidth: BORDER, borderColor: c.border, padding: space.md, marginBottom: space.md },
  queueText: { fontFamily: fonts.mono, fontSize: 12, color: c.onWarning, flex: 1 },
  row: { flexDirection: "row", alignItems: "center", gap: space.md, minHeight: 56, borderWidth: BORDER, borderColor: c.border, paddingHorizontal: space.lg, marginBottom: -BORDER },
  rowText: { flex: 1, fontFamily: fonts.display, fontWeight: "700", fontSize: 16, color: c.onSurface },
}));
