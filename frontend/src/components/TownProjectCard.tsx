import { memo } from "react";
import { Pressable, Text, View } from "react-native";
import { Badge, Feather, Progress } from "@/src/ui";
import { JobRow, jobStatus, ncCounts } from "@/src/towns";
import { BORDER, fonts, makeStyles, radius, space, useTheme } from "@/src/theme";

/** One project in a town: address, status, NC progress, outstanding count and a progress bar. */
export const TownProjectCard = memo(function TownProjectCard({ p, queued, onOpen }: { p: JobRow; queued: number; onOpen: (id: string) => void }) {
  const s = useStyles();
  const { colors } = useTheme();
  const st = p.my_statuses ?? [];
  const n = ncCounts(st);
  const status = jobStatus(st);
  return (
    <Pressable style={({ pressed }) => [s.card, n.revisit > 0 && { borderLeftWidth: 4, borderLeftColor: colors.onError }, pressed && { opacity: 0.85 }]}
      onPress={() => onOpen(p.id)} accessibilityRole="button" testID={`town-project-${p.ref}`}>
      <View style={s.top}>
        <Text style={s.ref}>{p.ref}</Text>
        <Badge status={status.badge} label={status.label} testID={`town-project-status-${p.ref}`} />
      </View>
      <Text style={s.addr}>{p.address}</Text>
      {p.postcode ? <Text style={s.pc}>{p.postcode}</Text> : null}
      <View style={s.row}>
        <Text style={s.meta} testID={`town-project-progress-${p.ref}`}>{n.attempted} of {n.total} NCs addressed</Text>
        <Text style={[s.meta, n.outstanding > 0 && { color: colors.onSurface, fontWeight: "700" }]} testID={`town-project-outstanding-${p.ref}`}>
          {n.outstanding} outstanding
        </Text>
      </View>
      <Progress value={n.total ? n.attempted / n.total : 0} />
      <View style={s.row}>
        {queued > 0 ? <Badge status="pending" label={`${queued} unsynced`} /> : <Text style={s.meta}>{n.done} approved</Text>}
        <Feather name="chevron-right" size={20} color={colors.muted} />
      </View>
    </Pressable>
  );
});

const useStyles = makeStyles((c) => ({
  card: { borderWidth: BORDER, borderRadius: radius.lg, borderColor: c.border, backgroundColor: c.surface, padding: space.lg, gap: space.sm },
  top: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: space.sm },
  ref: { fontFamily: fonts.text, fontSize: 13, fontWeight: "600", color: c.muted, letterSpacing: 0.3 },
  addr: { fontFamily: fonts.display, fontWeight: "700", fontSize: 18, lineHeight: 24, color: c.onSurface },
  pc: { fontFamily: fonts.text, fontSize: 14, fontWeight: "600", color: c.onSurfaceSecondary, marginTop: -4 },
  row: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  meta: { fontSize: 13, color: c.muted },
}));
