import { useState } from "react";
import { Pressable, Text, View } from "react-native";
import { Btn, Feather, Sheet } from "@/src/ui";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

const pad = (n: number) => String(n).padStart(2, "0");
const iso = (y: number, m: number, d: number) => `${y}-${pad(m + 1)}-${pad(d)}`;
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const DAYS = ["M", "T", "W", "T", "F", "S", "S"];

/** Tap-to-pick month calendar (pure RN, works on iOS, Android and web). value/onPick use YYYY-MM-DD. */
export function DatePickerSheet({ visible, value, title, onPick, onClose, testID = "date-picker" }: {
  visible: boolean; value: string | null; title: string; onPick: (date: string | null) => void; onClose: () => void; testID?: string;
}) {
  const s = useStyles();
  const { colors } = useTheme();
  const now = new Date();
  const today = iso(now.getFullYear(), now.getMonth(), now.getDate());
  const start = value ? new Date(`${value}T12:00:00`) : now;
  const [ym, setYm] = useState({ y: start.getFullYear(), m: start.getMonth() });
  const first = (new Date(ym.y, ym.m, 1).getDay() + 6) % 7; // Monday-first
  const days = new Date(ym.y, ym.m + 1, 0).getDate();
  const cells: (number | null)[] = [...Array(first).fill(null), ...Array.from({ length: days }, (_, i) => i + 1)];
  while (cells.length % 7) cells.push(null);
  const shift = (d: number) => setYm(({ y, m }) => ({ y: m + d < 0 ? y - 1 : m + d > 11 ? y + 1 : y, m: (m + d + 12) % 12 }));

  return (
    <Sheet visible={visible} onClose={onClose} title={title} testID={testID}>
      <View style={s.head}>
        <Pressable onPress={() => shift(-1)} style={s.nav} hitSlop={8} testID={`${testID}-prev`} accessibilityLabel="Previous month">
          <Feather name="chevron-left" size={22} color={colors.onSurface} />
        </Pressable>
        <Text style={s.month} testID={`${testID}-month`}>{MONTHS[ym.m]} {ym.y}</Text>
        <Pressable onPress={() => shift(1)} style={s.nav} hitSlop={8} testID={`${testID}-next`} accessibilityLabel="Next month">
          <Feather name="chevron-right" size={22} color={colors.onSurface} />
        </Pressable>
      </View>
      <View style={s.grid}>
        {DAYS.map((d, i) => <Text key={`h${i}`} style={s.dow}>{d}</Text>)}
        {cells.map((d, i) => {
          if (!d) return <View key={`e${i}`} style={s.cell} />;
          const v = iso(ym.y, ym.m, d);
          const sel = v === value;
          const isToday = v === today;
          return (
            <Pressable key={v} onPress={() => onPick(v)} style={[s.cell, s.day, isToday && s.today, sel && { backgroundColor: colors.brandPrimary }]} testID={`${testID}-day-${v}`} accessibilityState={{ selected: sel }}>
              <Text style={[s.dayText, sel && { color: colors.onBrandPrimary }]}>{d}</Text>
            </Pressable>
          );
        })}
      </View>
      <View style={s.actions}>
        <Btn title="Today" variant="outline" small onPress={() => onPick(today)} style={{ flex: 1 }} testID={`${testID}-today`} />
        {value ? <Btn title="Clear date" variant="outline" small onPress={() => onPick(null)} style={{ flex: 1 }} testID={`${testID}-clear`} /> : null}
      </View>
    </Sheet>
  );
}

const useStyles = makeStyles((c) => ({
  head: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: space.sm },
  nav: { width: 44, height: 44, alignItems: "center", justifyContent: "center", borderWidth: BORDER, borderColor: c.border },
  month: { fontFamily: fonts.display, fontWeight: "800", fontSize: 16, color: c.onSurface },
  grid: { flexDirection: "row", flexWrap: "wrap" },
  dow: { width: `${100 / 7}%`, textAlign: "center", fontFamily: fonts.mono, fontSize: 11, color: c.muted, paddingVertical: 4 },
  cell: { width: `${100 / 7}%`, height: 44, alignItems: "center", justifyContent: "center" },
  day: { borderWidth: 1, borderColor: "transparent" },
  today: { borderColor: c.border },
  dayText: { fontFamily: fonts.mono, fontSize: 14, color: c.onSurface },
  actions: { flexDirection: "row", gap: space.sm, marginTop: space.md },
}));
