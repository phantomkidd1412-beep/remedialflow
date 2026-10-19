import { Pressable, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/src/api";
import { Feather } from "@/src/ui";
import { BORDER, makeStyles, useTheme } from "@/src/theme";

export type Notif = { id: string; kind: string; title: string; body: string; project_id: string; nc_id?: string | null; read: boolean; created_at: string };

export function useNotifications() {
  return useQuery({ queryKey: ["notifications"], queryFn: () => api<{ items: Notif[]; unread: number }>("/notifications"), refetchInterval: 30000 });
}

export function Bell() {
  const s = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const q = useNotifications();
  const n = q.data?.unread ?? 0;
  return (
    <Pressable style={s.btn} onPress={() => router.push("/notifications")} testID="notifications-bell" hitSlop={6}>
      <Feather name="bell" size={20} color={colors.onSurface} />
      {n > 0 ? (
        <View style={s.badge}><Text style={s.badgeText} testID="notifications-unread-count">{n > 99 ? "99+" : n}</Text></View>
      ) : null}
    </Pressable>
  );
}

const useStyles = makeStyles((c) => ({
  btn: { width: 44, height: 44, borderWidth: BORDER, borderColor: c.border, alignItems: "center", justifyContent: "center" },
  badge: { position: "absolute", top: -6, right: -6, minWidth: 20, height: 20, paddingHorizontal: 4, backgroundColor: c.brandPrimary, borderWidth: BORDER, borderColor: c.border, alignItems: "center", justifyContent: "center" },
  badgeText: { fontSize: 11, fontWeight: "800", color: c.onBrandPrimary },
}));
