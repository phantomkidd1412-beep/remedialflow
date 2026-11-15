// Installer dashboard grouping helpers: town/city from the existing address, and the installer's own NC counts.
import type { ProjectRow } from "@/src/projects";

export type JobRow = ProjectRow & { my_statuses?: string[]; my_nc_numbers?: string[]; my_schedule?: string | null };

const PENDING = new Set(["open", "pending", "in_progress", "unassigned"]);
const REVIEW = new Set(["assessing", "employee_review", "admin_review", "potential_noncompliance", "ai_approved", "ai_verified_signoff"]);
const REVISIT = new Set(["rejected", "more_evidence"]);
const COUNTIES = /^(bedfordshire|berkshire|buckinghamshire|cambridgeshire|cheshire|cornwall|cumbria|derbyshire|devon|dorset|durham|essex|gloucestershire|hampshire|herefordshire|hertfordshire|kent|lancashire|leicestershire|lincolnshire|norfolk|northamptonshire|northumberland|nottinghamshire|oxfordshire|rutland|shropshire|somerset|staffordshire|suffolk|surrey|sussex|east sussex|west sussex|warwickshire|wiltshire|worcestershire|yorkshire|north yorkshire|south yorkshire|west yorkshire|east riding of yorkshire|greater london|greater manchester|merseyside|tyne and wear|west midlands|uk|united kingdom|england|wales|scotland)$/i;

/** Town/city from a UK address: the last comma part after the street that is not a county or postcode.
 *  Case/spacing-normalised so "luton" and "Luton " give one town. */
export function townOf(address: string): string {
  const parts = address.split(",").map((x) => x.trim().replace(/\s+/g, " ")).filter(Boolean).slice(1)
    .filter((x) => !COUNTIES.test(x) && !/^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$/i.test(x));
  const t = parts[parts.length - 1];
  return t ? t.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase()) : "Other";
}

/** Installer's own NC counts. Attempted (= addressed) = evidence submitted at least once. Outstanding = not yet approved. */
export function ncCounts(st: string[]) {
  const pending = st.filter((x) => PENDING.has(x)).length;
  const done = st.filter((x) => x === "approved").length;
  return {
    total: st.length, pending, attempted: st.length - pending, done, outstanding: st.length - done,
    review: st.filter((x) => REVIEW.has(x)).length, revisit: st.filter((x) => REVISIT.has(x)).length,
  };
}

/** One clear project status for the installer from their own NC statuses. */
export function jobStatus(st: string[]): { label: string; badge: string } {
  const n = ncCounts(st);
  if (n.revisit) return { label: "Revisit required", badge: "rejected" };
  if (n.total && n.done === n.total) return { label: "Completed", badge: "approved" };
  if (n.review && !n.pending) return { label: "In review", badge: "employee_review" };
  if (n.attempted) return { label: "In progress", badge: "in_progress" };
  return { label: "Not started", badge: "pending" };
}

export type Town = { name: string; jobs: JobRow[]; outstanding: number; revisit: number; done: number; attempted: number; total: number };

/** One entry per town (never duplicated), alphabetical, "Other" last. */
export function groupTowns(rows: JobRow[]): Town[] {
  const m: Record<string, JobRow[]> = {};
  rows.forEach((r) => (m[townOf(r.address)] ??= []).push(r));
  return Object.keys(m).sort((a, b) => (a === "Other" ? 1 : b === "Other" ? -1 : a.localeCompare(b))).map((name) => {
    const n = ncCounts(m[name].flatMap((r) => r.my_statuses ?? []));
    return { name, jobs: m[name], outstanding: n.outstanding, revisit: n.revisit, done: n.done, attempted: n.attempted, total: n.total };
  });
}
