import { useState } from "react";
import { Pressable, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useAuth } from "@/src/auth";
import { api } from "@/src/api";
import { Btn, ErrorBlock, Field, H1 } from "@/src/ui";
import { BORDER, radius, fonts, makeStyles, space } from "@/src/theme";

// Development-only quick fill. Remove before production deployment.
const DEMO = __DEV__
  ? [
      ["Installer", "installer@remedialflow.dev", "InstallerDemo!2026"],
      ["Employee", "employee@remedialflow.dev", "EmployeeDemo!2026"],
      ["Admin", "admin@remedialflow.dev", "AdminDemo!2026"],
    ]
  : [];

type Mode = "login" | "forgot" | "reset";

export default function Login() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { signIn } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [info, setInfo] = useState("");

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setErr("");
    try {
      await fn();
    } catch (e: any) {
      setErr(e.message || "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  const doLogin = () =>
    run(async () => {
      const u = await signIn(email, password);
      router.replace(u.role === "installer" ? "/(installer)/jobs" : "/(staff)/dashboard");
    });

  const doForgot = () =>
    run(async () => {
      const r = await api("/auth/forgot", { method: "POST", body: { email: email.trim() } });
      setInfo(`${r.message}\nOpen the link in the email to choose a new password.`);
      setMode("login");
    });

  const doReset = () =>
    run(async () => {
      await api("/auth/reset", { method: "POST", body: { token: code, password } });
      setInfo("Password set. Sign in with your new password.");
      setPassword("");
      setMode("login");
    });

  return (
    <View style={s.root}>
      <KeyboardAwareScrollView contentContainerStyle={[s.content, { paddingTop: insets.top + space.xxl, paddingBottom: insets.bottom + space.xl }]} bottomOffset={24} keyboardShouldPersistTaps="handled">
        <View style={s.logo}><Text style={s.logoText}>RF</Text></View>
        <H1 testID="login-title">REMEDIALFLOW</H1>
        <Text style={s.sub}>FIELD EVIDENCE · NC REMEDIATION</Text>

        <View style={s.form}>
          {mode !== "reset" ? (
            <Field label="Email" testID="login-email-input" value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" autoComplete="email" placeholder="you@company.co.uk" />
          ) : (
            <Field label="Reset / setup code" testID="reset-code-input" value={code} onChangeText={setCode} autoCapitalize="characters" placeholder="CODE" />
          )}
          {mode !== "forgot" ? (
            <Field label={mode === "reset" ? "New password (min 8)" : "Password"} testID="login-password-input" value={password} onChangeText={setPassword} secureTextEntry placeholder="••••••••" onSubmitEditing={mode === "login" ? doLogin : doReset} />
          ) : null}
          {info ? <Text style={s.info} testID="login-info">{info}</Text> : null}
          {err ? <ErrorBlock message={err} /> : null}
          {mode === "login" && <Btn title="Sign in" icon="log-in" onPress={doLogin} loading={busy} disabled={!email || !password} testID="login-submit-button" />}
          {mode === "forgot" && <Btn title="Email me a reset link" onPress={doForgot} loading={busy} disabled={!email} testID="forgot-submit-button" />}
          {mode === "reset" && <Btn title="Set password" onPress={doReset} loading={busy} disabled={!code || password.length < 8} testID="reset-submit-button" />}

          <View style={s.links}>
            {mode === "login" ? (
              <>
                <Pressable onPress={() => { setMode("forgot"); setInfo(""); }} testID="forgot-password-link" hitSlop={8}><Text style={s.link}>Forgot password?</Text></Pressable>
                <Pressable onPress={() => { setMode("reset"); setInfo(""); }} testID="have-code-link" hitSlop={8}><Text style={s.link}>Have a setup code?</Text></Pressable>
              </>
            ) : (
              <Pressable onPress={() => setMode("login")} testID="back-to-login-link" hitSlop={8}><Text style={s.link}>Back to sign in</Text></Pressable>
            )}
          </View>
        </View>

        {DEMO.length > 0 && mode === "login" ? (
          <View style={s.demo}>
            <Text style={s.demoLabel}>DEV DEMO ACCOUNTS</Text>
            <View style={s.demoRow}>
              {DEMO.map(([label, e, p]) => (
                <Pressable key={label} style={s.demoChip} onPress={() => { setEmail(e); setPassword(p); }} testID={`demo-${label.toLowerCase()}-button`}>
                  <Text style={s.demoText}>{label}</Text>
                </Pressable>
              ))}
            </View>
          </View>
        ) : null}
        <Text style={s.foot}>Accounts are created by your administrator.</Text>
      </KeyboardAwareScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  content: { paddingHorizontal: space.xl, gap: space.sm },
  logo: { width: 56, height: 56, backgroundColor: c.brandPrimary, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, alignItems: "center", justifyContent: "center", marginBottom: space.lg },
  logoText: { fontFamily: fonts.display, fontWeight: "800", fontSize: 22, color: c.onBrandPrimary },
  sub: { fontFamily: fonts.text, fontSize: 12, color: c.muted },
  form: { gap: space.lg, marginTop: space.xl },
  info: { fontFamily: fonts.text, fontSize: 12, color: c.onSurface, backgroundColor: c.warning, padding: space.md, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border },
  links: { flexDirection: "row", justifyContent: "space-between" },
  link: { fontFamily: fonts.text, fontSize: 13, color: c.onSurface, textDecorationLine: "underline" },
  demo: { marginTop: space.xxl, borderTopWidth: BORDER, borderColor: c.border, paddingTop: space.md, gap: space.sm },
  demoLabel: { fontFamily: fonts.text, fontSize: 11, color: c.muted },
  demoRow: { flexDirection: "row", gap: space.sm },
  demoChip: { flex: 1, height: 40, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, alignItems: "center", justifyContent: "center", backgroundColor: c.surfaceSecondary },
  demoText: { fontFamily: fonts.text, fontSize: 12, color: c.onSurface },
  foot: { fontFamily: fonts.text, fontSize: 11, color: c.muted, marginTop: space.lg },
}));
