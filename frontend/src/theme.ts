// Design tokens (from /app/design_guidelines.json). Light theme only — Brutalist field tool.
import { useMemo } from "react";
import { Appearance, StyleSheet, useColorScheme } from "react-native";

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

export const defaultScheme = "light" satisfies ColorScheme;

export const themes: { light: ThemeColors; dark?: ThemeColors } = { light };

export function setColorScheme(scheme: ColorScheme | null) {
  Appearance.setColorScheme?.(scheme ?? "unspecified");
}

setColorScheme?.(themes.dark ? null : defaultScheme);

export const colors = light;

export function useTheme(): { scheme: ColorScheme; colors: ThemeColors } {
  const system = useColorScheme();
  const scheme: ColorScheme = (system === "light" || system === "dark") && themes[system] ? system : defaultScheme;
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
