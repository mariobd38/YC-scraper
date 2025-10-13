import os
import time
import csv
import json
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from datetime import datetime, timezone


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
        # ensure 'America / Canada' region filter is checked via its label
        ac_input = driver.find_element(By.XPATH, "//label[.//span[normalize-space()='America / Canada']]//input[@type='checkbox']")
        if not ac_input.is_selected():
            ac_label = driver.find_element(By.XPATH, "//label[.//span[normalize-space()='America / Canada']]")
            driver.execute_script("arguments[0].click();", ac_label)
        time.sleep(0.5)
    except Exception:
        print("America / Canada checkbox not found")
        pass


    # Click USA option
    try:
        # find the checkbox input associated with 'United States of America'
        usa = driver.find_element(By.XPATH, "//label[.//span[normalize-space()='United States of America']]//input[@type='checkbox'] | //span[normalize-space()='United States of America']/preceding-sibling::input[@type='checkbox'] | //span[normalize-space()='United States of America']/ancestor::label//input[@type='checkbox']")
        if not usa.is_selected():
            # input may be visually hidden; toggle via its label to ensure click works
            label = driver.find_element(By.XPATH, "//label[.//span[normalize-space()='United States of America']]")
            driver.execute_script("arguments[0].click();", label)
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


def scroll_to_load_all(driver, timeout=120, pause=1.5, max_idle=8):
    start = time.time()
    last_seen = 0
    idle = 0

    while True:
        # do not count href https://www.ycombinator.com/companies/founders
        cards = driver.find_elements(By.CSS_SELECTOR, "a[href^='/companies/']")
        unique = {c.get_attribute("href") for c in cards if c.get_attribute("href") and c.get_attribute("href") != "https://www.ycombinator.com/companies/founders"}
        count = len(unique)
        print(f"Currently scraped: {count} companies")

        # Scroll to bottom
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)
        
        # Additional scroll to ensure lazy loading triggers
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.END)
        time.sleep(pause)

        if count <= last_seen:
            idle += 1
        else:
            idle = 0
            last_seen = count

        # Try clicking a 'Load more' button if present (check multiple variants)
        try:
            more = driver.find_element(By.XPATH, "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'load') and contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'more')]")
            if more.is_displayed() and more.is_enabled():
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", more)
                time.sleep(0.5)
                more.click()
                time.sleep(pause * 2)
                idle = 0  # Reset idle counter after clicking
        except Exception:
            pass

        if idle >= max_idle:
            print(f"No new companies loaded for {max_idle} consecutive checks. Stopping...")
            break
        if time.time() - start > timeout:
            print(f"Timeout of {timeout}s reached. Stopping...")
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


def scrape_jobs_for_company(driver, company_url, company_name):
    """Scrape all jobs for a single company."""
    jobs = []
    jobs_url = company_url + "/jobs"
    
    try:
        driver.get(jobs_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(0.5)
        
        # Find job containers by looking for divs with class APPLY and getting parent
        job_elements = driver.find_elements(By.XPATH, "//div[contains(@class, 'APPLY')]/..")
        
        for job_el in job_elements:
            try:
                # Extract job title and URL from the link
                job_title = None
                job_url = None
                try:
                    title_link = job_el.find_element(By.XPATH, ".//a[contains(@href, '/jobs/')]")
                    job_url = title_link.get_attribute("href")
                    job_title = title_link.text.strip()
                except:
                    continue
                
                if not job_title:
                    continue
                
                # Extract location, salary, and experience from the detail divs
                location = None
                salary = None
                experience = None
                
                try:
                    detail_divs = job_el.find_elements(By.XPATH, ".//div[@class='justify-left flex flex-row flex-wrap gap-x-2 gap-y-0 pr-2']/div")
                    for div in detail_divs:
                        text = div.text.strip()
                        if not text:
                            continue
                        
                        # Salary: contains $ and K
                        if '$' in text and 'K' in text:
                            salary = text
                        # Experience: contains "years" or ends with "+" or has "Any (new grads ok)" 
                        elif 'year' in text.lower() or text.endswith('+') or 'new grad' in text.lower():
                            experience = text
                        # Location: first one that's not salary or experience
                        elif not location and not '$' in text:
                            print('THIS IS THE LOCATION', text)
                            location = text
                except Exception:
                    pass
                
                # Extract date posted from job URL
                date_posted = None
                date_posted_str = None
                if job_url:
                    try:
                        # Open job URL in new tab
                        driver.execute_script("window.open(arguments[0], '_blank');", job_url)
                        WebDriverWait(driver, 10).until(lambda d: len(d.window_handles) > 1)
                        handles = driver.window_handles
                        driver.switch_to.window(handles[-1])
                        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                        
                        # Find JSON-LD script tags
                        scripts = driver.find_elements(By.XPATH, "//script[@type='application/ld+json']")
                        for sc in scripts:
                            try:
                                txt = sc.get_attribute("textContent") or sc.get_attribute("innerHTML") or ""
                                if not txt.strip():
                                    continue
                                data = json.loads(txt)
                                date_str = _find_date_posted_in_json(data)
                                if date_str:
                                    dt = _parse_iso_guess_to_utc(date_str)
                                    if dt:
                                        date_posted = dt
                                        date_posted_str = date_str
                                        break
                            except Exception:
                                continue
                        
                        # Close tab and switch back
                        driver.close()
                        driver.switch_to.window(handles[0])
                    except Exception:
                        # If error, make sure we're back on main window
                        try:
                            handles = driver.window_handles
                            if len(handles) > 1:
                                driver.close()
                            driver.switch_to.window(handles[0])
                        except:
                            pass
                
                # Filter: only include jobs with year >= 2025 AND keyword match
                if date_posted and date_posted < datetime(2025, 8, 4, tzinfo=timezone.utc):
                    continue
                
                # Check for keywords in job title
                keywords = ["engineering", "engineer", "developer"]
                if not any(keyword in job_title.lower() for keyword in keywords):
                    continue

                jobs.append({
                    "company_name": company_name,
                    "company_url": company_url,
                    "job_url": job_url,
                    "job_title": job_title,
                    "location": location,
                    "salary": salary,
                    "experience": experience,
                    "date_posted": date_posted.isoformat() if date_posted else None,
                    "date_posted_raw": date_posted_str
                })
                
            except Exception:
                continue
                
    except Exception:
        # Silent fail if company has no jobs page
        pass
    
    return jobs


def _find_date_posted_in_json(obj):
    """Recursively find a datePosted string in a JSON-LD structure."""
    if isinstance(obj, dict):
        # Direct JobPosting
        t = obj.get("@type")
        if (t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t)) and obj.get("datePosted"):
            return obj.get("datePosted")
        # @graph or nested
        if "@graph" in obj:
            res = _find_date_posted_in_json(obj["@graph"])
            if res:
                return res
        for v in obj.values():
            res = _find_date_posted_in_json(v)
            if res:
                return res
    elif isinstance(obj, list):
        for it in obj:
            res = _find_date_posted_in_json(it)
            if res:
                return res
    return None

def _parse_iso_guess_to_utc(dt_str: str) -> datetime | None:
    if not dt_str:
        return None
    s = dt_str.strip()
    try:
        # Handle trailing Z
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        # Fallback common formats
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(dt_str, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                return dt
            except Exception:
                pass
    return None

def scrape_jobs_worker(company_data, progress_counter, total, lock, headless=True):
    """Worker function that creates its own driver and scrapes jobs for one company."""
    driver = get_driver(headless=headless)
    try:
        company_name = company_data.get("company_name")
        company_url = company_data.get("company_url")
        
        jobs = scrape_jobs_for_company(driver, company_url, company_name)
        
        with lock:
            progress_counter[0] += 1
            print(f"[{progress_counter[0]}/{total}] {company_name}: {len(jobs)} jobs")
        
        return {
            "company_name": company_name,
            "company_url": company_url,
            "job_count": len(jobs),
            "jobs": jobs
        }
    finally:
        driver.quit()


def save_outputs(rows, out_json="companies.json", out_csv="companies.csv"):
    with open(out_json, "w") as f:
        json.dump(rows, f, indent=2)

    fields = ["company_name", "company_url", "blurb", "locations", "job_count"]
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})


def save_jobs_outputs(jobs, out_json="jobs.json", out_csv="jobs.csv"):
    with open(out_json, "w") as f:
        json.dump(jobs, f, indent=2)
    
    fields = ["company_name", "company_url", "job_url", "job_title", "location", "salary", "experience", "date_posted", "date_posted_raw"]
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for job in jobs:
            w.writerow({k: job.get(k) for k in fields})


def main():
    parser = argparse.ArgumentParser(description="Scrape YC companies with 'Is Hiring' and 'USA' filters.")
    parser.add_argument("--headless", action="store_true", help="Run Chrome headless.")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--pause", type=float, default=2.0)
    parser.add_argument("--out-json", default="companies.json")
    parser.add_argument("--out-csv", default="companies.csv")
    parser.add_argument("--jobs-json", default="jobs.json")
    parser.add_argument("--jobs-csv", default="jobs.csv")
    parser.add_argument("--scrape-jobs", action="store_true", help="Also scrape jobs for each company")
    parser.add_argument("--workers", type=int, default=5, help="Number of parallel workers for job scraping")
    args = parser.parse_args()

    # Check if we should load companies from file or scrape them
    if os.path.exists(args.out_json) and args.scrape_jobs:
        # If jobs scraping is requested and companies.json exists, load it
        with open(args.out_json, "r") as f:
            rows = json.load(f)
        print(f"Loaded {len(rows)} companies from {args.out_json}")
    else:
        # Otherwise scrape companies
        driver = get_driver(headless=args.headless)
        try:
            driver.get(YC_COMPANIES_URL)
            WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(2)

            apply_filters(driver)
            scroll_to_load_all(driver, timeout=args.timeout, pause=args.pause)
            rows = parse_company_cards(driver)
            print(f"Scraped {len(rows)} companies.")
        finally:
            driver.quit()
    
    # Scrape jobs if requested
    all_jobs = []
    if args.scrape_jobs:
        print(f"\nStarting to scrape jobs for {len(rows)} companies using {args.workers} workers...")
        
        progress_counter = [0]
        lock = Lock()
        
        # Use ThreadPoolExecutor for parallel scraping
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(scrape_jobs_worker, company, progress_counter, len(rows), lock, args.headless): company
                for company in rows if company.get("company_url")
            }
            
            for future in as_completed(futures):
                try:
                    result = future.result()
                    company_name = result["company_name"]
                    job_count = result["job_count"]
                    jobs = result["jobs"]
                    
                    # Update company with job count
                    for company in rows:
                        if company.get("company_name") == company_name:
                            company["job_count"] = job_count
                            break
                    
                    all_jobs.extend(jobs)
                except Exception as e:
                    print(f"Error processing company: {e}")
        
        print(f"\nTotal jobs scraped: {len(all_jobs)}")
        save_jobs_outputs(all_jobs, out_json=args.jobs_json, out_csv=args.jobs_csv)
    else:
        for company in rows:
            company["job_count"] = 0
    
    save_outputs(rows, out_json=args.out_json, out_csv=args.out_csv)


if __name__ == "__main__":
    main()



# use both USA and Remote filters
# parse location, salary, experience