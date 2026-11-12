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
