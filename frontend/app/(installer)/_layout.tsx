import { Redirect } from "expo-router";
import { useAuth } from "@/src/auth";
import { TabsShell } from "@/src/components/TabsShell";

export default function InstallerLayout() {
  const { user, ready } = useAuth();
  if (ready && !user) return <Redirect href="/login" />;
  return (
    <TabsShell
      tabs={[
        { name: "jobs", label: "Jobs", icon: "briefcase", sf: "briefcase.fill" },
        { name: "earnings", label: "Earnings", icon: "credit-card", sf: "sterlingsign.circle.fill" },
        { name: "profile", label: "Profile", icon: "user", sf: "person.fill" },
      ]}
    />
  );
}
