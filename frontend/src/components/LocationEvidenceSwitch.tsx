import { useState } from "react";
import { Switch, Text, View } from "react-native";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/src/api";
import { Label, useToast } from "@/src/ui";
import { fonts, makeStyles, space, useTheme } from "@/src/theme";

type LE = { state: "on" | "off" | "partial" };

/** Admin-only master switch for the five location-aware evidence features. Applies immediately (separate from "Save settings"). */
export function LocationEvidenceSwitch() {
  const s = useStyles();
  const { colors } = useTheme();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["location-evidence"], queryFn: () => api<LE>("/settings/location-evidence") });
  const [busy, setBusy] = useState(false);
  if (!q.data) return null;
  const st = q.data.state;
  const toggle = async (on: boolean) => {
    setBusy(true);
    try {
      qc.setQueryData(["location-evidence"], await api<LE>("/settings/location-evidence", { method: "PUT", body: { enabled: on } }));
      qc.invalidateQueries({ queryKey: ["settings"] });
      toast(on ? "Location-Aware Evidence turned on" : "Location-Aware Evidence turned off");
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <View style={s.box} testID="location-evidence-setting">
      <View style={s.row}>
        <View style={{ flex: 1 }}>
          <Label>Location-Aware Evidence</Label>
          <Text style={s.state} testID="location-evidence-state">
            {st === "on" ? "ON – all five features active" : st === "off" ? "OFF – existing behaviour" : "PARTLY ON – use the switch to set all five together"}
          </Text>
        </View>
        <Switch value={st === "on"} onValueChange={toggle} disabled={busy} trackColor={{ true: colors.brandPrimary, false: colors.borderStrong }} accessibilityLabel="Location-Aware Evidence" testID="location-evidence-switch" />
      </View>
      <Text style={s.help}>Location checks, photo sorting, coverage checks, location-organised PDFs and the staff review panel. Changes apply immediately and are recorded in the audit log.</Text>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  box: { gap: space.xs },
  row: { flexDirection: "row", alignItems: "center", gap: space.md, minHeight: 48 },
  state: { fontFamily: fonts.text, fontSize: 13, color: c.onSurface },
  help: { fontFamily: fonts.text, fontSize: 11, color: c.muted },
}));
