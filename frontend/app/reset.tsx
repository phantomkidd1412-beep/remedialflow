import { useState } from "react";
import { Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { Btn, ErrorBlock, Field, H1 } from "@/src/ui";
import { makeStyles, space } from "@/src/theme";

/** Target of the emailed reset link: /reset?token=… (single use, time limited; token never stored on device). */
export default function ResetPassword() {
  const s = useStyles();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { token } = useLocalSearchParams<{ token?: string }>();
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [done, setDone] = useState(false);

  const submit = async () => {
    if (pw.length < 8) return setErr("Use at least 8 characters");
    if (pw !== pw2) return setErr("Passwords do not match");
    setBusy(true);
    setErr("");
    try {
      await api("/auth/reset", { method: "POST", body: { token, password: pw } });
      setDone(true);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={s.root}>
      <KeyboardAwareScrollView contentContainerStyle={[s.content, { paddingTop: insets.top + space.xxl, paddingBottom: insets.bottom + space.xl }]} bottomOffset={24} keyboardShouldPersistTaps="handled">
        <H1 testID="reset-title">RESET PASSWORD</H1>
        {!token ? (
          <ErrorBlock message="This reset link is incomplete. Request a new one from the sign-in screen." />
        ) : done ? (
          <Text style={s.info} testID="reset-done">Password changed. All other sessions have been signed out.</Text>
        ) : (
          <>
            <Field label="New password (min 8)" value={pw} onChangeText={setPw} secureTextEntry testID="reset-password-input" />
            <Field label="Confirm new password" value={pw2} onChangeText={setPw2} secureTextEntry onSubmitEditing={submit} testID="reset-password-confirm" />
            {err ? <ErrorBlock message={err} /> : null}
            <Btn title="Set new password" onPress={submit} loading={busy} disabled={!pw || !pw2} testID="reset-link-submit" />
          </>
        )}
        <Btn title="Go to sign in" variant="outline" onPress={() => router.replace("/login")} testID="reset-to-login" />
      </KeyboardAwareScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  content: { padding: space.lg, gap: space.md },
  info: { fontSize: 15, color: c.onSurface },
}));
