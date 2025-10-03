import time
import csv
import json
import argparse

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


YC_COMPANIES_URL = "https://www.ycombinator.com/companies"


def get_driver(headless=True, extra_args=None):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1280,2000")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    )
    if extra_args:
        for a in extra_args:
            opts.add_argument(a)
    return webdriver.Chrome(options=opts)


def apply_filters(driver):
    wait = WebDriverWait(driver, 30)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(1)

    # Open Filters panel if collapsed
    try:
        filter_button = driver.find_element(By.XPATH, "//button[contains(., 'Filters')] | //button[contains(., 'Filter')] | //button[contains(., 'filters')]")
        if filter_button.is_displayed():
            filter_button.click()
            time.sleep(0.5)
    except Exception:
        pass

    # Helper to count current visible company cards
    def _count_cards():
        links = driver.find_elements(By.CSS_SELECTOR, "a[href^='/companies/']")
        return len({a.get_attribute("href") for a in links if a.get_attribute("href")})

    # Helper to wait for result count change or URL chip update
    def _wait_results_change(prev_count, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            try:
                now = _count_cards()
                if now != prev_count:
                    return True
                url = driver.current_url.lower()
                if "United States of America" in url:
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    # Toggle "Is Hiring"
    try:
        hiring = driver.find_element(By.XPATH, "//label[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'is hiring')]/input | //input[@type='checkbox' and @name='isHiring'] | //button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'is hiring')]")
        if hiring.tag_name.lower() == "input":
            if not hiring.is_selected():
                driver.execute_script("arguments[0].click();", hiring)
        else:
            hiring.click()
        time.sleep(0.5)
    except Exception:
        # Fallback: search any element with text 'Is Hiring'
        try:
            el = driver.find_element(By.XPATH, "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'is hiring')]")
            driver.execute_script("arguments[0].click();", el)
            time.sleep(0.5)
        except Exception:
            pass

    # Track count to detect changes
    before = _count_cards()

    try:
      # ds
      # make America / Canada checkbox checked
      # sibling should be a span with text 'America / Canada'
        latam_checkbox = driver.find_element(By.XPATH, "//input[@type='checkbox' ]/following-sibling::span[contains(normalize-space(.), 'America / Canada')]")
        driver.execute_script("arguments[0].click();", latam_checkbox)
        if latam_checkbox.is_selected():
            latam_checkbox.click()
        time.sleep(0.5)
    except Exception:
        print("America / Canada checkbox not found")
        pass


    # Click USA option
    try:
        latam = driver.find_element(By.XPATH, "//label[.//span[contains(normalize-space(.), 'United States of America')] | //input[@type='checkbox' and (contains(@name, 'region') or contains(@id, 'region'))] | //span[normalize-space(text())='United States of America']")
        if latam.tag_name.lower() == "input":
            if not latam.is_selected():
                driver.execute_script("arguments[0].click();", latam)
        else:
            latam.click()
        time.sleep(0.5)
    except Exception:
        # Fallback: click any visible element containing 'USA'
        try:
            print("USA option not found")
            el = driver.find_element(By.XPATH, "//*[contains(normalize-space(.), 'United States of America')]")
            driver.execute_script("arguments[0].click();", el)
            time.sleep(0.5)
        except Exception:
            pass

    # Give results time to refresh
    if not _wait_results_change(before, timeout=20):
        time.sleep(2)


def scroll_to_load_all(driver, timeout=90, pause=1.5, max_idle=5):
    start = time.time()
    last_seen = 0
    idle = 0

    while True:
        cards = driver.find_elements(By.CSS_SELECTOR, "a[href^='/companies/']")
        unique = {c.get_attribute("href") for c in cards if c.get_attribute("href")}
        count = len(unique)

        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.END)
        time.sleep(pause)

        if count <= last_seen:
            idle += 1
        else:
            idle = 0
            last_seen = count

        # Try clicking a 'Load more' button if present
        try:
            more = driver.find_element(By.XPATH, "//button[contains(., 'Loading more...')]" )
            if more.is_enabled():
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", more)
                time.sleep(0.3)
                more.click()
                time.sleep(pause)
        except Exception:
            pass

        if idle >= max_idle:
            break
        if time.time() - start > timeout:
            break


def parse_company_cards(driver):
    items = []
    seen = set()

    # Company cards are anchor links to /companies/<slug>
    links = driver.find_elements(By.CSS_SELECTOR, "a[href^='/companies/']")
    for a in links:
        href = a.get_attribute("href")
        if not href or href in seen or href == "https://www.ycombinator.com/companies/founders":
            continue

        # Heuristic: the anchor often wraps the card; extract name, blurb, tags within
        name = None
        blurb = None
        locations = None

        # Prefer stable heading tags, fall back to hashed class if present
        try:
            name_el = a.find_element(By.XPATH, ".//*[@class='_coName_i9oky_470' or contains(@class,'coName')][string-length(normalize-space(.))>0]")
            name = name_el.text.strip() or None
        except Exception:
          pass

        try:
          # span inside div with class "mb-1.5 text-sm"
            blurb_el = a.find_element(By.XPATH, ".//div[@class='mb-1.5 text-sm']//span[string-length(normalize-space(.))>0]")
            # print(blurb_el.text.strip())
            txt = blurb_el.text.strip()
            if txt and len(txt) <= 250:
                blurb = txt
        except Exception:
            pass

        try:
            # tokens/labels for locations often appear as chips or spans
            loc_el = a.find_element(By.XPATH, ".//*[@class='_coLocation_i9oky_486'][string-length(normalize-space(.))>0]")
            ltxt = loc_el.text.strip()
            # location currently formatted like this "locations": "San Francisco, CA, USA"
            # goal: "locations": {"city": "San Francisco", "state": "CA", "country": "USA"}
            if ltxt and len(ltxt) <= 120:
                locations = ltxt
                locations = locations.split(", ")
                locations = {
                    "city": locations[0],
                    "state": locations[1],
                    "country": locations[2],
                }
        except Exception:
            pass

        items.append({
            "company_name": name,
            "company_url": href,
            "blurb": blurb,
            "locations": locations,
        })
        seen.add(href)

    return items


def save_outputs(rows, out_json="companies.json", out_csv="companies.csv"):
    with open(out_json, "w") as f:
        json.dump(rows, f, indent=2)

    fields = ["company_name", "company_url", "blurb", "locations"]
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})


def main():
    parser = argparse.ArgumentParser(description="Scrape YC companies with 'Is Hiring' and 'USA' filters.")
    parser.add_argument("--headless", action="store_true", help="Run Chrome headless.")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--pause", type=float, default=1.5)
    parser.add_argument("--out-json", default="companies.json")
    parser.add_argument("--out-csv", default="companies.csv")
    args = parser.parse_args()

    driver = get_driver(headless=args.headless)
    try:
        driver.get(YC_COMPANIES_URL)
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(2)

        apply_filters(driver)
        scroll_to_load_all(driver, timeout=args.timeout, pause=args.pause)
        rows = parse_company_cards(driver)
        save_outputs(rows, out_json=args.out_json, out_csv=args.out_csv)
        print(f"Scraped {len(rows)} companies.")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()


