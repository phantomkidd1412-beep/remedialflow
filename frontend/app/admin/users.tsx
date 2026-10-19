import { useState } from "react";
import { FlatList, Pressable, Text, View } from "react-native";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { useAuth } from "@/src/auth";
import { Badge, Btn, ErrorBlock, Field, Label, Loading, Sheet, useToast } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

type U = { id: string; email: string; name: string; role: string; active: boolean };
const ROLES = ["installer", "employee", "admin"];

export default function Users() {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const toast = useToast();
  const qc = useQueryClient();
  const { user: me } = useAuth();
  const q = useQuery({ queryKey: ["users"], queryFn: () => api<U[]>("/admin/users") });
  const [adding, setAdding] = useState(false);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("installer");
  const [err, setErr] = useState("");
  const [code, setCode] = useState<{ title: string; code: string } | null>(null);
  const [sel, setSel] = useState<U | null>(null);

  const refresh = () => qc.invalidateQueries({ queryKey: ["users"] });

  const create = async () => {
    setErr("");
    try {
      const r = await api("/admin/users", { method: "POST", body: { email, name, role } });
      setAdding(false);
      setEmail(""); setName("");
      setCode({ title: `Setup code for ${r.user.email}`, code: r.setup_code });
      refresh();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const toggleActive = async (u: U) => {
    try {
      await api(`/admin/users/${u.id}`, { method: "PATCH", body: { active: !u.active } });
      toast(u.active ? `${u.name} deactivated · sessions revoked` : `${u.name} reactivated`);
      setSel(null);
      refresh();
    } catch (e: any) {
      toast(e.message);
    }
  };

  const reset = async (u: U) => {
    const r = await api(`/admin/users/${u.id}/reset`, { method: "POST" });
    setSel(null);
    setCode({ title: `Reset code for ${u.email}`, code: r.reset_code });
  };

  return (
    <View style={s.root}>
      <ScreenHeader title="USERS & ACCESS" back right={<Btn title="Invite" icon="user-plus" small onPress={() => setAdding(true)} testID="invite-user-button" />} />
      {q.isLoading ? (
        <Loading />
      ) : q.isError ? (
        <View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error).message} onRetry={() => q.refetch()} /></View>
      ) : (
        <FlatList
          data={q.data}
          keyExtractor={(u) => u.id}
          contentContainerStyle={{ padding: space.lg, paddingBottom: insets.bottom + space.xl }}
          renderItem={({ item }) => (
            <Pressable style={s.row} onPress={() => setSel(item)} testID={`user-row-${item.email}`}>
              <View style={{ flex: 1 }}>
                <Text style={s.name}>{item.name}</Text>
                <Text style={s.mono}>{item.email}</Text>
              </View>
              <Badge status={item.active ? "active" : "rejected"} label={item.active ? item.role : "Deactivated"} testID={`user-status-${item.email}`} />
            </Pressable>
          )}
        />
      )}

      <Sheet visible={adding} onClose={() => setAdding(false)} title="Invite user" testID="invite-sheet">
        <Field label="Full name" value={name} onChangeText={setName} testID="invite-name-input" />
        <Field label="Email" value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" testID="invite-email-input" />
        <Label>Role</Label>
        <View style={{ flexDirection: "row", gap: space.sm }}>
          {ROLES.map((r) => (
            <Pressable key={r} onPress={() => setRole(r)} style={[s.chip, role === r && { backgroundColor: colors.brandSecondary }]} testID={`invite-role-${r}`}>
              <Text style={[s.chipText, role === r && { color: colors.onBrandSecondary }]}>{r}</Text>
            </Pressable>
          ))}
        </View>
        {err ? <ErrorBlock message={err} /> : null}
        <Btn title="Create & get setup code" onPress={create} disabled={!email || !name} testID="invite-submit-button" />
      </Sheet>

      <Sheet visible={!!sel} onClose={() => setSel(null)} title={sel?.name ?? ""} testID="user-actions-sheet">
        <Text style={s.mono}>{sel?.email} · {sel?.role}</Text>
        <Btn title="Issue password reset code" icon="key" variant="outline" onPress={() => sel && reset(sel)} testID="user-reset-button" />
        {sel && sel.id !== me?.id ? (
          <Btn title={sel.active ? "Deactivate & revoke access" : "Reactivate"} icon={sel.active ? "slash" : "check"} variant={sel.active ? "danger" : "success"} onPress={() => toggleActive(sel)} testID="user-toggle-active-button" />
        ) : null}
      </Sheet>

      <Sheet visible={!!code} onClose={() => setCode(null)} title={code?.title ?? ""} testID="code-sheet">
        <Text style={s.code} selectable testID="issued-code">{code?.code}</Text>
        <Text style={s.mono}>Share securely. The user enters it on the sign-in screen under “Have a code?” to set their password. Valid 72 hours, single use.</Text>
        <Btn title="Done" onPress={() => setCode(null)} testID="code-done-button" />
      </Sheet>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  row: { flexDirection: "row", alignItems: "center", gap: space.md, minHeight: 64, borderWidth: BORDER, borderColor: c.border, padding: space.md, marginBottom: -BORDER },
  name: { fontFamily: fonts.display, fontWeight: "700", fontSize: 15, color: c.onSurface },
  mono: { fontFamily: fonts.mono, fontSize: 12, color: c.onSurface },
  chip: { height: 36, flex: 1, borderWidth: BORDER, borderColor: c.border, justifyContent: "center", alignItems: "center" },
  chipText: { fontFamily: fonts.mono, fontSize: 12, color: c.onSurface, textTransform: "uppercase" },
  code: { fontFamily: fonts.mono, fontSize: 28, color: c.onSurface, backgroundColor: c.warning, padding: space.md, borderWidth: BORDER, borderColor: c.border, textAlign: "center", letterSpacing: 2 },
}));
