import { FlatList, Pressable, RefreshControl, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useQueryClient } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { useAuth } from "@/src/auth";
import { Btn, Empty, Feather, IconName, Loading, fmtDate } from "@/src/ui";
import { Notif, useNotifications } from "@/src/components/Bell";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, CONTENT_MAX, radius, fonts, makeStyles, space, ThemeColors, useTheme } from "@/src/theme";

// one visual tone per notification category (soft tint + readable AA foreground)
const TONE = (c: ThemeColors): Record<string, [string, string, IconName]> => ({
  review: [c.brandSecondary, c.onBrandSecondary, "eye"], project_ready: [c.brandSecondary, c.onBrandSecondary, "check-square"],
  escalated: [c.warning, c.onWarning, "alert-circle"], action: [c.error, c.onError, "alert-triangle"],
  approved: [c.success, c.onSuccess, "check-circle"], completed: [c.success, c.onSuccess, "award"],
  assigned: [c.info, c.onInfo, "clipboard"], rate_changed: [c.info, c.onInfo, "tag"],
});

export default function Notifications() {
  const s = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const qc = useQueryClient();
  const { user } = useAuth();
  const q = useNotifications();

  const open = async (n: Notif) => {
    if (!n.read) await api(`/notifications/${n.id}/read`, { method: "POST" }).catch(() => {});
    qc.invalidateQueries({ queryKey: ["notifications"] });
    router.push(user?.role === "installer" ? `/job/${n.project_id}` : `/project/${n.project_id}`);
  };
  const readAll = async () => {
    await api("/notifications/read-all", { method: "POST" });
    qc.invalidateQueries({ queryKey: ["notifications"] });
  };

  if (q.isLoading) return <Loading />;
  return (
    <View style={s.root}>
      <ScreenHeader title="Notifications" back right={q.data?.unread ? <Btn title="Read all" small variant="outline" onPress={readAll} testID="notifications-read-all" /> : null} />
      <FlatList
        data={q.data?.items ?? []}
        keyExtractor={(n) => n.id}
        refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => { q.refetch(); }} />}
        contentContainerStyle={[s.list, { paddingBottom: insets.bottom + space.xl }]}
        renderItem={({ item }) => {
          const [bg, fg, icon] = TONE(colors)[item.kind] ?? [colors.surfaceSecondary, colors.onSurface, "bell"];
          return (
            <Pressable style={({ pressed }) => [s.card, !item.read && s.unread, pressed && { opacity: 0.85 }]} onPress={() => open(item)} testID={`notification-${item.id}`}>
              <View style={[s.icon, { backgroundColor: bg }]}><Feather name={icon} size={18} color={fg} /></View>
              <View style={{ flex: 1, gap: 2 }}>
                <View style={s.top}>
                  <Text style={[s.kind, { color: fg }]}>{item.title}</Text>
                  {!item.read ? <View style={s.dot} testID={`notification-unread-${item.id}`} /> : null}
                </View>
                <Text style={s.title} numberOfLines={2}>{item.address ?? item.title}</Text>
                {item.ref ? <Text style={s.ref}>{item.ref}</Text> : null}
                <Text style={s.body}>{item.body}</Text>
                <Text style={s.time}>{fmtDate(item.created_at)}</Text>
              </View>
            </Pressable>
          );
        }}
        ListEmptyComponent={<Empty icon="bell" text="No notifications" />}
      />
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  list: { padding: space.lg, gap: space.sm, width: "100%", maxWidth: CONTENT_MAX * 0.6, alignSelf: "center" },
  card: { flexDirection: "row", gap: space.md, borderWidth: BORDER, borderRadius: radius.lg, borderColor: c.border, padding: space.lg, backgroundColor: c.surface },
  unread: { backgroundColor: c.surfaceSecondary, borderColor: c.borderStrong },
  icon: { width: 36, height: 36, borderRadius: radius.pill, alignItems: "center", justifyContent: "center" },
  top: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: space.sm },
  kind: { fontFamily: fonts.text, fontSize: 12, fontWeight: "700", letterSpacing: 0.3 },
  dot: { width: 10, height: 10, borderRadius: 5, backgroundColor: c.brandPrimary },
  title: { fontFamily: fonts.display, fontWeight: "700", fontSize: 16, lineHeight: 21, color: c.onSurface },
  ref: { fontFamily: fonts.text, fontSize: 13, fontWeight: "600", color: c.muted },
  body: { fontFamily: fonts.text, fontSize: 14, lineHeight: 20, color: c.onSurface },
  time: { fontFamily: fonts.text, fontSize: 12, color: c.muted, marginTop: 2 },
}));
