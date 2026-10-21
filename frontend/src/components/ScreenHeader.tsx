import { ReactNode } from "react";
import { Pressable, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Feather } from "@/src/ui";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

export function ScreenHeader({ title, sub, back, right, children }: { title: string; sub?: string; back?: boolean; right?: ReactNode; children?: ReactNode }) {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { colors } = useTheme();
  return (
    <View style={[s.wrap, { paddingTop: insets.top + space.sm }]}>
      <View style={s.row}>
        {back ? (
          <Pressable onPress={() => (router.canGoBack() ? router.back() : router.replace("/"))} style={s.back} testID="header-back-button" hitSlop={8}>
            <Feather name="arrow-left" size={22} color={colors.onSurface} />
          </Pressable>
        ) : null}
        <View style={s.titles}>
          <Text style={s.title} numberOfLines={1} testID="screen-title">{title}</Text>
          {sub ? <Text style={s.sub} numberOfLines={2}>{sub}</Text> : null}
        </View>
        {right}
      </View>
      {children}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  wrap: { backgroundColor: c.surface, borderBottomWidth: BORDER, borderColor: c.divider, paddingHorizontal: space.lg, paddingBottom: space.md, gap: space.md },
  row: { flexDirection: "row", alignItems: "center", gap: space.md, minHeight: 44 },
  back: { width: 44, height: 44, borderRadius: radius.pill, backgroundColor: c.surfaceSecondary, alignItems: "center", justifyContent: "center" },
  titles: { flex: 1 },
  title: { fontFamily: fonts.display, fontWeight: "700", fontSize: 22, color: c.onSurface, letterSpacing: -0.3 },
  sub: { fontFamily: fonts.text, fontSize: 13, color: c.muted, marginTop: 2 },
}));
