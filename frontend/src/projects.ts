import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { cached } from "@/src/offlineCache";
import { Linking, Platform } from "react-native";
import { absUrl, api } from "@/src/api";

/**
 * Opens a signed file URL. Web: fetch it and save via a same-origin blob link, because the
 * preview iframe blocks popups / cross-site framing ("refused to connect"). Native: system browser.
 */
async function openSigned(path: string, filename: string, inline = false) {
  const url = absUrl(path);
  if (Platform.OS !== "web") return Linking.openURL(url);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  const blobUrl = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = blobUrl;
  if (inline) a.target = "_blank";
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
}

/** Downloads a short-lived signed export (ZIP per project, or JSON metadata for everything). */
/** Saves the current pack to the project's Dropbox folder; the server reuses an identical earlier export. */
export async function exportToDropbox(projectId: string) {
  return api<{ status: "exported" | "already_exported"; location: string | null; saved_at: string }>(`/projects/${projectId}/export-dropbox`, { method: "POST" });
}

export async function openExport(projectId?: string) {
  const r = await api<{ url: string }>(`/export/link${projectId ? `?project_id=${projectId}` : ""}`);
  await openSigned(r.url, projectId ? "project-evidence.zip" : "remedialflow-metadata.json");
}

/** Work Tracker export (CSV/Excel) with the current filters. */
export async function openTrackerExport(fmt: "csv" | "xlsx", params: Record<string, string>) {
  const qs = new URLSearchParams({ fmt, ...params }).toString();
  const r = await api<{ url: string }>(`/tracker/export-link?${qs}`);
  await openSigned(r.url, `work-tracker.${fmt}`);
}

export type RateRow = { installer_id: string; name: string; pay_type: string; rate: number | null; earning: { status: string; amount: number } | null };
export type ProjectRow = { id: string; ref: string; address: string; postcode: string; status: string; nc_total: number; nc_done: number; nc_review: number; installer_id?: string | null; installer_name?: string | null; rate: number; rate_rows?: RateRow[]; schedule_rows?: { installer_id: string; name: string; date: string | null }[]; source_report?: { filename?: string } | null; needs_review?: boolean; installer_names?: string[]; completed_at?: string | null; resolved?: { at: string; by_name: string } | null; resolution_history?: { at: string; by_name: string }[] };

export type EvidenceFile = {
  id: string; kind: string; source: string; status: string; urls: Record<string, string>; sha256: string; size: number;
  captured_at?: string | null; server_received_at: string; tz?: string | null; lat?: number | null; lng?: number | null;
  accuracy_m?: number | null; location_verified: boolean; duration_s?: number | null; processing: string;
  exclude_reason?: string | null; playback?: string | null; stage?: "pre" | "mid" | "final" | null; stage_source?: string | null; pdf_include?: boolean | null;
};

/** Server-configured per-video size limit (shown before recording/selection). */
export function useMediaConfig() {
  const q = useQuery({ queryKey: ["media-config"], queryFn: () => api<{ max_video_mb: number }>("/config/media"), staleTime: Infinity });
  return { maxVideoMb: q.data?.max_video_mb ?? 500 };
}

export type NCItem = {
  id: string; number: string; description: string; original_text?: string; required_work: string; safety_critical: boolean; status: string;
  assigned_installer_id?: string | null; assigned_installer_name?: string | null; approved_by?: string | null; approved_at?: string | null; rejection_source?: string | null; reopen_history?: { at: string; source: string; note?: string }[];
  ai?: { outcome: string; confidence: number | null; status?: string; feedback: string; concerns: string[]; requests?: string[]; blocked?: string[]; model: string; at: string } | null;
  installer_note?: string; requires_stages?: boolean; export_note_source?: string; pdf_ai?: { uncertain?: boolean } | null;
  review_note: string; export_note?: string; trustmark: EvidenceFile[]; trustmark_hidden?: EvidenceFile[]; photos: EvidenceFile[]; videos?: EvidenceFile[]; video: EvidenceFile | null; video_versions: number; draft_count: number;
  text_history?: { original_text: string; replaced_at: string }[];
};

export type ProjectDetail = { project: ProjectRow; ncs: NCItem[] };

export const DONE = ["approved"];

export const STAGES = [["pre", "Pre"], ["mid", "Mid"], ["final", "Final"]] as const;
/** Clear AI decision labels (no fake 0% when AI did not run). */
export const AI_STATUS: Record<string, string> = {
  ai_approved: "AI Approved", ai_verified_signoff: "AI Technically Verified - Human Sign-off Required", more_evidence: "More Evidence Required",
  employee_review: "Employee Review Required", potential_noncompliance: "Potential Noncompliance", pending: "AI Assessment Pending", unavailable: "AI Unavailable",
};

export function useProjects(search = "", status = "") {
  return useInfiniteQuery({
    queryKey: ["projects", search, status],
    initialPageParam: 1,
    queryFn: ({ pageParam }) => {
      const get = () => api<{ items: ProjectRow[]; has_more: boolean; total: number }>(`/projects?page=${pageParam}&limit=20&search=${encodeURIComponent(search)}&status=${status}`);
      // first page of the unfiltered job list stays available offline
      return pageParam === 1 && !search ? cached(`projects:${status}`, get) : get();
    },
    getNextPageParam: (last, all) => (last.has_more ? all.length + 1 : undefined),
  });
}

export function useProject(id: string) {
  return useQuery({
    queryKey: ["project", id],
    queryFn: () => cached(`project:${id}`, () => api<ProjectDetail>(`/projects/${id}`)),
    // Poll only while AI assessment / processing is running.
    refetchInterval: (q) => {
      const d = q.state.data as ProjectDetail | undefined;
      const busy = d?.ncs.some((n) => n.status === "assessing" || n.photos.some((p) => p.processing === "pending"));
      return busy ? 4000 : false;
    },
  });
}

export async function openReport(projectId: string) {
  const r = await api<{ url: string }>(`/projects/${projectId}/report-link`);
  await openSigned(r.url, "inspection-report.pdf");
}
