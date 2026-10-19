import { createContext, PropsWithChildren, ReactNode, useCallback, useContext, useRef, useState } from "react";
import { ActivityIndicator, Modal, Pressable, StyleProp, Text, TextInput, TextInputProps, View, ViewStyle } from "react-native";
import Feather from "@react-native-vector-icons/feather";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { BORDER, fonts, makeStyles, space, ThemeColors, useTheme } from "@/src/theme";

export type IconName = React.ComponentProps<typeof Feather>["name"];
export { Feather };

const useStyles = makeStyles((c) => ({
  btn: { minHeight: 48, borderWidth: BORDER, borderColor: c.border, flexDirection: "row", alignItems: "center", justifyContent: "center", paddingHorizontal: space.lg, gap: space.sm },
  btnSm: { minHeight: 40, paddingHorizontal: space.md },
  btnText: { fontFamily: fonts.display, fontWeight: "800", fontSize: 14, letterSpacing: 0.5, textTransform: "uppercase" },
  badge: { borderWidth: BORDER, borderColor: c.border, paddingHorizontal: 6, paddingVertical: 2, alignSelf: "flex-start" },
  badgeText: { fontFamily: fonts.mono, fontSize: 11, textTransform: "uppercase" },
  barOuter: { height: 10, borderWidth: BORDER, borderColor: c.border, backgroundColor: c.surface },
  barInner: { height: "100%", backgroundColor: c.brandPrimary },
  input: { minHeight: 48, borderWidth: BORDER, borderColor: c.border, paddingHorizontal: space.md, fontFamily: fonts.mono, fontSize: 14, color: c.onSurface, backgroundColor: c.surface },
  label: { fontFamily: fonts.mono, fontSize: 11, color: c.muted, textTransform: "uppercase", marginBottom: space.xs },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: space.xl, gap: space.md, backgroundColor: c.surface },
  mono: { fontFamily: fonts.mono, fontSize: 13, color: c.onSurface },
  errBox: { backgroundColor: c.error, padding: space.lg, gap: space.md, borderWidth: BORDER, borderColor: c.border },
  errText: { fontFamily: fonts.mono, color: c.onError, fontSize: 13 },
  toast: { position: "absolute", left: space.lg, right: space.lg, backgroundColor: c.surfaceInverse, padding: space.md, borderWidth: BORDER, borderColor: c.border },
  toastText: { fontFamily: fonts.mono, color: c.onSurfaceInverse, fontSize: 13 },
  sheetBg: { flex: 1, backgroundColor: c.overlay, justifyContent: "flex-end" },
  sheet: { backgroundColor: c.surface, borderTopWidth: BORDER, borderColor: c.border, padding: space.lg, gap: space.md },
  sheetTitle: { fontFamily: fonts.display, fontWeight: "800", fontSize: 20, color: c.onSurface },
  h1: { fontFamily: fonts.display, fontWeight: "900", fontSize: 24, color: c.onSurface, letterSpacing: -0.5 },
}));

type Variant = "primary" | "dark" | "outline" | "danger" | "success";
const variantColors = (c: ThemeColors, v: Variant) =>
  ({
    primary: [c.brandPrimary, c.onBrandPrimary],
    dark: [c.brandSecondary, c.onBrandSecondary],
    outline: [c.surface, c.onSurface],
    danger: [c.error, c.onError],
    success: [c.success, c.onSuccess],
  })[v];

export function Btn(p: { title: string; onPress?: () => void; variant?: Variant; icon?: IconName; disabled?: boolean; loading?: boolean; small?: boolean; testID?: string; style?: StyleProp<ViewStyle> }) {
  const s = useStyles();
  const { colors } = useTheme();
  const [bg, fg] = variantColors(colors, p.variant ?? "primary");
  const off = p.disabled || p.loading;
  return (
    <Pressable
      testID={p.testID}
      onPress={off ? undefined : p.onPress}
      style={({ pressed }) => [s.btn, p.small && s.btnSm, { backgroundColor: bg, opacity: off ? 0.45 : pressed ? 0.75 : 1 }, p.style]}
    >
      {p.loading ? <ActivityIndicator color={fg} /> : p.icon ? <Feather name={p.icon} size={18} color={fg} /> : null}
      <Text style={[s.btnText, { color: fg }]} numberOfLines={1}>{p.title}</Text>
    </Pressable>
  );
}

export const STATUS: Record<string, { label: string; tone: "ok" | "warn" | "bad" | "info" | "brand" }> = {
  open: { label: "To do", tone: "info" },
  assessing: { label: "AI checking", tone: "brand" },
  ai_verified: { label: "AI verified", tone: "ok" },
  approved: { label: "Internally approved", tone: "ok" },
  more_evidence: { label: "More evidence", tone: "warn" },
  manual_review: { label: "Employee review", tone: "brand" },
  employee_review: { label: "Employee review", tone: "brand" },
  admin_review: { label: "Admin review", tone: "warn" },
  unassigned: { label: "Unassigned", tone: "warn" },
  in_progress: { label: "In progress", tone: "info" },
  trustmark_resolved: { label: "TrustMark resolved", tone: "ok" },
  rejected: { label: "Rejected", tone: "bad" },
  active: { label: "Active", tone: "info" },
  awaiting_review: { label: "Awaiting review", tone: "brand" },
  completed: { label: "Completed", tone: "ok" },
  earned: { label: "Earned", tone: "brand" },
  paid: { label: "Paid", tone: "ok" },
  pending: { label: "Queued", tone: "warn" },
  uploading: { label: "Uploading", tone: "brand" },
  failed: { label: "Failed", tone: "bad" },
};

export function Badge({ status, label, testID }: { status: string; label?: string; testID?: string }) {
  const s = useStyles();
  const { colors: c } = useTheme();
  const tone = STATUS[status]?.tone ?? "info";
  const [bg, fg] = { ok: [c.success, c.onSuccess], warn: [c.warning, c.onWarning], bad: [c.error, c.onError], info: [c.info, c.onInfo], brand: [c.brandTertiary, c.onBrandTertiary] }[tone];
  return (
    <View testID={testID} style={[s.badge, { backgroundColor: bg }]}>
      <Text style={[s.badgeText, { color: fg }]}>{label ?? STATUS[status]?.label ?? status}</Text>
    </View>
  );
}

export function Progress({ value, testID }: { value: number; testID?: string }) {
  const s = useStyles();
  return (
    <View style={s.barOuter} testID={testID}>
      <View style={[s.barInner, { width: `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%` }]} />
    </View>
  );
}

export function Field({ label, ...p }: TextInputProps & { label?: string }) {
  const s = useStyles();
  const { colors } = useTheme();
  return (
    <View>
      {label ? <Text style={s.label}>{label}</Text> : null}
      <TextInput placeholderTextColor={colors.muted} {...p} style={[s.input, p.multiline && { minHeight: 80, paddingTop: space.md, textAlignVertical: "top" }, p.style]} />
    </View>
  );
}

export function Loading({ testID }: { testID?: string }) {
  const s = useStyles();
  const { colors } = useTheme();
  return (
    <View style={s.center} testID={testID ?? "loading"}>
      <ActivityIndicator size="large" color={colors.onSurface} />
    </View>
  );
}

export function ErrorBlock({ message, onRetry }: { message: string; onRetry?: () => void }) {
  const s = useStyles();
  return (
    <View style={s.errBox} testID="error-block">
      <Text style={s.errText}>{message.toUpperCase()}</Text>
      {onRetry ? <Btn title="Retry" variant="dark" small onPress={onRetry} testID="error-retry-button" /> : null}
    </View>
  );
}

export function Empty({ icon, text }: { icon: IconName; text: string }) {
  const s = useStyles();
  const { colors } = useTheme();
  return (
    <View style={[s.center, { paddingVertical: 64 }]} testID="empty-state">
      <Feather name={icon} size={48} color={colors.onSurface} />
      <Text style={s.mono}>{text.toUpperCase()}</Text>
    </View>
  );
}

export function Label({ children }: { children: ReactNode }) {
  const s = useStyles();
  return <Text style={s.label}>{children}</Text>;
}

export function H1({ children, testID }: { children: ReactNode; testID?: string }) {
  const s = useStyles();
  return <Text style={s.h1} testID={testID}>{children}</Text>;
}

// ---------- Sheet (bottom modal) ----------
export function Sheet({ visible, onClose, title, children, testID }: PropsWithChildren<{ visible: boolean; onClose: () => void; title: string; testID?: string }>) {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={s.sheetBg} onPress={onClose} testID={`${testID ?? "sheet"}-backdrop`}>
        <Pressable style={[s.sheet, { paddingBottom: insets.bottom + space.lg }]} onPress={() => {}} testID={testID}>
          <Text style={s.sheetTitle}>{title}</Text>
          {children}
        </Pressable>
      </Pressable>
    </Modal>
  );
}

// ---------- Toast ----------
const ToastCtx = createContext<(msg: string) => void>(() => {});
export function ToastProvider({ children }: PropsWithChildren) {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  const [msg, setMsg] = useState<string | null>(null);
  const t = useRef<ReturnType<typeof setTimeout> | null>(null);
  const show = useCallback((m: string) => {
    setMsg(m);
    if (t.current) clearTimeout(t.current);
    t.current = setTimeout(() => setMsg(null), 2600);
  }, []);
  return (
    <ToastCtx.Provider value={show}>
      {children}
      {msg ? (
        <View pointerEvents="none" style={[s.toast, { top: insets.top + space.sm }]} testID="toast">
          <Text style={s.toastText}>{msg}</Text>
        </View>
      ) : null}
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

export const money = (n: number, cur = "GBP") => {
  try {
    return new Intl.NumberFormat("en-GB", { style: "currency", currency: cur }).format(n || 0);
  } catch {
    return `${cur} ${(n || 0).toFixed(2)}`;
  }
};

export const fmtDate = (iso?: string | null) => {
  if (!iso) return "—";
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};
