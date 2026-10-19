import { QueryClientProvider } from "@tanstack/react-query";
import { useFonts } from "expo-font";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { LogBox, StyleSheet, View } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { KeyboardProvider } from "react-native-keyboard-controller";

import { ErrorBoundary } from "@/src/components/error-boundary";
import { queryClient } from "@/src/query-client";
import { AuthProvider } from "@/src/auth";
import { QueueProvider } from "@/src/queue";
import { ToastProvider } from "@/src/ui";
import { colors } from "@/src/theme";

LogBox.ignoreAllLogs(true);

queryClient.setDefaultOptions({ queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false } });

export default function RootLayout() {
  const [loaded] = useFonts({
    Archivo: require("../assets/fonts/Archivo-Bold.ttf"),
    SpaceMono: require("../assets/fonts/SpaceMono-Regular.ttf"),
    Feather: require("@react-native-vector-icons/feather/fonts/Feather.ttf"),
  });
  if (!loaded) return <View style={styles.boot} />;
  return (
    <GestureHandlerRootView style={styles.root}>
      <ErrorBoundary>
        <QueryClientProvider client={queryClient}>
          <KeyboardProvider>
            <AuthProvider>
              <QueueProvider>
                <ToastProvider>
                  <StatusBar style="dark" />
                  <Stack screenOptions={{ headerShown: false, animation: "fade", animationDuration: 150 }} />
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
  boot: { flex: 1, backgroundColor: colors.surface },
});
