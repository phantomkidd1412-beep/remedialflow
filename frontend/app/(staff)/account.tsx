import { useRouter } from "expo-router";
import { useAuth } from "@/src/auth";
import { openExport } from "@/src/projects";
import { ProfileView, MenuItem } from "@/src/components/ProfileView";
import { useToast } from "@/src/ui";

export default function StaffAccount() {
  const router = useRouter();
  const toast = useToast();
  const { user } = useAuth();
  const menu: MenuItem[] = [{ label: "New project", icon: "plus-square", onPress: () => router.push("/project/new"), testID: "menu-new-project" }];
  if (user?.role === "admin") {
    menu.push(
      { label: "Users & access", icon: "users", onPress: () => router.push("/admin/users"), testID: "menu-users" },
      { label: "Evidence rules & payments", icon: "sliders", onPress: () => router.push("/admin/settings"), testID: "menu-settings" },
      { label: "Export all metadata (JSON)", icon: "download", onPress: () => openExport().catch((e) => toast(e.message)), testID: "menu-export-all" },
    );
  }
  return <ProfileView menu={menu} />;
}
