// Unsaved staff evidence selections (per NC) and their flush functions. Exports/PDF downloads call
// flushSelections() first so the report is always built from the employee's final selection.
export const pending: Record<string, string[]> = {}; // unsaved local selections survive switching NCs
export const flushers: Record<string, () => Promise<void>> = {};

export async function flushSelections() {
  await Promise.all(Object.values(flushers).map((f) => f().catch(() => {})));
  const left = Object.keys(pending).length;
  if (left) throw new Error(`Photo selection for ${left} NC${left === 1 ? "" : "s"} is not saved yet. Check your connection and try again.`);
}
