from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else context.new_page()
    html = page.content()
    with open("flow_dom.html", "w") as f:
        f.write(html)
    print("Saved to flow_dom.html")
