import os
import asyncio
import time
from dotenv import load_dotenv
from playwright.async_api import async_playwright
import gspread
from google.oauth2.service_account import Credentials

# ------------------------------------------------------------------
# Load environment variables
# ------------------------------------------------------------------
load_dotenv()
EMAIL       = os.getenv("ENDOLE_EMAIL")
PASSWORD    = os.getenv("ENDOLE_PASSWORD")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ------------------------------------------------------------------
# Authenticate Google Sheets
# ------------------------------------------------------------------
creds  = Credentials.from_service_account_file("service_account.json", scopes=SCOPES)
client = gspread.authorize(creds)
sheet  = client.open_by_key(GOOGLE_SHEET_ID).worksheet("Parent")

# ------------------------------------------------------------------
# Load sheet data
# ------------------------------------------------------------------
all_values = sheet.get_all_values()
headers = all_values[0]
rows    = all_values[1:]

# Ensure required columns exist
if "Individual Turnover" not in headers:
    headers.append("Individual Turnover")
    for row in rows:
        row.append("")
sheet.update([headers], "A1")     # push header row back

# ------------------------------------------------------------------
# Column indexes
# ------------------------------------------------------------------
reg_num_idx   = headers.index("Companies House Regestration Number")
reg_name_idx  = headers.index("Company")
turnover_idx  = headers.index("Individual Turnover")

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def create_endole_slug(company_name: str) -> str:
    return (
        company_name.strip()
        .lower()
        .replace("&", "and")
        .replace(",", "")
        .replace(".", "")
        .replace("'", "")
        .replace("’", "")
        .replace(" ", "-")
    )

async def scrape_company_data(page, reg_number: str, company_slug: str) -> str:
    url = f"https://app.endole.co.uk/company/{reg_number}/{company_slug}"
    print(f"🔗 Visiting: {url}")
    await page.goto(url)
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(5000)

    turnover = "N/A"
    try:
        fin_frame = next((f for f in page.frames if "tile=financials" in f.url), None)
        if fin_frame:
            t_elem = fin_frame.locator("//div[contains(text(),'Turnover')]/following-sibling::div")
            if await t_elem.count():
                turnover = (await t_elem.first.text_content() or "").strip()
    except Exception as e:
        print(f"⚠️  Error scraping financials: {e}")

    print(f"✅ Scraped → Turnover: {turnover}")
    return turnover

# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # ----------------  LOGIN  ----------------
        print("🔐 Logging in to Endole...")
        await page.goto("https://app.endole.co.uk/login")
        await page.fill("input[name='email']", EMAIL)
        await page.fill("input[name='password']", PASSWORD)
        await page.click("button[type='submit']")
        await page.wait_for_load_state("networkidle")
        print("✅ Logged in successfully.\n")

        # ----------------  CACHE  ----------------
        turnover_cache: dict[tuple[str, str], str] = {}

        # ----------------  PROCESS ROWS  ----------------
        for idx, row in enumerate(rows):
            try:
                reg_number = row[reg_num_idx].strip()
                reg_name   = row[reg_name_idx].strip()
                turnover_val = row[turnover_idx].strip() if row[turnover_idx] else ""

                # skip invalid or already-filled rows
                if not reg_number or not reg_name or reg_number.lower() == "nan":
                    print(f"⏭️  Skipping invalid row {idx + 2}")
                    continue
                if turnover_val:
                    print(f"⏭️  Skipping row {idx + 2}, already has data")
                    continue

                slug      = create_endole_slug(reg_name)
                cache_key = (reg_number, slug)

                if cache_key in turnover_cache:
                    turnover = turnover_cache[cache_key]
                    print(f"🎯 Cached  → Turnover: {turnover}")
                else:
                    turnover = await scrape_company_data(page, reg_number, slug)
                    turnover_cache[cache_key] = turnover

                sheet.update_cell(idx + 2, turnover_idx + 1, turnover)
                print(f"📝 Updated row {idx + 2} in sheet.")

                # optional: close company tab if one exists
                try:
                    close_btn = page.locator("div._close")
                    if await close_btn.count():
                        await close_btn.first.click()
                        await page.wait_for_timeout(1000)
                except Exception:
                    pass

                time.sleep(1)          # gentle rate-limit for Sheets API

            except Exception as e:
                print(f"❌ Error at row {idx + 2}: {e}")

        await context.close()
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())


