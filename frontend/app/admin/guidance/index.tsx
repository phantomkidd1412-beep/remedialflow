import { useState } from "react";
import { FlatList, Pressable, RefreshControl, ScrollView, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { Btn, Empty, ErrorBlock, Loading, fmtDate } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

export type Guidance = {
  id: string; title: string; measure: string; keywords: string[]; defect: string; evidence_required: string; photos_to_collect: string;
  photo_details: string; video_guidance: string; insufficient_examples: string; request_example: string; references: string; review_notes: string;
  status: "draft" | "approved" | "disabled"; version: number; source?: { type: string; action?: string; reason?: string };
  created_by_name?: string; created_at: string; approved_by_name?: string; approved_at?: string | null; updated_by_name?: string; updated_at: string;
  example_photos: { id: string; url: string }[]; history?: { version: number; at: string; by_name?: string; change?: string }[];
};

const FILTERS = [["", "All"], ["draft", "Drafts"], ["approved", "Approved"], ["disabled", "Disabled"]] as const;
export const G_STATUS: Record<string, string> = { draft: "DRAFT", approved: "APPROVED", disabled: "DISABLED" };

/** Admin → AI Evidence Guidance: approved entries are given to Gemini as context when assessing matching NCs. */
export default function GuidanceList() {
  const s = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [filter, setFilter] = useState("");
  const q = useQuery({ queryKey: ["guidance", filter], queryFn: () => api<Guidance[]>(`/admin/guidance${filter ? `?status=${filter}` : ""}`) });
  return (
    <View style={s.root}>
      <ScreenHeader title="AI Evidence Guidance" back>
        <Text style={s.hint}>Approved entries guide Gemini&apos;s evidence checks for matching NCs. They add context only. They never override safety rules or human sign-off.</Text>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space.sm }}>
          {FILTERS.map(([k, l]) => (
            <Pressable key={k} onPress={() => setFilter(k)} style={[s.chip, filter === k && { backgroundColor: colors.brandSecondary, borderColor: colors.brandPrimary }]} testID={`guidance-filter-${k || "all"}`}>
              <Text style={[s.chipText, filter === k && { color: colors.onBrandSecondary }]}>{l}</Text>
            </Pressable>
          ))}
        </ScrollView>
      </ScreenHeader>
      {q.isError ? (
        <View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error).message} onRetry={() => q.refetch()} /></View>
      ) : (
        <FlatList
          data={q.data ?? []}
          keyExtractor={(g) => g.id}
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => { q.refetch(); }} />}
          contentContainerStyle={[s.list, { paddingBottom: insets.bottom + space.xl }]}
          ListHeaderComponent={
            <View style={{ gap: space.sm }}>
              <Btn title="New guidance entry" icon="plus" onPress={() => router.push("/admin/guidance/new")} testID="guidance-new" />
              <Btn title="NC evidence profiles" icon="layers" variant="outline" onPress={() => router.push("/admin/profiles")} testID="guidance-profiles" />
              <Btn title="Technical documents" icon="file-text" variant="outline" onPress={() => router.push("/admin/documents")} testID="guidance-documents" />
            </View>
          }
          ListEmptyComponent={q.isLoading ? <Loading /> : <Empty icon="book-open" text="No guidance entries yet" />}
          renderItem={({ item: g }) => (
            <Pressable style={s.card} onPress={() => router.push(`/admin/guidance/${g.id}`)} testID={`guidance-row-${g.id}`}>
              <View style={s.row}>
                <Text style={[s.status, g.status === "approved" && { backgroundColor: colors.success, color: colors.onSuccess }, g.status === "draft" && { backgroundColor: colors.warning, color: colors.onWarning }]} testID={`guidance-status-${g.id}`}>{G_STATUS[g.status]}</Text>
                {g.measure ? <Text style={s.meta}>{g.measure}</Text> : null}
                <Text style={s.meta}>v{g.version}</Text>
                {g.source?.type === "employee_feedback" ? <Text style={s.meta}>Suggested by employee</Text> : null}
              </View>
              <Text style={s.title}>{g.title}</Text>
              {g.keywords.length ? <Text style={s.meta}>Keywords: {g.keywords.join(", ")}</Text> : <Text style={s.meta}>No keywords: matches on measure only</Text>}
              <Text style={s.meta}>Updated {fmtDate(g.updated_at)}{g.updated_by_name ? ` by ${g.updated_by_name}` : ""}{g.approved_by_name && g.status === "approved" ? ` · approved by ${g.approved_by_name}` : ""}</Text>
            </Pressable>
          )}
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  list: { padding: space.lg, gap: space.sm },
  hint: { fontSize: 13, color: c.muted },
  chip: { height: 36, paddingHorizontal: space.md, borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, justifyContent: "center" },
  chipText: { fontFamily: fonts.text, fontSize: 12, color: c.onSurface },
  card: { borderWidth: BORDER, borderRadius: radius.md, borderColor: c.border, padding: space.md, gap: space.xs },
  row: { flexDirection: "row", gap: space.sm, alignItems: "center", flexWrap: "wrap" },
  status: { fontFamily: fonts.text, fontSize: 10, fontWeight: "700", paddingHorizontal: space.sm, paddingVertical: 2, borderRadius: radius.md, overflow: "hidden", backgroundColor: c.surfaceSecondary, color: c.onSurface },
  title: { fontFamily: fonts.display, fontWeight: "700", fontSize: 16, color: c.onSurface },
  meta: { fontSize: 12, color: c.muted },
}));
