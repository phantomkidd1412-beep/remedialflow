import { useState } from "react";
import { ActivityIndicator, Pressable, Text } from "react-native";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/src/api";
import { Feather, useToast } from "@/src/ui";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

/** Safety-critical indicator. Orange when active; staff can toggle (saved server-side), installers see it read-only. */
export function SafetyToggle({ ncId, number, value, projectId, readOnly }: { ncId: string; number: string; value: boolean; projectId: string; readOnly?: boolean }) {
  const s = useStyles();
  const { colors } = useTheme();
  const qc = useQueryClient();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  if (readOnly && !value) return null;
  const toggle = async () => {
    setBusy(true);
    try {
      await api(`/ncs/${ncId}/safety`, { method: "PUT", body: { safety_critical: !value } });
      await qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["review-groups"] });
      toast(`${number}: ${value ? "no longer marked" : "marked"} safety-critical`);
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(false);
    }
  };
  const tint = value ? colors.brandPrimary : colors.muted;
  return (
    <Pressable
      onPress={readOnly ? undefined : toggle}
      disabled={readOnly || busy}
      style={[s.btn, value && { borderColor: colors.brandPrimary, backgroundColor: colors.brandSecondary }]}
      accessibilityRole={readOnly ? "text" : "switch"}
      accessibilityState={{ checked: value, disabled: readOnly || busy }}
      accessibilityLabel={value ? "Safety critical: on" : "Safety critical: off"}
      hitSlop={6}
      testID={`safety-toggle-${number}`}
    >
      {busy ? <ActivityIndicator size="small" color={tint} /> : <Feather name="alert-triangle" size={16} color={tint} />}
      <Text style={[s.text, { color: tint }]} testID={`safety-state-${number}`}>{value ? "Safety critical" : "Not safety critical"}</Text>
    </Pressable>
  );
}

const useStyles = makeStyles((c) => ({
  btn: { flexDirection: "row", alignItems: "center", gap: space.xs, minHeight: 36, paddingHorizontal: space.sm, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, alignSelf: "flex-start" },
  text: { fontFamily: fonts.text, fontSize: 12, fontWeight: "700" },
}));
