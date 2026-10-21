import { QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useFonts } from "expo-font";
import { Stack, useRouter } from "expo-router";
import { StatusBar } from "expo-status-bar";
import * as Notifications from "expo-notifications";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { Linking, LogBox, Platform, StyleSheet, Text, View } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { KeyboardProvider } from "react-native-keyboard-controller";

import { ErrorBoundary } from "@/src/components/error-boundary";
import { queryClient } from "@/src/query-client";
import { AuthProvider } from "@/src/auth";
import { QueueProvider } from "@/src/queue";
import { Btn, Sheet, ToastProvider } from "@/src/ui";
import { loadThemePref, useTheme } from "@/src/theme";

LogBox.ignoreAllLogs(true);

// Push: foreground display + Android channel at module scope (native only)
if (Platform.OS !== "web") {
  Notifications.setNotificationHandler({
    handleNotification: async () => ({ shouldShowAlert: true, shouldShowBanner: true, shouldShowList: true, shouldPlaySound: true, shouldSetBadge: false }),
  });
}
if (Platform.OS === "android") {
  Notifications.setNotificationChannelAsync("default", { name: "Default", importance: Notifications.AndroidImportance.MAX, sound: "default" });
}

// only in-app routes are followed from a push payload
const openPushTarget = (router: ReturnType<typeof useRouter>, data: any) => {
  const url = data?.deeplink || data?.action_url;
  if (typeof url === "string" && url.startsWith("/")) router.push(url as any);
};

function PushHandlers() {
  const router = useRouter();
  const { colors } = useTheme();
  const [nudge, setNudge] = useState(false);
  useEffect(() => {
    if (Platform.OS === "web") return;
    const tapSub = Notifications.addNotificationResponseReceivedListener((r) => openPushTarget(router, r.notification.request.content.data));
    Notifications.getLastNotificationResponseAsync().then((r) => r && openPushTarget(router, r.notification.request.content.data));
    (async () => {
      const { status, canAskAgain } = await Notifications.getPermissionsAsync();
      if (status !== "denied" || canAskAgain) return;
      const last = await AsyncStorage.getItem("pushNudgeAt");
      if (last && Date.now() - Number(last) <= 7 * 864e5) return;
      setNudge(true);
    })();
    return () => tapSub.remove();
  }, [router]);
  const done = async (open: boolean) => {
    await AsyncStorage.setItem("pushNudgeAt", String(Date.now()));
    setNudge(false);
    if (open) Linking.openSettings();
  };
  return (
    <Sheet visible={nudge} onClose={() => done(false)} title="Turn on notifications" testID="push-nudge-sheet">
      <Text style={[styles.nudge, { color: colors.onSurface }]}>Get alerted when new work is assigned or a job needs action. You can change this in Settings.</Text>
      <Btn title="Open Settings" onPress={() => done(true)} testID="push-nudge-settings" />
      <Btn title="Later" variant="outline" onPress={() => done(false)} testID="push-nudge-later" />
    </Sheet>
  );
}

// offlineFirst keeps the offline-cache queryFn running without a connection; offline misses and 4xx are final
queryClient.setDefaultOptions({
  queries: { staleTime: 30_000, refetchOnWindowFocus: false, networkMode: "offlineFirst", retry: (n, e: any) => !e?.offline && !(e?.status >= 400 && e?.status < 500) && n < 1 },
  mutations: { networkMode: "offlineFirst" },
});

export default function RootLayout() {
  const [loaded] = useFonts({
    Archivo: require("../assets/fonts/Archivo-Bold.ttf"),
    Feather: require("@react-native-vector-icons/feather/fonts/Feather.ttf"),
  });
  const [prefReady, setPrefReady] = useState(false);
  useEffect(() => {
    loadThemePref().finally(() => setPrefReady(true));
  }, []);
  const { scheme, colors } = useTheme();
  if (!loaded || !prefReady) return <View style={[styles.boot, { backgroundColor: colors.surface }]} />;
  return (
    <GestureHandlerRootView style={[styles.root, { backgroundColor: colors.surface }]}>
      <ErrorBoundary>
        <QueryClientProvider client={queryClient}>
          <KeyboardProvider>
            <AuthProvider>
              <QueueProvider>
                <ToastProvider>
                  <StatusBar style={scheme === "dark" ? "light" : "dark"} />
                  <Stack screenOptions={{ headerShown: false, animation: "fade", animationDuration: 150, contentStyle: { backgroundColor: colors.surface } }} />
                  <PushHandlers />
                </ToastProvider>
              </QueueProvider>
            </AuthProvider>
          </KeyboardProvider>
        </QueryClientProvider>
      </ErrorBoundary>
    </GestureHandlerRootView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  boot: { flex: 1 },
  nudge: { fontSize: 15 },
});
