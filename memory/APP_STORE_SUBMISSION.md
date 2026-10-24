# RemedialFlow — App Store submission pack (iOS 1.0.2; submit Build 104)

Build 104 changes: own RF icon + launch screen, iPhone-only. Privacy policy still has [CONFIRM] items: do NOT give Apple the URL until they are filled in.

## 1. Screenshots (genuine, captured on device via TestFlight)
Simulators aren't available in this environment, and Build 103 can't run in Xcode Simulator (TestFlight builds are device-only).
Screenshots taken on a real device with TestFlight are genuine, and they come out at the device's native resolution.

| App Store Connect slot | Device to capture on | Native PNG size (portrait) |
|---|---|---|
| iPhone 6.3" (Dynamic Island) | iPhone 15 Pro / 16 / 16 Pro / 17 | 1179 × 2556 (16 Pro: 1206 × 2622) |
| iPad 13" | NOT REQUIRED from Build 104 (iPhone-only, supportsTablet=false) | — |

Other iPads (10.9"/11") produce sizes that are **not** accepted in the 13" slot.
If you have no 13" iPad, choose one:
- (a) Borrow one.
- (b) Use a Mac with Xcode: run a development build in the "iPad Pro 13-inch" simulator.
- (c) Set `supportsTablet: false`. This needs a **new build** and removes the iPad requirement.

Steps on each device:
1. Sign in as the demo **employee** (see section 5), on a device with no notifications showing. Turn on Do Not Disturb, and use full battery and Wi-Fi.
2. Take a screenshot (side button + volume up) of each screen below.
3. AirDrop the images to your Mac, keep them as PNG (Photos → File → Export Unmodified Original), and upload them to the matching slot.
   - Dashboard: project list with progress bars (DEMO-1001 etc.)
   - Project page: NC list, progress, Require-video setting
   - NC expanded: original TrustMark wording + defect photos
   - AI assessment panel on a submitted NC (confidence, requests)
   - Review queue / evidence viewer
   - Sign in as the demo **installer**: job card with "Video required", then the camera screen (take photos of an ordinary wall vent / bracket at your office)

Never show the login screen with credentials typed in, the Users screen (real emails), or any real project.

## 2. App Review contact (App Store Connect → App Review Information)
You must enter: First name, Last name, Phone number (with country code, e.g. +44…), Email.
Use a real person who can answer Apple during review (e.g. you, info@networksurveyltd.uk). Apple does not publish these details.

## 3. Privacy policy
- Page (public, phone-friendly): `<production address>/api/privacy`. It's served by the backend and works after you redeploy.
- Preview copy that works now (temporary, don't submit it): https://field-evidence-app-1.preview.emergentagent.com/api/privacy
- Alternative/stable: upload `memory/remedialflow-privacy-policy.html` as e.g. https://www.networksurveyltd.uk/privacy
- Items you must confirm (marked [CONFIRM] in the page):
  - Company legal name/registration
  - Legal basis
  - Retention period for compliance records
  - International transfers
- App Store "App Privacy" answers:
  - Data collected: Name, Email, Photos or Videos, Precise Location, User ID, Other User Content (notes), Other Financial Info (installer earnings).
  - All of it is linked to the user and used for App Functionality.
  - Tracking: **No**.

## 4. Content Rights
- Select: **"No, it does not contain, show, or access third-party content"** only if you confirm the two points below. Otherwise select **"Yes"**, and confirm you hold the rights or permission.
  - TrustMark inspection reports and photos that users import are your business documents/third-party content. Be sure you have the right to process them.
- DONE (Build 104): app icon + launch screen replaced with the approved RF design (memory/icon-proposal). Previously: the app icon was the Emergent template "e" logo (assets/images/icon.png, 512×512).** It isn't your branding, and Apple may reject it under 2.3.8/4.1.
  - Replace it with your own 1024×1024 RemedialFlow icon. This needs a **new build** (Build 104).
- Fonts: Archivo and Space Mono (SIL Open Font License). Icons: Feather (MIT). Both are fine.
- "TrustMark" is referred to by name only. No TrustMark logo is used.

## 5. App Review demo access (create via Admin screens in the LIVE app)
1. Admin → Users & access → Add user:
   - Employee "App Review" (e.g. appreview@networksurveyltd.uk)
   - Installer "Demo Installer" (e.g. appreview.installer@networksurveyltd.uk)
   - Give each a password using the one-time code shown, then sign in once on a device to set it.
2. New project (fictional):
   - Ref DEMO-1001, address "1 Example Street, Sampletown", postcode "AB1 2CD", installer = Demo Installer, Require video ON.
   - NC-01 "Insulation continuity not maintained around external wall bracket (EWI)".
   - NC-02 "Extract fan vent not sealed internally" (video OFF project DEMO-1002 to show optional video).
3. As Demo Installer, submit photos for NC-01 (photograph any bracket/vent) so an AI assessment appears.
4. App Store Connect → App Review Information → Sign-in required. Enter the installer and employee emails/passwords, plus these notes:
   "RemedialFlow is an internal business app for Network Survey Ltd staff and installers; accounts are created by admins. Use the installer login to capture evidence (camera, location optional) and the employee login to review AI assessments. Demo data is fictional. Payments shown are installer payout records, not in-app purchases. Account deletion: Account → Delete account."
5. Keep the demo credentials only in this App Review field, never in the description or screenshots. Delete the demo accounts with Users → Delete account after approval if you want.

## 6. Other App Store Connect items
- Support URL (required): e.g. https://www.networksurveyltd.uk (a contact page).
- Description, keywords, category (Business/Productivity), age rating questionnaire (no objectionable content → 4+), price, availability.
- Export compliance: already answered in the build (usesNonExemptEncryption=false).
- Deployment health check flagged two config warnings (an `.env` ignore pattern; the Feather package listed in app.json plugins). Build 103 built and runs, so these didn't block it. Review them before any future build.
