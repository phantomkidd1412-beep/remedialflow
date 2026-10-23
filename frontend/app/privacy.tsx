import { Linking, ScrollView, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { COMPANY, SUPPORT_EMAIL } from "@/src/support";
import { fonts, makeStyles, space } from "@/src/theme";

const UPDATED = "June 2026";
const SECTIONS: [string, string[]][] = [
  ["Who we are", [`RemedialFlow is operated by ${COMPANY}. It is a business app used by our staff and contracted installers to record and review remediation work on properties. Accounts are created by our administrators.`]],
  ["What we collect", [
    "Account details: your name, work email address, role, and an encrypted (hashed) password.",
    "Installer payment details recorded by our office: pay type, job rates and earnings records.",
    "Work evidence you upload: photos, videos and notes, with the date, time and (if you allow it) the GPS location where they were taken.",
    "Project information: property addresses, project references and inspection reports about the work.",
    "Technical data: sign-in sessions, a push-notification device token (if you allow notifications) and an audit log of actions in the app.",
  ]],
  ["How we use it", [
    "To assign work, receive and review remediation evidence, and produce evidence packs for compliance bodies such as TrustMark.",
    "To calculate installer payments.",
    "To send you notifications about work that needs your attention.",
    "To check evidence automatically with AI (Google Gemini). Only the evidence and NC details needed for the check are sent. They are not used to sell or advertise.",
    "We do not use your data for advertising, and we do not track you across other apps or websites.",
  ]],
  ["Where it is stored", [
    "Evidence files are stored in our company Dropbox account. Account and project records are stored in our secure application database.",
    "Data is only shared with the service providers needed to run the app (hosting, Dropbox storage, AI evidence checks, email and notifications), and with compliance bodies when we submit evidence packs.",
  ]],
  ["How long we keep it", [
    "Work evidence, reviews and audit history are kept as long as they are needed as compliance records for the properties concerned.",
    "Account details are kept while your account is active.",
  ]],
  ["Deleting your account", [
    "You can delete your account at any time in the app: Account → Delete account. Our administrators can also delete it for you.",
    "Deleting your account removes your name, email address, password, sign-in sessions and notification devices, and closes the account for good. The work evidence you submitted stays as part of the property's compliance record, labelled \"Deleted user\".",
  ]],
  ["Your rights", [`You can ask for a copy of your data, ask us to correct it, or ask questions about this policy by emailing ${SUPPORT_EMAIL}. You also have the right to complain to the Information Commissioner's Office (ico.org.uk).`]],
];

/** Public page (no sign-in needed). Linked from the sign-in and account screens. */
export default function Privacy() {
  const s = useStyles();
  const insets = useSafeAreaInsets();
  return (
    <View style={s.root}>
      <ScreenHeader title="PRIVACY POLICY" back />
      <ScrollView contentContainerStyle={[s.content, { paddingBottom: insets.bottom + space.xl }]} testID="privacy-policy">
        <Text style={s.muted}>Last updated {UPDATED}</Text>
        {SECTIONS.map(([h, ps]) => (
          <View key={h} style={{ gap: space.xs }}>
            <Text style={s.h}>{h}</Text>
            {ps.map((p) => <Text key={p} style={s.p}>{p}</Text>)}
          </View>
        ))}
        <Text style={s.p}>Contact: <Text style={s.link} onPress={() => Linking.openURL(`mailto:${SUPPORT_EMAIL}`)}>{SUPPORT_EMAIL}</Text></Text>
      </ScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surface },
  content: { padding: space.lg, gap: space.lg },
  h: { fontFamily: fonts.display, fontWeight: "800", fontSize: 17, color: c.onSurface },
  p: { fontSize: 14, lineHeight: 21, color: c.onSurface },
  muted: { fontSize: 12, color: c.muted },
  link: { color: c.onBrandSecondary, fontWeight: "700" },
}));
