import { FlatList, Pressable, RefreshControl, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyboardAvoidingView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api";
import { Empty, ErrorBlock, Loading, fmtDate, useToast } from "@/src/ui";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { DOC_CATS, DOC_STATUS, TechDoc, UploadBox, ver } from "@/src/components/DocUpload";
import { BORDER, radius, fonts, makeStyles, space, useTheme } from "@/src/theme";

/** Admin: Master Documents Library (PAS, TrustMark, Building Regulations, manufacturer instructions). */
export default function Documents() {
  const s = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["documents"], queryFn: () => api<TechDoc[]>("/admin/documents") });
  return (
    <KeyboardAvoidingView behavior="padding" style={s.root}>
      <ScreenHeader title="Technical Documents" back>
        <Text style={s.hint}>Approved documents are searched (by wording, no AI) when an NC is assessed or its checklist drafted. Staff see the title, version, clause and page.</Text>
      </ScreenHeader>
      {q.isError ? (
        <View style={{ padding: space.lg }}><ErrorBlock message={(q.error as Error).message} onRetry={() => q.refetch()} /></View>
      ) : (
        <FlatList
          data={q.data ?? []}
          keyExtractor={(d) => d.id}
          keyboardShouldPersistTaps="handled"
          refreshControl={<RefreshControl refreshing={q.isRefetching} onRefresh={() => q.refetch()} />}
          contentContainerStyle={[s.list, { paddingBottom: insets.bottom + space.xl }]}
          ListHeaderComponent={<UploadBox onDone={(d) => { qc.invalidateQueries({ queryKey: ["documents"] }); toast(`Uploaded: ${d.pages} pages read`); router.push(`/admin/documents/${d.id}`); }} />}
          ListEmptyComponent={q.isLoading ? <Loading /> : <Empty icon="file-text" text="No technical documents yet" />}
          renderItem={({ item: d }) => (
            <Pressable style={s.card} onPress={() => router.push(`/admin/documents/${d.id}`)} testID={`doc-row-${d.id}`}>
              <View style={s.top}>
                <Text style={s.title} numberOfLines={2}>{d.title}</Text>
                <Text style={[s.status, d.status === "approved" && { backgroundColor: colors.success, color: colors.onSuccess }, d.status === "draft" && { backgroundColor: colors.warning, color: colors.onWarning }]} testID={`doc-status-${d.id}`}>{DOC_STATUS[d.status]}</Text>
              </View>
              <Text style={s.meta}>{DOC_CATS[d.category]} · {ver(d.version)} · rev {d.rev} · {d.pages} pages{d.publisher ? ` · ${d.publisher}` : ""} · {fmtDate(d.created_at)}</Text>
              {d.extract_status !== "ok" ? <Text style={[s.meta, { color: colors.error }]}>Little readable text (scanned PDF?) — retrieval may miss it</Text> : null}
            </Pressable>
          )}
        />
      )}
    </KeyboardAvoidingView>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  hint: { fontFamily: fonts.text, fontSize: 12, color: c.muted },
  list: { padding: space.lg, gap: space.md },
  card: { borderWidth: BORDER, borderColor: c.border, borderRadius: radius.md, padding: space.md, gap: 4 },
  top: { flexDirection: "row", gap: space.sm, alignItems: "flex-start" },
  title: { flex: 1, fontFamily: fonts.text, fontSize: 15, fontWeight: "700", color: c.onSurface },
  status: { fontFamily: fonts.text, fontSize: 10, fontWeight: "700", paddingHorizontal: 6, paddingVertical: 2, borderRadius: radius.sm, overflow: "hidden", backgroundColor: c.border, color: c.onSurface },
  meta: { fontFamily: fonts.text, fontSize: 11, color: c.muted },
}));
