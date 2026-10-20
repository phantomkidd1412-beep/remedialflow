// Push registration (native builds only; Expo Go / web skip). The backend binds the device to the signed-in user.
import { Platform } from "react-native";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { api } from "@/src/api";

export async function registerForPush() {
  if (Platform.OS === "web" || !Device.isDevice) return;
  try {
    const cur = await Notifications.getPermissionsAsync();
    let status = cur.status;
    if (status !== "granted" && cur.canAskAgain) status = (await Notifications.requestPermissionsAsync()).status;
    if (status !== "granted") return; // never blocks sign-in; in-app notifications still work
    const token = await Notifications.getDevicePushTokenAsync();
    await api("/register-push", { method: "POST", body: { platform: Platform.OS, device_token: String(token.data) } });
  } catch {
    // Expo Go / no push key yet: ignore, in-app notifications remain
  }
}

export async function unregisterPush() {
  if (Platform.OS === "web") return;
  try {
    await api("/unregister-push", { method: "POST" });
  } catch {}
}
