from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else context.new_page()
    page.screenshot(path="flow_screenshot.png")
    print("Screenshot saved to flow_screenshot.png")
