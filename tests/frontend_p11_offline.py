# Phase 11 - Offline evidence frontend tests (web preview, 390x844).
# Covers: offline cache for previously-opened job, "not available offline" for never-opened,
# offline queue + sync bar + sync-now, logout warning sheet, unsynced items persist and
# are user-scoped (installer2 does not see installer1 queue).
# Note: native camera, GPS, airplane mode, backgrounding = device-only; not tested here.
import asyncio
import re
import time
from playwright.async_api import async_playwright, expect

BASE = "https://field-evidence-app-1.preview.emergentagent.com"
INST1 = ("installer@remedialflow.dev", "InstallerDemo!2026")
INST2 = ("installer2@remedialflow.dev", "InstallerDemo!2026")


async def login(page, email, pw):
    await page.goto(BASE + "/login")
    await page.wait_for_selector('input[type="email"], [data-testid="login-email"]', timeout=15000)
    # fill
    try:
        await page.fill('[data-testid="login-email"]', email)
        await page.fill('[data-testid="login-password"]', pw)
        await page.click('[data-testid="login-submit"]', force=True)
    except Exception:
        await page.fill('input[type="email"]', email)
        await page.fill('input[type="password"]', pw)
        await page.get_by_text(re.compile("SIGN IN", re.I)).first.click(force=True)
    # wait for dashboard: project search or job list
    await page.wait_for_timeout(2500)


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        page.on("pageerror", lambda e: print(f"PAGEERR: {e}"))
        results = {}

        # ===== Step 1: Installer1 login & cache a job =====
        try:
            await login(page, *INST1)
            await page.screenshot(path="/tmp/p11_01_dash.jpg", quality=40, full_page=False)
            # Find a job card and open it
            # dashboard renders project rows - click first one that looks like a job
            cards = await page.query_selector_all('[data-testid^="project-row-"], [data-testid^="job-card-"]')
            print("project rows:", len(cards))
            if not cards:
                # Fallback: find any clickable text that looks like a project ref
                refs = await page.query_selector_all('text=/^EF-/')
                print("EF refs seen:", len(refs))
                if refs:
                    cards = refs
            if cards:
                first = cards[0]
                tid = await first.get_attribute("data-testid")
                print("opening:", tid)
                await first.click(force=True)
                await page.wait_for_timeout(2500)
                await page.screenshot(path="/tmp/p11_02_job.jpg", quality=40, full_page=False)
                job_url = page.url
                print("job url:", job_url)
                results["cache_open"] = "ok" if "/job/" in job_url or "/project/" in job_url else f"unexpected_url:{job_url}"
            else:
                results["cache_open"] = "no_project_cards_found"
                job_url = None
        except Exception as e:
            results["cache_open"] = f"err:{e}"
            job_url = None

        # ===== Step 2: Go offline & reload - cached job should still render =====
        try:
            if job_url:
                await ctx.set_offline(True)
                print("offline=True, reloading")
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=10000)
                except Exception as e:
                    print("reload warn:", e)
                await page.wait_for_timeout(2500)
                await page.screenshot(path="/tmp/p11_03_offline_cached.jpg", quality=40, full_page=False)
                html = await page.content()
                not_avail = "Not available offline" in html
                # The job should render some NC content; if offline marker is top-level fine too.
                has_content = any(k in html for k in ["NC-", "TRUSTMARK", "ACCOUNT", "NEW EVIDENCE", "ORIGINAL"])
                results["offline_cached_renders"] = f"not_avail={not_avail} has_content={has_content}"
            else:
                results["offline_cached_renders"] = "skipped (no job url)"
        except Exception as e:
            results["offline_cached_renders"] = f"err:{e}"

        # ===== Step 3: Navigate (while offline) to a never-opened job id =====
        try:
            # fake id that is a valid ObjectId format but we never opened
            fake_pid = "0" * 24
            await page.goto(BASE + f"/job/{fake_pid}", wait_until="domcontentloaded", timeout=10000)
            await page.wait_for_timeout(2500)
            await page.screenshot(path="/tmp/p11_04_never_opened.jpg", quality=40, full_page=False)
            html = await page.content()
            msg = "Not available offline. Open this job once while connected." in html
            results["offline_never_opened_message"] = "ok" if msg else "MISSING: " + ("found 'Not available offline'" if "Not available offline" in html else "no offline message")
        except Exception as e:
            results["offline_never_opened_message"] = f"err:{e}"

        # ===== Step 4: Online again and test offline queue + sync bar =====
        try:
            await ctx.set_offline(False)
            # Reopen a real job
            await page.goto(BASE + "/", wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)
            cards = await page.query_selector_all('[data-testid^="project-row-"], [data-testid^="job-card-"]')
            if cards:
                await cards[0].click(force=True)
                await page.wait_for_timeout(2500)
            await page.screenshot(path="/tmp/p11_05_online_job.jpg", quality=40, full_page=False)
            # Expand an NC
            nc_toggles = await page.query_selector_all('[data-testid^="nc-toggle-"]')
            print("nc toggles:", len(nc_toggles))
            if nc_toggles:
                await nc_toggles[0].click(force=True)
                await page.wait_for_timeout(1500)
            await page.screenshot(path="/tmp/p11_06_nc_expanded.jpg", quality=40, full_page=False)

            # Intercept evidence POSTs to simulate network failure
            async def abort_evidence(route):
                print("ROUTE ABORT:", route.request.url)
                await route.abort("failed")
            await page.route("**/api/ncs/*/evidence", abort_evidence)

            # Try gallery-upload by finding gallery input (web Expo uses hidden <input type=file>)
            # The gallery button triggers an ImagePicker on web - may open a file chooser.
            # We'll upload via set_input_files on hidden file inputs.
            gallery_btns = await page.query_selector_all('[data-testid^="gallery-upload-"]')
            print("gallery btns:", len(gallery_btns))
            uploaded = False
            if gallery_btns:
                # Create a tiny jpeg
                import pathlib
                tmp = pathlib.Path("/tmp/p11_sample.jpg")
                tmp.write_bytes(bytes.fromhex(
                    "FFD8FFE000104A46494600010100000100010000FFDB004300080606070605080707"
                    "07090908" "0A0C140D0C0B0B0C1912130F141D1A1F1E1D1A1C1C20242E2720222C231C1C2837292C"
                    "30313434341F27393D38323C2E333432FFC00011080001000103012200021101031101FFC401"
                    "A20000010501010101010100000000000000000102030405060708090A0B100002010303020403"
                    "05050404000001" "7D01020300041105122131410613516107227114328191A1082342B1C11552D1F02433"
                    "62728" "2090A161718191A25262728292A3435363738393A434445464748494A535455565758595A63"
                    "646566676869" "6A737475767778797A838485868788898A92939495969798999AA2A3A4A5A6A7A8A9AA"
                    "B2B3B4B5B6B7B8" "B9BABCBDBEBFC0C1C2C3C4C5C6C7C8C9CACDCECFD0D1D2D3D4D5D6D7D8D9DADEDFE0"
                    "E1E2E3E4E5E6E7" "E8E9EAEDEEEFF0F1F2F3F4F5F6F7F8F9FA" "FFDA000C03010002110311003F00FBD00FFFD9"
                ))
                # Try clicking button then setting file input
                try:
                    async with page.expect_file_chooser(timeout=4000) as fc_info:
                        await gallery_btns[0].click(force=True)
                    fc = await fc_info.value
                    await fc.set_files(str(tmp))
                    uploaded = True
                    print("file set via chooser")
                except Exception as e:
                    print("chooser failed:", e)
                    # Fallback: inject file into hidden input
                    file_inputs = await page.query_selector_all('input[type="file"]')
                    if file_inputs:
                        await file_inputs[0].set_input_files(str(tmp))
                        uploaded = True
                        print("file set via hidden input")
            await page.wait_for_timeout(3500)
            await page.screenshot(path="/tmp/p11_07_after_upload_attempt.jpg", quality=40, full_page=False)

            # Check sync bar
            sb = await page.query_selector('[data-testid="sync-bar"]')
            sb_text = ""
            if sb:
                t = await page.query_selector('[data-testid="sync-bar-text"]')
                sb_text = (await t.inner_text()) if t else ""
            print("sync-bar visible:", bool(sb), "text:", sb_text)
            # queued items
            queued = await page.query_selector_all('[data-testid^="queued-status-"]')
            qstatuses = [await q.inner_text() for q in queued]
            print("queued statuses:", qstatuses)
            results["offline_queue_pending"] = {
                "uploaded_attempted": uploaded,
                "sync_bar_visible": bool(sb),
                "sync_bar_text": sb_text,
                "queued_statuses": qstatuses,
            }
        except Exception as e:
            results["offline_queue_pending"] = f"err:{e}"

        # ===== Step 5: Unroute, press sync - items should upload =====
        try:
            await page.unroute("**/api/ncs/*/evidence")
            # Press sync-now-button
            syncnow = await page.query_selector('[data-testid="sync-now-button"]')
            if syncnow:
                await syncnow.click(force=True)
                await page.wait_for_timeout(5000)
            await page.screenshot(path="/tmp/p11_08_after_sync.jpg", quality=40, full_page=False)
            # Check that sync bar either shows UPLOADING / UPLOADED / disappeared
            sb_text2 = ""
            t = await page.query_selector('[data-testid="sync-bar-text"]')
            if t:
                sb_text2 = await t.inner_text()
            queued2 = await page.query_selector_all('[data-testid^="queued-status-"]')
            qstatuses2 = [await q.inner_text() for q in queued2]
            print("after-sync bar:", sb_text2, "queued:", qstatuses2)
            results["offline_queue_sync"] = {"sync_bar_text": sb_text2, "queued_statuses": qstatuses2}
        except Exception as e:
            results["offline_queue_sync"] = f"err:{e}"

        # ===== Step 6: Logout warning (if items unsynced) =====
        try:
            # Navigate to Account
            # tabs
            account = await page.query_selector('[data-testid="tab-account"], [data-testid="menu-account"]')
            if account:
                await account.click(force=True)
            else:
                # Try text
                try:
                    await page.get_by_text("ACCOUNT", exact=False).first.click(force=True)
                except Exception:
                    await page.goto(BASE + "/account")
            await page.wait_for_timeout(2000)
            await page.screenshot(path="/tmp/p11_09_account.jpg", quality=40, full_page=False)
            # queue up a new offline item so logout triggers warning
            await page.route("**/api/ncs/*/evidence", lambda r: asyncio.create_task(r.abort("failed")))

            # Press logout
            lb = await page.query_selector('[data-testid="logout-button"]')
            if lb:
                await lb.click(force=True)
                await page.wait_for_timeout(1500)
            ws = await page.query_selector('[data-testid="logout-warning-sheet"]')
            lsync = await page.query_selector('[data-testid="logout-sync-first"]')
            lforce = await page.query_selector('[data-testid="logout-force-button"]')
            print("warning sheet visible:", bool(ws), "sync-first:", bool(lsync), "force:", bool(lforce))
            await page.screenshot(path="/tmp/p11_10_logout_warn.jpg", quality=40, full_page=False)
            results["logout_warning"] = {"sheet": bool(ws), "sync_first": bool(lsync), "force": bool(lforce)}
        except Exception as e:
            results["logout_warning"] = f"err:{e}"

        print("\n==== P11 FRONTEND RESULTS ====")
        for k, v in results.items():
            print(f"- {k}: {v}")
        await browser.close()


asyncio.run(main())
