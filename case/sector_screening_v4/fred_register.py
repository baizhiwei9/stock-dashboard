import asyncio
import json
from playwright.async_api import async_playwright
import os
import sys

async def main():
    screenshot_dir = "/app/data/所有对话/主对话/browser/screenshots/fred"
    os.makedirs(screenshot_dir, exist_ok=True)
    
    async with async_playwright() as p:
        # Connect to existing Chrome via CDP
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0] if browser.contexts else await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
        )
        page = context.pages[0] if context.pages else await context.new_page()
        
        # Navigate to FRED account login/registration page
        print("Navigating to FRED account login page...")
        await page.goto("https://fredaccount.stlouisfed.org/login/secure/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        
        title = await page.title()
        print(f"Page title: {title}")
        print(f"Current URL: {page.url}")
        
        # Take screenshot
        await page.screenshot(path=os.path.join(screenshot_dir, "01_login_page.png"), full_page=True)
        print("Screenshot saved: 01_login_page.png")
        
        # Find and list all links and inputs
        links = await page.query_selector_all("a")
        print(f"\nFound {len(links)} links:")
        for link in links[:20]:
            text = await link.inner_text()
            href = await link.get_attribute("href")
            if text.strip():
                print(f"  '{text.strip()}' -> {href}")
        
        inputs = await page.query_selector_all("input")
        print(f"\nFound {len(inputs)} input fields:")
        for inp in inputs:
            name = await inp.get_attribute("name")
            type_attr = await inp.get_attribute("type")
            id_attr = await inp.get_attribute("id")
            placeholder = await inp.get_attribute("placeholder")
            print(f"  name={name}, type={type_attr}, id={id_attr}, placeholder={placeholder}")
        
        await browser.close()

asyncio.run(main())
