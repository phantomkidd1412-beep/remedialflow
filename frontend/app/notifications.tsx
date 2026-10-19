import { FlatList, Pressable, RefreshControl, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useQueryClient } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { useAuth } from "@/src/auth";
import { Btn, Empty, Loading, fmtDate } from "@/src/ui";
import { Notif, useNotifications } from "@/src/components/Bell";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

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
    router.push(user?.role === "installer" ? `/job/${n.project_id}` : `/project/${n.project_id}${n.nc_id ? `?nc=${n.nc_id}` : ""}`);
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
        refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => q.refetch()} />}
        contentContainerStyle={[s.list, { paddingBottom: insets.bottom + space.xl }]}
        renderItem={({ item }) => (
          <Pressable style={[s.card, !item.read && { borderColor: colors.brandPrimary, backgroundColor: colors.brandTertiary }]} onPress={() => open(item)} testID={`notification-${item.id}`}>
            <Text style={s.title}>{item.title}</Text>
            <Text style={s.body}>{item.body}</Text>
            <Text style={s.time}>{fmtDate(item.created_at)}{item.read ? "" : " · NEW"}</Text>
          </Pressable>
        )}
        ListEmptyComponent={<Empty icon="bell" text="No notifications" />}
      />
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  list: { padding: space.lg, gap: space.md },
  card: { borderWidth: BORDER, borderColor: c.border, padding: space.md, gap: 4 },
  title: { fontFamily: fonts.display, fontWeight: "800", fontSize: 16, color: c.onSurface },
  body: { fontSize: 14, lineHeight: 20, color: c.onSurface },
  time: { fontSize: 12, color: c.muted },
}));
