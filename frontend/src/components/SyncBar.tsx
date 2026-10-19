import { ActivityIndicator, Pressable, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useQueue } from "@/src/queue";
import { usesNativeTabs } from "@/src/navigation";
import { Feather } from "@/src/ui";
import { BORDER, fonts, makeStyles, space, useTheme } from "@/src/theme";

/** Persistent upload-queue indicator. Renders nothing when everything is synced. */
export function SyncBar({ projectId, inTabs = true }: { projectId?: string; inTabs?: boolean }) {
  const s = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const { items, syncing, syncNow } = useQueue();
  const list = projectId ? items.filter((i) => i.projectId === projectId) : items;
  if (!list.length) return null;
  const failed = list.filter((i) => i.status === "failed").length;
  const uploads = list.filter((i) => i.type === "upload").length;
  const waiting = list.find((i) => i.error === "Waiting for connection");
  const bottom = inTabs ? (usesNativeTabs ? insets.bottom : 0) : insets.bottom;
  return (
    <View style={[s.bar, failed ? { backgroundColor: colors.error } : null, { paddingBottom: bottom + space.sm }]} testID="sync-bar">
      {syncing ? <ActivityIndicator color={colors.onSurface} size="small" /> : <Feather name={failed ? "alert-triangle" : waiting ? "wifi-off" : "upload-cloud"} size={18} color={failed ? colors.onError : colors.onWarning} />}
      <Text style={[s.text, failed ? { color: colors.onError } : null]} testID="sync-bar-text" numberOfLines={2}>
        {failed ? `${failed} FAILED · ` : ""}
        {uploads} FILE{uploads === 1 ? "" : "S"} NOT YET ON SERVER{waiting ? " · OFFLINE" : syncing ? " · UPLOADING" : ""}
      </Text>
      <Pressable onPress={syncNow} style={s.btn} testID="sync-now-button" hitSlop={6}>
        <Text style={s.btnText}>SYNC</Text>
      </Pressable>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  bar: { flexDirection: "row", alignItems: "center", gap: space.sm, backgroundColor: c.warning, borderTopWidth: BORDER, borderColor: c.border, paddingHorizontal: space.lg, paddingTop: space.sm },
  text: { flex: 1, fontFamily: fonts.mono, fontSize: 12, color: c.onWarning },
  btn: { borderWidth: BORDER, borderColor: c.border, paddingHorizontal: space.md, height: 36, justifyContent: "center", backgroundColor: c.surface },
  btnText: { fontFamily: fonts.mono, fontSize: 12, color: c.onSurface },
}));
