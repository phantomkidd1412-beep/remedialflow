// Design tokens (from /app/design_guidelines.json). Light + dark themes, user-selectable (Light / Dark / Auto).
import { useMemo, useSyncExternalStore } from "react";
import { Appearance, ImageStyle, Platform, StyleSheet, TextStyle, useColorScheme, ViewStyle } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";

export type ColorScheme = "light" | "dark";

const light = {
  surface: "#FFFFFF",
  onSurface: "#1A1A1A",
  surfaceSecondary: "#F5F5F7",
  onSurfaceSecondary: "#333333",
  surfaceTertiary: "#EBEBEF",
  onSurfaceTertiary: "#4A4A4A",
  surfaceInverse: "#1A1A1A",
  onSurfaceInverse: "#FFFFFF",
  muted: "#5F5F66",

  brand: "#FF5A00",
  onBrand: "#1A1A1A",
  brandPrimary: "#FF5A00",
  onBrandPrimary: "#1A1A1A",
  brandSecondary: "#FFF0E5",
  onBrandSecondary: "#A33A00",
  brandTertiary: "#FFE1CC",
  onBrandTertiary: "#8A3000",

  // status colours are soft tints; the matching on* colour is the readable (AA) text/icon colour
  success: "#E5F5E9",
  onSuccess: "#0F5132",
  warning: "#FFF4E5",
  onWarning: "#663C00",
  error: "#FDEAEA",
  onError: "#842029",
  info: "#E5F0FF",
  onInfo: "#004085",

  border: "#E5E5EA",
  borderStrong: "#C7C7CC",
  divider: "#E5E5EA",
  overlay: "rgba(0,0,0,0.4)",
};

export type ThemeColors = typeof light;

// Dark: lifted greys (not inverted), same brand orange, tinted status colours with light AA text.
const dark: ThemeColors = {
  surface: "#121212",
  onSurface: "#F2F2F2",
  surfaceSecondary: "#1E1E1E",
  onSurfaceSecondary: "#E0E0E0",
  surfaceTertiary: "#2C2C2E",
  onSurfaceTertiary: "#CCCCCC",
  surfaceInverse: "#F5F5F7",
  onSurfaceInverse: "#121212",
  muted: "#A8A8AE",

  brand: "#FF5A00",
  onBrand: "#1A1A1A",
  brandPrimary: "#FF6A1A",
  onBrandPrimary: "#1A1A1A",
  brandSecondary: "#3A1A08",
  onBrandSecondary: "#FFC9A6",
  brandTertiary: "#4D1B00",
  onBrandTertiary: "#FFB885",

  success: "#143324",
  onSuccess: "#8FD3B0",
  warning: "#3A2E05",
  onWarning: "#FFDA6A",
  error: "#3A1A1C",
  onError: "#F4A3AA",
  info: "#0B2A4A",
  onInfo: "#9CC3FF",

  border: "#38383A",
  borderStrong: "#545456",
  divider: "#2C2C2E",
  overlay: "rgba(0,0,0,0.6)",
};

export const defaultScheme = "light" satisfies ColorScheme;

export const themes: { light: ThemeColors; dark?: ThemeColors } = { light, dark };

// ---- user theme preference (Light / Dark / Auto), persisted on the device across logout/login ----
export type ThemePref = "light" | "dark" | "auto";
const PREF_KEY = "rf.themePref";
let pref: ThemePref = "light";
const listeners = new Set<() => void>();

export function setColorScheme(scheme: ColorScheme | null) {
  if (Platform.OS === "web") return; // react-native-web has no override; useTheme resolves the pref itself
  try {
    Appearance.setColorScheme?.(scheme ?? "unspecified");
  } catch {}
}

export function setThemePref(p: ThemePref) {
  pref = p;
  setColorScheme(p === "auto" ? null : p); // native chrome (keyboard, alerts) follows too
  AsyncStorage.setItem(PREF_KEY, p).catch(() => {});
  listeners.forEach((l) => l());
}

export async function loadThemePref() {
  try {
    const v = await AsyncStorage.getItem(PREF_KEY);
    if (v === "light" || v === "dark" || v === "auto") pref = v;
  } catch {}
  setColorScheme(pref === "auto" ? null : pref);
  listeners.forEach((l) => l());
}

export function useThemePref(): ThemePref {
  return useSyncExternalStore(
    (l) => (listeners.add(l), () => listeners.delete(l)),
    () => pref,
    () => pref,
  );
}

export const colors = light;

export function useTheme(): { scheme: ColorScheme; colors: ThemeColors } {
  const p = useThemePref();
  const system = useColorScheme();
  const scheme: ColorScheme = p === "auto" ? (system === "dark" ? "dark" : "light") : p;
  return { scheme, colors: themes[scheme] ?? themes.light };
}

type NamedStyles<T> = { [P in keyof T]: ViewStyle | TextStyle | ImageStyle };

export function makeStyles<T extends NamedStyles<T> | NamedStyles<any>>(
  factory: (colors: ThemeColors) => T & NamedStyles<any>,
): () => T {
  return function useStyles(): T {
    const { colors } = useTheme();
    return useMemo(() => StyleSheet.create(factory(colors)), [colors]);
  };
}

// Archivo for headings only; body/UI text uses the platform system font (no extra font downloads).
export const fonts = {
  display: "Archivo",
  text: Platform.select({ ios: "System", android: "sans-serif", default: "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif" }) as string,
};
export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 };
export const BORDER = 1;
export const radius = { sm: 6, md: 10, lg: 14, pill: 999 };
/** Centred max width so web layouts use space well without over-long lines. */
export const CONTENT_MAX = 1200;
