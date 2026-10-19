import { Redirect } from "expo-router";
import { useAuth } from "@/src/auth";
import { Loading } from "@/src/ui";

export default function Index() {
  const { user, ready } = useAuth();
  if (!ready) return <Loading testID="boot-loading" />;
  if (!user) return <Redirect href="/login" />;
  return <Redirect href={user.role === "installer" ? "/(installer)/jobs" : "/(staff)/dashboard"} />;
}
