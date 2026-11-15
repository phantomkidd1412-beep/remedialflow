// Installer job list shared by the town dashboard and the town screen: loads every page (town counts must include all
// assigned jobs) and refreshes on focus and every minute, so a reassigned project leaves/arrives without signing out.
import { useCallback, useEffect, useMemo } from "react";
import { useFocusEffect } from "expo-router";
import { useProjects } from "@/src/projects";
import type { JobRow } from "@/src/towns";

export function useJobs() {
  const q = useProjects();
  const { hasNextPage, isFetchingNextPage, fetchNextPage, refetch } = q;
  useEffect(() => {
    if (hasNextPage && !isFetchingNextPage) fetchNextPage();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);
  useFocusEffect(useCallback(() => {
    refetch();
    const t = setInterval(() => refetch(), 60_000);
    return () => clearInterval(t);
  }, [refetch]));
  const rows = useMemo(() => (q.data?.pages.flatMap((p) => p.items) ?? []) as JobRow[], [q.data]);
  return { q, rows };
}
