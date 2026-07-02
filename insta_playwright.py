import os, csv, json, time, datetime, argparse, sys, logging, random
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_CSV = os.path.join(BASE_DIR, "accounts.csv")
POSTS_DIR = os.path.join(BASE_DIR, "posts")
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
MEDIAS_DIR = os.path.join(BASE_DIR, "medias")
PROXIES_FILE = os.path.join(BASE_DIR, "proxies.txt")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
POST_FIELDS = ["media_path", "caption", "posted"]
REQ_FIELDS = ["account_id", "username", "password", "post_time"]

account_loggers = {}


def get_logger(aid):
    if aid in account_loggers:
        return account_loggers[aid]
    l = logging.getLogger(f"pw.{aid}")
    l.setLevel(logging.INFO)
    l.propagate = False
    if not l.handlers:
        os.makedirs(LOGS_DIR, exist_ok=True)
        h = logging.FileHandler(os.path.join(LOGS_DIR, f"{aid}_pw.log"), encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        l.addHandler(h)
    account_loggers[aid] = l
    return l


def read_accounts():
    if not os.path.isfile(ACCOUNTS_CSV):
        print("[ERROR] accounts.csv not found")
        return []
    with open(ACCOUNTS_CSV, newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        missing = [c for c in REQ_FIELDS if c not in (r.fieldnames or [])]
        if missing:
            print(f"[ERROR] Missing columns: {missing}")
            return []
        rows = list(r)
    return [
        row for row in rows
        if (row.get("account_id") or "").strip()
        and (row.get("enabled", "yes") or "yes").strip().lower() != "no"
    ]


def load_proxies():
    if not os.path.isfile(PROXIES_FILE):
        return []
    with open(PROXIES_FILE, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip() and not l.startswith("#")]


def posts_path(aid):
    return os.path.join(POSTS_DIR, f"{aid}_posts.csv")


def read_posts(aid):
    p = posts_path(aid)
    if not os.path.isfile(p):
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_posts(aid, rows):
    with open(posts_path(aid), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=POST_FIELDS)
        w.writeheader()
        w.writerows(rows)


def next_post(aid):
    for row in read_posts(aid):
        if row.get("posted", "no").strip().lower() != "yes":
            return row
    return None


def mark_posted(aid, target):
    rows = read_posts(aid)
    for row in rows:
        if row["media_path"] == target["media_path"]:
            row["posted"] = "yes"
            break
    write_posts(aid, rows)


def ensure_dirs(aid):
    os.makedirs(POSTS_DIR, exist_ok=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    os.makedirs(os.path.join(MEDIAS_DIR, "images", aid), exist_ok=True)
    os.makedirs(os.path.join(MEDIAS_DIR, "videos", aid), exist_ok=True)


def resolve_path(aid, mp):
    s = mp.lstrip("/\\")
    if len(mp) > 1 and mp[1] == ":":
        candidates = [mp]
    else:
        candidates = [os.path.join(BASE_DIR, s)]
    b = os.path.basename(s)
    vd = os.path.join(MEDIAS_DIR, "videos", aid)
    candidates += [os.path.join(vd, b), os.path.join(MEDIAS_DIR, b), os.path.join(MEDIAS_DIR, "videos", b)]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[0]


def cookies_path(aid):
    return os.path.join(SESSIONS_DIR, f"{aid}_cookies.json")


def save_cookies(ctx, aid):
    cookies = ctx.cookies()
    with open(cookies_path(aid), "w") as f:
        json.dump(cookies, f)
    print(f"  [{aid}] Cookies saved.")


def load_cookies(ctx, aid):
    p = cookies_path(aid)
    if not os.path.isfile(p):
        return False
    with open(p) as f:
        ctx.add_cookies(json.load(f))
    return True


def delete_cookies(aid):
    p = cookies_path(aid)
    if os.path.exists(p):
        os.remove(p)
        print(f"  [{aid}] Cookies deleted.")


def logged_in(page):
    try:
        page.wait_for_selector('a[href="/"], svg[aria-label="Home"], [alt*="profile"]', timeout=10)
        return True
    except:
        return False


def dismiss_popups(page):
    for sel in [
        'button:has-text("Not Now")',
        'button:has-text("Save Info")',
        'button:has-text("Turn On")',
        'button:has-text("Turn Off")',
        'div[role="dialog"] button:has-text("Cancel")',
        '[aria-label="Close"]',
    ]:
        try:
            b = page.locator(sel)
            if b.is_visible(timeout=2):
                b.click()
                time.sleep(0.5)
        except:
            pass


def login(page, row, proxy):
    aid = row["account_id"].strip()
    logger = get_logger(aid)

    if load_cookies(page.context, aid):
        page.goto("https://www.instagram.com/", timeout=60000)
        page.wait_for_load_state("networkidle")
        if logged_in(page):
            print(f"  [{aid}] Cookies valid.")
            logger.info("Cookies valid.")
            dismiss_popups(page)
            return True
        print(f"  [{aid}] Cookies expired.")
        logger.info("Cookies expired.")

    print(f"  [{aid}] Logging in...")
    page.goto("https://www.instagram.com/accounts/login/", timeout=60000)
    page.wait_for_load_state("networkidle")
    time.sleep(3)

    dismiss_popups(page)
    time.sleep(1)

    for sel in ['input[name="username"]', 'input[type="text"]', 'input:not([type="hidden"])']:
        try:
            inp = page.locator(sel).first
            if inp.is_visible(timeout=5):
                inp.fill(row["username"])
                time.sleep(random.uniform(0.3, 1))
                break
        except:
            continue
    else:
        page.screenshot(path=os.path.join(LOGS_DIR, f"{aid}_debug.png"))
        print(f"  [{aid}] Username field not found. Screenshot saved.")
        logger.error("Username field not found.")
        return False

    for sel in ['input[name="password"]', 'input[type="password"]']:
        try:
            inp = page.locator(sel).first
            if inp.is_visible(timeout=5):
                inp.fill(row["password"])
                time.sleep(random.uniform(0.3, 1))
                break
        except:
            continue

    for sel in ['button[type="submit"]', 'button:has-text("Log In")', 'button:has-text("Log in")']:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=3):
                btn.click()
                break
        except:
            continue

    for attempt in range(2):
        try:
            page.wait_for_selector('a[href="/"], svg[aria-label="Home"], [alt*="profile"]', timeout=90000)
            print(f"  [{aid}] Login OK.")
            logger.info("Login OK.")
            dismiss_popups(page)
            save_cookies(page.context, aid)
            return True
        except:
            if "challenge" in page.url.lower():
                print(f"  [{aid}] CHALLENGE — verify in the browser window.")
                logger.warning("Challenge — waiting for manual resolution.")
                try:
                    page.wait_for_selector('a[href="/"], svg[aria-label="Home"], [alt*="profile"]', timeout=300000)
                    print(f"  [{aid}] Challenge resolved.")
                    logger.info("Challenge resolved.")
                    dismiss_popups(page)
                    save_cookies(page.context, aid)
                    return True
                except:
                    print(f"  [{aid}] Challenge timeout.")
                    logger.error("Challenge timeout.")
                    return False
            elif attempt == 0:
                print(f"  [{aid}] Retrying login...")
                for sel in ['input[name="password"]', 'input[type="password"]']:
                    try:
                        page.locator(sel).first.fill(row["password"])
                        break
                    except:
                        continue
                for sel in ['button[type="submit"]', 'button:has-text("Log In")']:
                    try:
                        page.locator(sel).first.click()
                        break
                    except:
                        continue
                continue
            else:
                page.screenshot(path=os.path.join(LOGS_DIR, f"{aid}_login_fail.png"))
                print(f"  [{aid}] Login failed. URL: {page.url}")
                logger.error(f"Login failed: {page.url}")
                return False
    return False


def upload(page, aid, media_path, caption):
    logger = get_logger(aid)
    fp = resolve_path(aid, media_path)
    fn = os.path.basename(fp)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not os.path.isfile(fp):
        print(f"  [{ts}] [{aid}] File not found: {fp}")
        logger.error(f"File not found: {fp}")
        return False

    ext = os.path.splitext(fn)[1].lower()
    is_video = ext in VIDEO_EXTS

    print(f"  [{aid}] Opening upload...")

    try:
        page.goto("https://www.instagram.com/create/", timeout=30000)
    except:
        pass

    try:
        with page.expect_file_chooser() as fc_info:
            page.locator('input[type="file"]').first.click(timeout=15000)
        fc = fc_info.value
        fc.set_files(fp)
        print(f"  [{aid}] File selected: {fn}")
    except Exception as e:
        print(f"  [{aid}] File select failed: {e}")
        logger.error(f"File select failed: {e}")
        return False

    try:
        page.wait_for_selector('button:has-text("Next")', timeout=60000)
        time.sleep(random.uniform(1, 2))
        page.click('button:has-text("Next")')
    except:
        print(f"  [{aid}] Next button (1) not found.")
        logger.error("Next button (1) not found.")
        return False

    if is_video:
        try:
            page.wait_for_selector('button:has-text("Next")', timeout=30000)
            time.sleep(random.uniform(1, 2))
            page.click('button:has-text("Next")')
        except:
            pass

    try:
        cap = page.locator('[aria-label="Write a caption..."]')
        cap.wait_for(timeout=30000)
        cap.fill(caption)
        print(f"  [{aid}] Caption entered.")
    except:
        try:
            page.locator('div[role="textbox"]').first.fill(caption)
        except:
            print(f"  [{aid}] Caption area not found.")
            logger.warning("Caption area not found.")

    time.sleep(random.uniform(1, 3))

    for attempt in range(3):
        try:
            page.click('button:has-text("Share")', timeout=15000)
            time.sleep(2)
            print(f"  [{ts}] [{aid}] POSTED \u2014 {fn}")
            logger.info(f"Posted: {fn}")
            return True
        except:
            time.sleep(2)

    print(f"  [{ts}] [{aid}] Share failed.")
    logger.error(f"Share failed: {fn}")
    return False


def run_instant():
    print("\n--- Playwright Instant Mode ---\n")

    rows = read_accounts()
    proxies = load_proxies()
    if not rows:
        print("No enabled accounts.")
        return

    with sync_playwright() as p:
        for i, row in enumerate(rows):
            aid = row["account_id"].strip()
            ensure_dirs(aid)
            logger = get_logger(aid)

            proxy = proxies[i % len(proxies)] if proxies else (row.get("proxy") or "").strip() or None
            pw_proxy = {"server": proxy} if proxy else None

            print(f"\n--- {aid} ---")
            if proxy:
                print(f"  [{aid}] Proxy: {proxy}")

            browser = p.chromium.launch(
                headless=False,
                proxy=pw_proxy,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
            )
            ctx = browser.new_context(
                viewport={"width": 1366, "height": 768},
                locale="en-US",
                timezone_id="America/New_York",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = ctx.new_page()

            try:
                if not login(page, row, proxy):
                    print(f"  [{aid}] Login failed, skipping.")
                    logger.error("Login failed.")
                    ctx.close()
                    browser.close()
                    delete_cookies(aid)
                    continue

                post_row = next_post(aid)
                if not post_row:
                    print(f"  [{aid}] Nothing to post in {posts_path(aid)}.")
                    logger.info("Nothing to post.")
                elif upload(page, aid, post_row["media_path"], post_row["caption"]):
                    mark_posted(aid, post_row)
            except Exception as e:
                print(f"  [{aid}] Error: {e}")
                logger.error(f"Error: {e}")

            ctx.close()
            browser.close()
            delete_cookies(aid)
            print(f"  [{aid}] Done.\n")

    print("All accounts processed.\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Multi-account Instagram poster (Playwright).")
    p.add_argument("--instant", action="store_true", default=True, help="Post all queued items immediately (default).")
    args = p.parse_args()
    run_instant()
