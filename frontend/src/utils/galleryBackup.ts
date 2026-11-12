// Local backup of in-app camera photos (unstamped original) to the phone's Photos/Gallery.
// Saved once, right after capture, so upload retries never create duplicates. Gallery picks are never re-saved.
// No permission -> the photo is kept in app-private storage (Documents/backup), never uploaded anywhere else.
import { Platform } from "react-native";

export type BackupPerm = "granted" | "undetermined" | "denied" | "blocked";

/* eslint-disable @typescript-eslint/no-require-imports */
const ML = () => require("expo-media-library/legacy");
/* eslint-enable @typescript-eslint/no-require-imports */

function toPerm(r: { granted: boolean; status: string; canAskAgain: boolean }): BackupPerm {
  if (r.granted) return "granted";
  if (r.status === "undetermined") return "undetermined";
  return r.canAskAgain ? "denied" : "blocked";
}

export async function backupPermission(): Promise<BackupPerm> {
  if (Platform.OS === "web") return "granted";
  try {
    return toPerm(await ML().getPermissionsAsync(true, ["photo"]));
  } catch {
    return "blocked";
  }
}

export async function requestBackupPermission(): Promise<BackupPerm> {
  if (Platform.OS === "web") return "granted";
  try {
    return toPerm(await ML().requestPermissionsAsync(true, ["photo"]));
  } catch {
    return "blocked";
  }
}

function keepPrivate(uri: string, id: string) {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { File, Directory, Paths } = require("expo-file-system");
  const dir = new Directory(Paths.document, "backup");
  if (!dir.exists) dir.create({ intermediates: true, idempotent: true });
  new File(uri).copy(new File(dir, `${id}.jpg`));
}

/** Phone storage used by evidence still waiting to upload (never deleted here) and by private backup copies. */
export function localStorageUsage(): { pendingMb: number; pendingFiles: number; backupMb: number; backupFiles: number } {
  const out = { pendingMb: 0, pendingFiles: 0, backupMb: 0, backupFiles: 0 };
  if (Platform.OS === "web") return out;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { Directory, File, Paths } = require("expo-file-system");
    for (const [name, mb, n] of [["evidence", "pendingMb", "pendingFiles"], ["backup", "backupMb", "backupFiles"]] as const) {
      const dir = new Directory(Paths.document, name);
      if (!dir.exists) continue;
      for (const f of dir.list()) {
        if (f instanceof File) {
          out[mb] += (f.size ?? 0) / 1048576;
          out[n] += 1;
        }
      }
    }
  } catch {}
  return out;
}

/** Deletes ONLY the private backup copies (Documents/backup). Evidence waiting to upload lives in Documents/evidence
 *  and is never touched; photos already saved to the Photos app are managed there by the user. */
export function deletePrivateBackups(): number {
  if (Platform.OS === "web") return 0;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { Directory, File, Paths } = require("expo-file-system");
    const dir = new Directory(Paths.document, "backup");
    if (!dir.exists) return 0;
    let n = 0;
    for (const f of dir.list()) {
      if (f instanceof File) {
        f.delete();
        n += 1;
      }
    }
    return n;
  } catch {
    return 0;
  }
}

/** Returns where the backup went. Never throws: a failed backup must not block capturing evidence. */
export async function backupPhoto(uri: string, id: string): Promise<"gallery" | "private" | "skipped"> {
  if (Platform.OS === "web") return "skipped";
  try {
    if ((await backupPermission()) === "granted") {
      await ML().saveToLibraryAsync(uri);
      return "gallery";
    }
  } catch {}
  try {
    keepPrivate(uri, id);
    return "private";
  } catch {
    return "skipped";
  }
}
