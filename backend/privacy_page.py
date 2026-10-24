"""Public, mobile-friendly privacy policy page for App Store Connect: GET /api/privacy (no sign-in).
Wording is based on the implemented data handling; items marked [CONFIRM] need the owner's confirmation."""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/api")

UPDATED = "June 2026"
CONTROLLER = "Network Survey Ltd"
CONTACT = "info@networksurveyltd.uk"

SECTIONS = [
    ("Who we are", [
        f"RemedialFlow is a business app operated by {CONTROLLER} (\"we\", \"us\"). Our staff and contracted installers use it to record, "
        "review and export evidence of remediation work on properties. It is not for consumers. Accounts are created only by our administrators.",
        f"For data protection purposes, {CONTROLLER} is the controller of the personal data described here. Contact: {CONTACT}.",
    ]),
    ("Information we collect", [
        "<b>Account and sign-in:</b> your name, work email address and role (admin, employee or installer). We store a hashed (one-way encrypted) "
        "password, not the password itself. We also keep sign-in session records. If a sign-in attempt fails, your IP address is held briefly "
        "to protect accounts from repeated password guessing.",
        "<b>Installer payments:</b> pay type, job rates, earnings and payment records entered by our office.",
        "<b>Project and property information:</b> property addresses and postcodes, project references, and inspection reports with their "
        "non-compliance (NC) descriptions and reference photos. Some of this information relates to the property's occupiers or owners.",
        "<b>Photos, videos and notes:</b> evidence you capture or upload in the app, with the capture date, time and time zone, and file details.",
        "<b>Location:</b> if you allow location access, the app records the GPS position (latitude, longitude and accuracy) when evidence is "
        "captured with the in-app camera. This is used to confirm where evidence was taken, and the position may be printed on evidence images. "
        "Location is never collected in the background, and the app still works if you refuse it.",
        "<b>Notifications:</b> if you allow notifications, we store your device's push notification token so we can send you work alerts.",
        "<b>Activity records:</b> an audit log of actions in the app, such as uploads, reviews, approvals and exports, so that compliance records "
        "can be checked.",
        "We do not use advertising, analytics or tracking tools, and we do not track you across other companies' apps or websites.",
    ]),
    ("How we use information", [
        "To run the remediation workflow: assigning work, receiving and reviewing evidence, and producing evidence packs (PDF and ZIP) for "
        "compliance bodies such as TrustMark.",
        "To check evidence automatically with AI (see below), and to calculate installer payments.",
        "To send password-reset emails and in-app or push notifications about work that needs your attention.",
        "To keep the service secure and to keep accurate compliance records.",
        "[CONFIRM] Legal basis: we process this information because it is necessary for our contracts with installers and clients, and for our "
        "legitimate interest in providing auditable remediation evidence.",
    ]),
    ("AI evidence assessment", [
        "Evidence photos, frames taken from videos, the NC descriptions and installer notes are sent to Google's Gemini AI model, through our "
        "platform provider's AI service, to check whether they show the remedial work. Property addresses and names are not included in these "
        "requests. AI results are advice only: staff review and approve them, and safety-critical items always need a person to sign them off.",
    ]),
    ("Where information is stored and who processes it", [
        "<b>Dropbox</b> (Dropbox, Inc.): evidence photos, videos and evidence-pack exports are stored in our company Dropbox account.",
        "<b>Emergent</b> (our app platform provider): hosts the app's server and database. It also provides the services we use for AI requests "
        "to Google Gemini, password-reset email and push notifications. Evidence uploaded before we moved to Dropbox may still be held in "
        "Emergent's file storage.",
        "<b>Google</b> (Gemini AI): processes evidence for the AI assessment described above.",
        "<b>Apple and Google push services</b>: deliver notifications to your device.",
        "We share evidence packs with compliance bodies such as TrustMark when we submit them. We do not sell personal data.",
        "[CONFIRM] Some of these providers may process data outside the UK. Where they do, we rely on their standard contractual safeguards.",
    ]),
    ("Security", [
        "Data is sent over encrypted HTTPS connections. Passwords are stored as one-way hashes. Access is limited by role: installers only see the "
        "work assigned to them, and evidence files are only available to signed-in, authorised users through time-limited links. "
        "No system is perfectly secure, but we take reasonable steps to protect your information.",
    ]),
    ("How long we keep information", [
        "There is currently no automatic deletion schedule. Work evidence, reviews and audit history are kept as compliance records for the "
        "properties concerned until our administrators delete them. Account details are kept while your account is active.",
        "[CONFIRM] Our retention period for compliance records: ________.",
    ]),
    ("Deleting your account", [
        "You can delete your account in the app at any time: Account (or Profile) → Delete account, then confirm with your password. "
        "Our administrators can also delete an account for you.",
        "Deleting your account permanently removes your name, email address, password, sign-in sessions and notification registration. Evidence "
        "you submitted stays as part of the property's compliance record, labelled \"Deleted user\". To ask for anything else to be removed, "
        f"email {CONTACT}.",
    ]),
    ("Your rights", [
        "Under UK data protection law, you can ask to access, correct or erase your personal data, to restrict or object to how we use it, and "
        f"to receive a copy of it. Email {CONTACT} to do this. You can also complain to the Information Commissioner's Office (ico.org.uk).",
    ]),
    ("Children", ["RemedialFlow is a workplace app and is not intended for children."]),
    ("Changes", ["If we change this policy, we will update the date below and, where appropriate, tell users in the app."]),
]


def render() -> str:
    body = "".join(f"<h2>{h}</h2>" + "".join(f"<p>{p}</p>" for p in ps) for h, ps in SECTIONS)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>RemedialFlow Privacy Policy</title>
<style>body{{font-family:-apple-system,system-ui,Segoe UI,Roboto,sans-serif;max-width:760px;margin:0 auto;padding:24px 20px 48px;
line-height:1.6;color:#111;background:#fff}}h1{{font-size:28px;margin:0 0 4px}}h2{{font-size:19px;margin:28px 0 8px}}p{{margin:0 0 12px;font-size:16px}}
.muted{{color:#666;font-size:14px}}a{{color:#C2410C}}</style></head><body>
<h1>RemedialFlow Privacy Policy</h1><p class="muted">Last updated {UPDATED}</p>{body}
<p>Contact: <a href="mailto:{CONTACT}">{CONTACT}</a></p></body></html>"""


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy_page():
    return HTMLResponse(render(), headers={"Cache-Control": "public, max-age=3600",
                                           "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"})
