import { useRouter } from "expo-router";
import { useAuth } from "@/src/auth";
import { openExport } from "@/src/projects";
import { ProfileView, MenuItem } from "@/src/components/ProfileView";
import { useToast } from "@/src/ui";

export default function StaffAccount() {
  const router = useRouter();
  const toast = useToast();
  const { user } = useAuth();
  const menu: MenuItem[] = [
    { label: "New project", icon: "plus-square", onPress: () => router.push("/project/new"), testID: "menu-new-project" },
    { label: "Work tracker", icon: "bar-chart-2", onPress: () => router.push("/tracker"), testID: "menu-tracker" },
  ];
  if (user?.role === "admin") {
    menu.push(
      { label: "Users & access", icon: "users", onPress: () => router.push("/admin/users"), testID: "menu-users" },
      { label: "Evidence rules & payments", icon: "sliders", onPress: () => router.push("/admin/settings"), testID: "menu-settings" },
      { label: "AI Evidence Guidance", icon: "book-open", onPress: () => router.push("/admin/guidance"), testID: "menu-guidance" },
      { label: "Deleted projects", icon: "trash-2", onPress: () => router.push("/admin/deleted"), testID: "menu-deleted" },
      { label: "Activity log", icon: "activity", onPress: () => router.push("/admin/activity"), testID: "menu-activity" },
      { label: "Export all metadata (JSON)", icon: "download", onPress: () => openExport().catch((e) => toast(e.message)), testID: "menu-export-all" },
    );
  }
  return <ProfileView menu={menu} />;
}
