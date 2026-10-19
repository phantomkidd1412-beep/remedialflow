import { Redirect } from "expo-router";
import { useAuth } from "@/src/auth";
import { TabsShell } from "@/src/components/TabsShell";

export default function StaffLayout() {
  const { user, ready } = useAuth();
  if (ready && !user) return <Redirect href="/login" />;
  return (
    <TabsShell
      tabs={[
        { name: "dashboard", label: "Projects", icon: "grid", sf: "square.grid.2x2.fill" },
        { name: "review", label: "Review", icon: "check-square", sf: "checkmark.seal.fill" },
        { name: "account", label: "Account", icon: "user", sf: "person.fill" },
      ]}
    />
  );
}
