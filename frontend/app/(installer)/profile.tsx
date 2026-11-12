import { Alert, Platform } from "react-native";
import { ProfileView } from "@/src/components/ProfileView";
import { deletePrivateBackups, localStorageUsage } from "@/src/utils/galleryBackup";

function phoneStorage() {
  if (Platform.OS === "web") return;
  const u = localStorageUsage();
  const msg = `Waiting to upload: ${u.pendingFiles} file(s), ${u.pendingMb.toFixed(1)} MB (kept until the server confirms them).\n\n`
    + `Private backup copies: ${u.backupFiles} photo(s), ${u.backupMb.toFixed(1)} MB (only made when Photos access is off).`;
  Alert.alert("Phone storage", msg, [
    { text: "Close", style: "cancel" },
    ...(u.backupFiles ? [{ text: "Delete backup copies", style: "destructive" as const, onPress: () => {
      Alert.alert("Delete backup copies?", "Only the private backup copies are deleted. Evidence waiting to upload is kept.", [
        { text: "Cancel", style: "cancel" },
        { text: "Delete", style: "destructive", onPress: () => { deletePrivateBackups(); } },
      ]);
    } }] : []),
  ]);
}

export default function InstallerProfile() {
  return <ProfileView menu={Platform.OS === "web" ? undefined : [{ label: "Phone storage", icon: "hard-drive", onPress: phoneStorage, testID: "menu-phone-storage" }]} />;
}
