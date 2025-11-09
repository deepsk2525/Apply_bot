# apply_bot.py
import time, random, os, yaml, json, sys, logging
from pathlib import Path
from jinja2 import Template
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from selenium import webdriver
from webdriver_manager.chrome import ChromeDriverManager


logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

# ---------- Utils ----------
def rnd_wait(cfg):
    t = random.uniform(cfg['run'].get('random_wait_min', 1), cfg['run'].get('random_wait_max', 3))
    logging.debug(f"waiting {t:.2f}s")
    time.sleep(t)

def load_cfg(path='config.yaml'):
    with open(path,'r') as f:
        return yaml.safe_load(f)

def render_cover_letter(template_path, context):
    tpl_text = Path(template_path).read_text()
    tpl = Template(tpl_text)
    return tpl.render(**context)

# ---------- Browser startup ----------
def make_driver(headless=False):
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")

    # ✅ Fix: wrap ChromeDriverManager().install() with Service
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)

    driver.set_window_size(1200, 900)
    return driver

# ---------- LinkedIn functions ----------
def linkedin_login(driver, cfg):
    email = cfg['credentials']['linkedin']['email']
    pwd = cfg['credentials']['linkedin']['password']
    logging.info("LinkedIn: logging in")
    driver.get("https://www.linkedin.com/login")
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "username")))
    driver.find_element(By.ID, "username").send_keys(email)
    driver.find_element(By.ID, "password").send_keys(pwd)
    driver.find_element(By.XPATH, "//button[@type='submit']").click()
    # wait for home
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "global-nav-search")))
    except TimeoutException:
        logging.warning("LinkedIn login may have failed or requires additional verification.")

def linkedin_search_and_apply(driver, cfg):
    keywords = cfg['search']['keywords']
    locations = cfg['search']['locations']
    max_pages = cfg['search'].get('max_pages', 2)
    resume_path = cfg['apply_rules']['resume_path']
    cover_tpl = cfg['apply_rules']['cover_letter_template_path']

    base_search = "https://www.linkedin.com/jobs/search/?f_AL=true&keywords={kw}&location={loc}"
    for kw in keywords:
        for loc in locations:
            search_url = base_search.format(kw=kw.replace(' ','%20'), loc=loc.replace(' ','%20'))
            logging.info(f"LinkedIn: searching {kw} | {loc}")
            driver.get(search_url)
            rnd_wait(cfg)
            for page in range(max_pages):
                logging.info(f"LinkedIn: parsing page {page+1}")
                job_cards = driver.find_elements(By.CSS_SELECTOR, "ul.jobs-search__results-list li")
                for jc in job_cards:
                    try:
                        job_link = jc.find_element(By.TAG_NAME, "a").get_attribute('href')
                    except Exception:
                        continue
                    # open job
                    driver.execute_script("window.open(arguments[0]);", job_link)
                    driver.switch_to.window(driver.window_handles[-1])
                    rnd_wait(cfg)
                    try:
                        title = driver.find_element(By.CSS_SELECTOR, "h1").text
                    except:
                        title = ""
                    try:
                        company = driver.find_element(By.CSS_SELECTOR, ".topcard__org-name-link, .topcard__flavor").text
                    except:
                        company = ""
                    page_text = driver.page_source.lower()
                    matched = all(k.lower() in page_text for k in cfg['filters']['must_have_keywords'])
                    logging.info(f"Found: {title} @ {company} | matched_keywords={matched}")
                    # Decide auto-apply: here simple rule (contains 'easy apply' button)
                    try:
                        easy_apply = driver.find_element(By.CSS_SELECTOR, "button.jobs-apply-button").is_displayed()
                    except Exception:
                        easy_apply = False

                    if matched and easy_apply:
                        logging.info("Attempting auto-apply (LinkedIn Easy Apply)")
                        try:
                            perform_linkedin_easy_apply(driver, resume_path, cover_tpl, cfg)
                        except Exception as e:
                            logging.exception(f"Auto-apply failed: {e}")
                            queue_for_review({
                                "platform":"linkedin",
                                "url": job_link,
                                "title": title,
                                "company": company
                            })
                    else:
                        logging.info("Queued for manual review")
                        queue_for_review({
                            "platform":"linkedin",
                            "url": job_link,
                            "title": title,
                            "company": company
                        })

                    driver.close()
                    driver.switch_to.window(driver.window_handles[0])
                    rnd_wait(cfg)
                # try to click next
                try:
                    nxt = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Page " + str(page+2) + "']")
                    nxt.click()
                    rnd_wait(cfg)
                except Exception:
                    logging.info("No next page found or pagination different. Breaking.")
                    break

def perform_linkedin_easy_apply(driver, resume_path, cover_tpl, cfg):
    wait = WebDriverWait(driver, 10)
    # Click apply
    apply_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.jobs-apply-button")))
    apply_btn.click()
    rnd_wait(cfg)
    # Fill steps: simplistic generic handler
    for step in range(6):
        # upload resume if file input exists
        try:
            file_in = driver.find_element(By.XPATH, "//input[@type='file']")
            file_in.send_keys(str(Path(resume_path).absolute()))
            logging.info("Uploaded resume")
        except Exception:
            pass
        # if there's a text area for message / cover, fill generated cover letter
        try:
            textarea = driver.find_element(By.TAG_NAME, "textarea")
            # generate small cover letter
            cover = render_cover_letter(cover_tpl, {"job_title": driver.find_element(By.CSS_SELECTOR,"h1").text, "company": driver.find_element(By.CSS_SELECTOR,".topcard__org-name-link, .topcard__flavor").text, "name":"Your Name"})
            textarea.clear()
            textarea.send_keys(cover)
        except Exception:
            pass
        # try to click the submit / next / review button
        try:
            # prioritize 'submit application' text or 'next'
            possible = driver.find_elements(By.XPATH, "//button")
            clicked = False
            for b in possible:
                text = (b.text or "").strip().lower()
                if text in ("submit application", "submit", "next", "review"):
                    try:
                        b.click()
                        clicked = True
                        rnd_wait(cfg)
                        break
                    except ElementClickInterceptedException:
                        continue
            if not clicked:
                break
        except Exception:
            break
    # final check for success message
    logging.info("Attempted to complete Easy Apply. Please verify in LinkedIn applications.")

# ---------- Indeed functions ----------
def indeed_search_and_apply(driver, cfg):
    keywords = cfg['search']['keywords']
    locations = cfg['search']['locations']
    resume_path = cfg['apply_rules']['resume_path']
    cover_tpl = cfg['apply_rules']['cover_letter_template_path']
    for kw in keywords:
        for loc in locations:
            query = f"https://www.indeed.com/jobs?q={kw.replace(' ','+')}&l={loc.replace(' ','+')}"
            logging.info(f"Indeed: searching {kw} | {loc}")
            driver.get(query)
            rnd_wait(cfg)
            job_cards = driver.find_elements(By.CSS_SELECTOR, "a.tapItem")
            for card in job_cards:
                try:
                    job_link = card.get_attribute('href')
                except:
                    continue
                driver.execute_script("window.open(arguments[0]);", job_link)
                driver.switch_to.window(driver.window_handles[-1])
                rnd_wait(cfg)
                try:
                    title = driver.find_element(By.CSS_SELECTOR, "h1").text
                except:
                    title = ""
                page_text = driver.page_source.lower()
                matched = all(k.lower() in page_text for k in cfg['filters']['must_have_keywords'])
                logging.info(f"Indeed: {title} | matched={matched}")
                # look for apply button
                try:
                    apply_btn = driver.find_element(By.CSS_SELECTOR, "button.indeed-apply-button")
                    has_in_app_apply = True
                except:
                    has_in_app_apply = False
                if matched and has_in_app_apply:
                    try:
                        perform_indeed_apply(driver, resume_path, cover_tpl, cfg)
                    except Exception as e:
                        logging.exception("Indeed auto-apply failed")
                        queue_for_review({"platform":"indeed","url":job_link,"title":title})
                else:
                    queue_for_review({"platform":"indeed","url":job_link,"title":title})
                driver.close()
                driver.switch_to.window(driver.window_handles[0])
                rnd_wait(cfg)

def perform_indeed_apply(driver, resume_path, cover_tpl, cfg):
    # simplistic: click apply then try to fill
    try:
        btn = driver.find_element(By.CSS_SELECTOR, "button.indeed-apply-button")
        btn.click()
        rnd_wait(cfg)
        # look for upload
        try:
            file_in = driver.find_element(By.XPATH, "//input[@type='file']")
            file_in.send_keys(str(Path(resume_path).absolute()))
        except Exception:
            pass
        # fill cover letter if textarea exists
        try:
            ta = driver.find_element(By.TAG_NAME,"textarea")
            cover = render_cover_letter(cfg['apply_rules']['cover_letter_template_path'], {"job_title": driver.find_element(By.CSS_SELECTOR,"h1").text, "company": "", "name":"Your Name"})
            ta.clear(); ta.send_keys(cover)
        except Exception:
            pass
        # try submit
        try:
            submit = driver.find_element(By.XPATH, "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit') or contains(., 'Apply')]")
            submit.click()
        except Exception:
            logging.info("Could not find final submit — manual verification required.")
    except Exception as e:
        logging.exception("Error in indeed apply process")

# ---------- Queue ----------
QUEUE_PATH = Path("queue.json")
def queue_for_review(item):
    data = []
    if QUEUE_PATH.exists():
        data = json.loads(QUEUE_PATH.read_text())
    data.append(item)
    QUEUE_PATH.write_text(json.dumps(data, indent=2))
    logging.info("Queued for manual review")

# ---------- Main ----------
def main():
    cfg = load_cfg()
    driver = make_driver(cfg['run'].get('headless', False))
    try:
        linkedin_login(driver, cfg)
        linkedin_search_and_apply(driver, cfg)
        indeed_search_and_apply(driver, cfg)
    finally:
        logging.info("Done. Closing browser.")
        driver.quit()

if __name__ == "__main__":
    main()
