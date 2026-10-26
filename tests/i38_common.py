B = "https://field-evidence-app-1.preview.emergentagent.com"
PID = "6adebf679184d042a1565596"
async def login_open(page):
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.wait_for_selector('[data-testid="login-email-input"]', timeout=20000)
    await page.fill('[data-testid="login-email-input"]', "qa.kbui.admin@example.com")
    await page.fill('[data-testid="login-password-input"]', "QaCheck-7731x")
    await page.click('[data-testid="login-submit-button"]', force=True)
    await page.wait_for_timeout(4000)
    await page.goto(f"{B}/project/{PID}", wait_until="domcontentloaded")
    await page.wait_for_selector('[data-testid="staff-nc-toggle-NC-01"]', timeout=20000)
    await page.click('[data-testid="staff-nc-toggle-NC-01"]', force=True)
    await page.wait_for_selector('[data-testid="recs-NC-01"]', timeout=15000)
