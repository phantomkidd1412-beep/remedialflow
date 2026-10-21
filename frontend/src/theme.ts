// Design tokens (from /app/design_guidelines.json). Light + dark themes, user-selectable (Light / Dark / Auto).
import { useMemo, useSyncExternalStore } from "react";
import { Appearance, Platform, StyleSheet, useColorScheme } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";

export type ColorScheme = "light" | "dark";

const light = {
  surface: "#FFFFFF",
  onSurface: "#0A0A0A",
  surfaceSecondary: "#F5F5F5",
  onSurfaceSecondary: "#0A0A0A",
  surfaceTertiary: "#E0E0E0",
  onSurfaceTertiary: "#0A0A0A",
  surfaceInverse: "#0A0A0A",
  onSurfaceInverse: "#FFFFFF",
  muted: "#757575",

  brand: "#FF5A00",
  onBrand: "#0A0A0A",
  brandPrimary: "#FF5A00",
  onBrandPrimary: "#0A0A0A",
  brandSecondary: "#0A0A0A",
  onBrandSecondary: "#FFFFFF",
  brandTertiary: "#FFDCC2",
  onBrandTertiary: "#B33F00",

  success: "#00E676",
  onSuccess: "#0A0A0A",
  warning: "#FFEA00",
  onWarning: "#0A0A0A",
  error: "#FF1744",
  onError: "#FFFFFF",
  info: "#E0E0E0",
  onInfo: "#0A0A0A",

  border: "#0A0A0A",
  borderStrong: "#0A0A0A",
  divider: "#0A0A0A",
  overlay: "rgba(10,10,10,0.72)",
};

export type ThemeColors = typeof light;

// Dark: same brutalist language inverted (near-black surfaces, light borders/text); brand/status colours unchanged.
const dark: ThemeColors = {
  surface: "#0A0A0A",
  onSurface: "#F2F2F2",
  surfaceSecondary: "#1A1A1A",
  onSurfaceSecondary: "#F2F2F2",
  surfaceTertiary: "#2A2A2A",
  onSurfaceTertiary: "#F2F2F2",
  surfaceInverse: "#F2F2F2",
  onSurfaceInverse: "#0A0A0A",
  muted: "#A6A6A6",

  brand: "#FF5A00",
  onBrand: "#0A0A0A",
  brandPrimary: "#FF5A00",
  onBrandPrimary: "#0A0A0A",
  brandSecondary: "#F2F2F2",
  onBrandSecondary: "#0A0A0A",
  brandTertiary: "#3D1E0A",
  onBrandTertiary: "#FFB48A",

  success: "#00E676",
  onSuccess: "#0A0A0A",
  warning: "#FFEA00",
  onWarning: "#0A0A0A",
  error: "#FF4569",
  onError: "#FFFFFF",
  info: "#2A2A2A",
  onInfo: "#F2F2F2",

  border: "#E0E0E0",
  borderStrong: "#F2F2F2",
  divider: "#E0E0E0",
  overlay: "rgba(0,0,0,0.8)",
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

export function makeStyles<T extends StyleSheet.NamedStyles<T> | StyleSheet.NamedStyles<any>>(
  factory: (colors: ThemeColors) => T & StyleSheet.NamedStyles<any>,
): () => T {
  return function useStyles(): T {
    const { colors } = useTheme();
    return useMemo(() => StyleSheet.create(factory(colors)), [colors]);
  };
}

export const fonts = { display: "Archivo", mono: "SpaceMono" };
export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 };
export const BORDER = 2;
