import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Linking, Platform } from "react-native";
import { absUrl, api } from "@/src/api";

/** Opens a short-lived signed export link (ZIP per project, or JSON metadata for everything). */
export async function openExport(projectId?: string) {
  const r = await api<{ url: string }>(`/export/link${projectId ? `?project_id=${projectId}` : ""}`);
  const url = absUrl(r.url);
  if (Platform.OS === "web") window.open(url, "_blank");
  else await Linking.openURL(url);
}

export type ProjectRow = { id: string; ref: string; address: string; postcode: string; status: string; nc_total: number; nc_done: number; nc_review: number; installer_id?: string | null; installer_name?: string | null; rate: number };

export type EvidenceFile = {
  id: string; kind: string; source: string; status: string; urls: Record<string, string>; sha256: string; size: number;
  captured_at?: string | null; server_received_at: string; tz?: string | null; lat?: number | null; lng?: number | null;
  accuracy_m?: number | null; location_verified: boolean; duration_s?: number | null; processing: string;
};

export type NCItem = {
  id: string; number: string; description: string; required_work: string; safety_critical: boolean; status: string;
  ai?: { outcome: string; confidence: number; feedback: string; concerns: string[]; model: string; at: string } | null;
  review_note: string; trustmark: EvidenceFile[]; photos: EvidenceFile[]; video: EvidenceFile | null; video_versions: number; draft_count: number;
};

export type ProjectDetail = { project: ProjectRow; ncs: NCItem[] };

export const DONE = ["approved", "ai_verified"];

export function useProjects(search = "", status = "") {
  return useInfiniteQuery({
    queryKey: ["projects", search, status],
    initialPageParam: 1,
    queryFn: ({ pageParam }) =>
      api<{ items: ProjectRow[]; has_more: boolean; total: number }>(`/projects?page=${pageParam}&limit=20&search=${encodeURIComponent(search)}&status=${status}`),
    getNextPageParam: (last, all) => (last.has_more ? all.length + 1 : undefined),
  });
}

export function useProject(id: string) {
  return useQuery({
    queryKey: ["project", id],
    queryFn: () => api<ProjectDetail>(`/projects/${id}`),
    // Poll only while AI assessment / processing is running.
    refetchInterval: (q) => {
      const d = q.state.data as ProjectDetail | undefined;
      const busy = d?.ncs.some((n) => n.status === "assessing" || n.photos.some((p) => p.processing === "pending"));
      return busy ? 4000 : false;
    },
  });
}
