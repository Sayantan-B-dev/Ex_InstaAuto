import os
import csv
import time
import datetime
import argparse
import sys
import logging
import cv2
from instagrapi import Client

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich import box

# pip install instagrapi opencv-python rich
# For video posts you also need:
#   pip install "instagrapi[video]"
#   pip install --no-deps "moviepy==2.2.1"
# ...and ffmpeg installed and reachable on PATH.

# ---------------- CONFIG ----------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_CSV = os.path.join(BASE_DIR, "accounts.csv")
POSTS_DIR = os.path.join(BASE_DIR, "posts")
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
MEDIAS_DIR = os.path.join(BASE_DIR, "medias")

CHECK_INTERVAL_SECONDS = 1  # dashboard refresh / accounts.csv re-read interval

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
POST_FIELDNAMES = ["media_path", "caption", "posted"]
REQUIRED_ACCOUNT_FIELDS = ["account_id", "username", "password", "post_time"]

# -----------------------------------------

console = Console()
account_state = {}  # account_id -> {"client", "post_time", "next_run"}
account_loggers = {}  # account_id -> logging.Logger (writes to accounts/<id>/activity.log)

BANNER = r"""
 ___ _   _ ____ _____  _        ____   ____ _   _ _____ ____  _   _ _     _____ ____
|_ _| \ | / ___|_   _|/ \      / ___| / ___| | | | ____|  _ \| | | | |   | ____|  _ \
 | ||  \| \___ \ | | / _ \     \___ \| |   | |_| |  _| | | | | | | | |   |  _| | |_) |
 | || |\  |___) || |/ ___ \     ___) | |___|  _  | |___| |_| | |_| | |___| |___|  _ <
|___|_| \_|____/ |_/_/   \_\   |____/ \____|_| |_|_____|____/ \___/|_____|_____|_| \_\
"""


# ---------------- NARRATIVE LOG HELPERS ----------------

def log_info(msg):
    console.print(f"[cyan]i[/cyan]  {msg}")


def log_step(msg):
    console.print(f"[dim]->[/dim]  {msg}")


def log_success(msg):
    console.print(f"[bold green]✓[/bold green]  {msg}")


def log_warn(msg):
    console.print(f"[bold yellow]!</bold yellow]  {msg}")


def log_error(msg):
    console.print(f"[bold red]✖[/bold red]  {msg}")


def section(title, style="bold magenta"):
    console.print(f"\n{title}")


# ---------------- PER-ACCOUNT FILE LOGGING ----------------

def get_account_logger(account_id):
    """Each account gets its own persistent log file at logs/<account_id>.log,
    independent of the live console dashboard, so history survives across runs."""
    if account_id in account_loggers:
        return account_loggers[account_id]

    logger = logging.getLogger(f"insta.{account_id}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        os.makedirs(LOGS_DIR, exist_ok=True)
        log_path = os.path.join(LOGS_DIR, f"{account_id}.log")
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)

    account_loggers[account_id] = logger
    return logger


# ---------------- ACCOUNTS.CSV ----------------

def read_accounts_raw():
    """Return ALL rows from accounts.csv, unfiltered, with validation warnings.
    Used both by read_accounts() and by the startup diagnostic."""
    if not os.path.isfile(ACCOUNTS_CSV):
        log_error(f"accounts.csv not found at {ACCOUNTS_CSV}")
        return []

    with open(ACCOUNTS_CSV, "rb") as f:
        raw_head = f.read(3)
    if raw_head == b"\xef\xbb\xbf":
        log_warn(
            "accounts.csv has a UTF-8 BOM at the start of the file (common when saved from "
            "Excel/Notepad on Windows). This can corrupt the FIRST column header into "
            "'[BOM]account_id', silently breaking that row's account_id lookup. "
            "Re-save the file as UTF-8 (without BOM) if the first row ever behaves oddly."
        )

    with open(ACCOUNTS_CSV, newline="", encoding="utf-8-sig") as f:
        # encoding="utf-8-sig" transparently strips a BOM if present, so we don't
        # actually break on it -- but we still warn above so you know it's there.
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in REQUIRED_ACCOUNT_FIELDS if c not in header]
        if missing:
            log_error(
                f"accounts.csv is missing required column(s): {missing}. "
                f"Found columns: {header}"
            )
            return []
        rows = list(reader)

    return rows


def read_accounts():
    rows = read_accounts_raw()
    valid = []
    for row in rows:
        account_id = (row.get("account_id") or "").strip()
        if not account_id:
            log_warn(f"Skipping a row in accounts.csv with a blank account_id: {row}")
            continue
        if (row.get("enabled", "yes") or "yes").strip().lower() == "no":
            continue
        valid.append(row)
    return valid


def diagnose_accounts_csv():
    """Startup sanity check: compares accounts.csv against what's actually sitting on disk
    in accounts/, and flags anything that doesn't line up. Catches the most common causes
    of 'my account never gets processed': typos in account_id, enabled=no, duplicate ids,
    or a wrong working directory."""
    section("Startup diagnostic — checking accounts.csv", style="bold blue")
    log_info(f"Reading accounts.csv from: {ACCOUNTS_CSV}")

    all_rows = read_accounts_raw()
    if not all_rows:
        log_error("No rows found in accounts.csv (or file missing/malformed) — nothing to do.")
        return

    seen_ids = {}
    for i, row in enumerate(all_rows):
        account_id = (row.get("account_id") or "").strip()
        enabled = (row.get("enabled", "yes") or "yes").strip().lower()

        if not account_id:
            log_warn(f"Row {i}: blank account_id — this row will be skipped entirely.")
            continue

        if account_id in seen_ids:
            log_warn(
                f"Row {i}: duplicate account_id '{account_id}' (also seen at row "
                f"{seen_ids[account_id]}) — only one will actually be used."
            )
        seen_ids[account_id] = i

        if enabled == "no":
            log_warn(f"Row {i}: account '{account_id}' has enabled=no — it will be SKIPPED.")
        else:
            log_info(f"Row {i}: account '{account_id}' is enabled and will be processed.")

        for field in ("username", "password", "post_time"):
            if not (row.get(field) or "").strip():
                log_warn(f"Row {i} ('{account_id}'): '{field}' column is empty.")

    # Cross-check against folders that already exist under posts/
    if os.path.isdir(POSTS_DIR):
        existing_folders = {
            name for name in os.listdir(POSTS_DIR)
            if os.path.isdir(os.path.join(POSTS_DIR, name))
        }
        orphaned = existing_folders - set(seen_ids.keys())
        for folder_id in sorted(orphaned):
            posts_path = os.path.join(POSTS_DIR, folder_id, "posts.csv")
            has_queue = os.path.isfile(posts_path) and len(read_posts(folder_id)) > 0
            if has_queue:
                log_error(
                    f"Folder accounts/{folder_id}/ has a non-empty posts.csv but "
                    f"'{folder_id}' is NOT in accounts.csv (or is misspelled/disabled) — "
                    f"this account's queue will NEVER be posted until you fix accounts.csv."
                )
            else:
                log_warn(
                    f"Folder accounts/{folder_id}/ exists but has no matching row in "
                    f"accounts.csv — likely a leftover from a renamed/removed account."
                )

    console.print()


# ---------------- PATH HELPERS ----------------

def account_dir(account_id):
    return POSTS_DIR


def account_image_dir(account_id):
    return os.path.join(MEDIAS_DIR, "images", account_id)


def account_video_dir(account_id):
    return os.path.join(MEDIAS_DIR, "videos", account_id)


def ensure_account_dirs(account_id):
    os.makedirs(POSTS_DIR, exist_ok=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    os.makedirs(account_image_dir(account_id), exist_ok=True)
    os.makedirs(account_video_dir(account_id), exist_ok=True)
    posts_path = os.path.join(POSTS_DIR, f"{account_id}_posts.csv")
    if not os.path.isfile(posts_path):
        with open(posts_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=POST_FIELDNAMES)
            writer.writeheader()
        log_step(
            f"[{account_id}] First time seeing this account — created {posts_path} and "
            f"medias/images/{account_id} + medias/videos/{account_id} folders."
        )
        get_account_logger(account_id).info("Initialized account folders and posts file")


# ---------------- LOGIN ----------------

def login_account(row):
    account_id = row["account_id"].strip()
    logger = get_account_logger(account_id)
    session_path = os.path.join(SESSIONS_DIR, f"{account_id}_session.json")

    cl = Client()

    def login_handler(challenge):
        logger.warning(f"Received challenge: {challenge.get('redirect_uri', 'Unknown')}")
        logger.error("Login challenge requires manual intervention - please complete verification")
        raise Exception(
            "Login challenge detected. Please complete the verification manually. "
            "This is often due to 2FA or account security features."
        )

    cl.challenge_code_handler = login_handler
    cl.change_password_handler = login_handler
    proxy = (row.get("proxy") or "").strip()
    if proxy:
        cl.set_proxy(proxy)
        log_step(f"[{account_id}] Proxy configured for this account.")
        logger.info(f"Proxy configured: {proxy}")
    else:
        log_warn(f"[{account_id}] No proxy set for this account — traffic will use your real IP.")
        logger.warning("No proxy set for this account.")

    if os.path.exists(session_path):
        log_step(f"[{account_id}] Found a saved session, verifying it still works...")
        cl.load_settings(session_path)
        try:
            cl.login(row["username"], row["password"])
            cl.get_timeline_feed()
            log_success(f"[{account_id}] Session verified — logged in, no fresh login needed.")
            logger.info("Session verified, logged in with saved session.")
            return cl
        except Exception as e:
            log_warn(f"[{account_id}] Saved session is invalid or expired ({e}).")
            logger.warning(f"Saved session invalid/expired: {e}")
    else:
        log_step(f"[{account_id}] No saved session found for this account.")
        logger.info("No saved session found.")

    log_step(f"[{account_id}] Attempting a fresh login...")
    try:
        cl.login(row["username"], row["password"])
        cl.dump_settings(session_path)
        log_success(f"[{account_id}] Fresh login successful — session saved for next time.")
        logger.info("Fresh login successful, session saved.")
        return cl
    except Exception as e:
        log_error(f"[{account_id}] Login failed: {e}")
        logger.error(f"Login failed: {e}")
        return None


# ---------------- PER-ACCOUNT POST QUEUE ----------------

def posts_csv_path(account_id):
    return os.path.join(POSTS_DIR, f"{account_id}_posts.csv")


def read_posts(account_id):
    path = posts_csv_path(account_id)
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_posts(account_id, rows):
    with open(posts_csv_path(account_id), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=POST_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def get_next_post(account_id):
    for row in read_posts(account_id):
        if row.get("posted", "no").strip().lower() != "yes":
            return row
    return None


def mark_as_posted(account_id, target_row):
    rows = read_posts(account_id)
    for row in rows:
        if row["media_path"] == target_row["media_path"]:
            row["posted"] = "yes"
            break
    write_posts(account_id, rows)


# ---------------- MEDIA / POSTING ----------------

def get_media_type(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def generate_thumbnail(video_path, account_id):
    """Thumbnail is always written into this account's own medias/videos/<account_id>/ folder,
    regardless of where the source video file actually lives."""
    log_step(f"[{account_id}] Extracting a thumbnail frame from the video...")
    logger = get_account_logger(account_id)

    thumb_dir = account_video_dir(account_id)
    os.makedirs(thumb_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    thumb_path = os.path.join(thumb_dir, f"{base_name}.thumbnail.jpg")

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    if fps > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(fps * 0.5))
    success, frame = cap.read()
    if not success:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        success, frame = cap.read()
    cap.release()
    if not success:
        log_warn(f"[{account_id}] Could not extract a thumbnail frame.")
        logger.warning(f"Could not extract thumbnail frame for {video_path}")
        return None
    cv2.imwrite(thumb_path, frame)
    log_step(f"[{account_id}] Thumbnail ready -> {thumb_path}")
    logger.info(f"Thumbnail saved to {thumb_path}")
    return thumb_path


def post_media(account_id, cl, media_path, caption):
    logger = get_account_logger(account_id)
    full_path = media_path if os.path.isabs(media_path) else os.path.join(BASE_DIR, media_path)
    filename = os.path.basename(full_path)
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not os.path.isfile(full_path):
        console.print(Panel(
            f"[bold]Account:[/bold] {account_id}\n"
            f"[bold]File:[/bold] {full_path}\n"
            f"[bold]Time:[/bold] {now_str}\n"
            f"[bold]Result:[/bold] [bold red]Failed — file not found[/bold red]",
            title=f"📤 Posting attempt — {account_id}", border_style="red", box=box.ROUNDED,
        ))
        logger.error(f"Post failed — file not found: {full_path}")
        return False

    media_type = get_media_type(full_path)
    if media_type is None:
        console.print(Panel(
            f"[bold]Account:[/bold] {account_id}\n"
            f"[bold]File:[/bold] {filename}\n"
            f"[bold]Result:[/bold] [bold red]Failed — unsupported file type[/bold red]",
            title=f"📤 Posting attempt — {account_id}", border_style="red", box=box.ROUNDED,
        ))
        logger.error(f"Post failed — unsupported file type: {filename}")
        return False

    log_step(f"[{account_id}] Preparing to upload {media_type}: {filename}")
    logger.info(f"Preparing to upload {media_type}: {filename}")

    try:
        if media_type == "image":
            cl.photo_upload(full_path, caption)
        else:
            thumbnail = generate_thumbnail(full_path, account_id)
            if thumbnail:
                cl.clip_upload(full_path, caption, thumbnail=thumbnail)
            else:
                cl.clip_upload(full_path, caption)

        console.print(Panel(
            f"[bold]Account:[/bold] {account_id}\n"
            f"[bold]Type:[/bold] {media_type.title()}\n"
            f"[bold]File:[/bold] {filename}\n"
            f"[bold]Caption:[/bold] {caption[:100]}\n"
            f"[bold]Time:[/bold] {now_str}\n"
            f"[bold]Result:[/bold] [bold green]Uploaded successfully[/bold green]",
            title=f"📤 Posting update — {account_id}", border_style="green", box=box.ROUNDED,
        ))
        logger.info(f"Uploaded successfully: {filename}")
        return True

    except Exception as e:
        console.print(Panel(
            f"[bold]Account:[/bold] {account_id}\n"
            f"[bold]Type:[/bold] {media_type.title()}\n"
            f"[bold]File:[/bold] {filename}\n"
            f"[bold]Time:[/bold] {now_str}\n"
            f"[bold]Result:[/bold] [bold red]Upload failed — {e}[/bold red]",
            title=f"📤 Posting update — {account_id}", border_style="red", box=box.ROUNDED,
        ))
        logger.error(f"Upload failed for {filename}: {e}")
        return False


# ---------------- SCHEDULING ----------------

def next_run_datetime(post_time_str):
    now = datetime.datetime.now()
    target_time = datetime.datetime.strptime(post_time_str.strip(), "%H:%M").time()
    target_dt = datetime.datetime.combine(now.date(), target_time)
    if target_dt <= now:
        target_dt += datetime.timedelta(days=1)
    return target_dt


def format_hms(seconds):
    seconds = max(int(seconds), 0)
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ---------------- MAIN LOOP ----------------

def sync_accounts():
    """Read accounts.csv, log in any new/changed accounts, drop removed/disabled ones."""
    rows = read_accounts()
    seen_ids = set()

    for row in rows:
        account_id = row["account_id"].strip()
        seen_ids.add(account_id)
        ensure_account_dirs(account_id)

        if account_id not in account_state:
            section(f"New account detected: {account_id}", style="bold blue")
            cl = login_account(row)
            if cl is None:
                log_warn(f"[{account_id}] Will retry login next cycle.")
                continue
            account_state[account_id] = {
                "client": cl,
                "post_time": row["post_time"].strip(),
                "next_run": next_run_datetime(row["post_time"]),
            }
            log_info(f"[{account_id}] Scheduled — next post at "
                     f"{account_state[account_id]['next_run'].strftime('%Y-%m-%d %H:%M')}.")
            get_account_logger(account_id).info(
                f"Scheduled, next post at {account_state[account_id]['next_run']}"
            )
        else:
            new_time = row["post_time"].strip()
            if account_state[account_id]["post_time"] != new_time:
                account_state[account_id]["post_time"] = new_time
                account_state[account_id]["next_run"] = next_run_datetime(new_time)
                log_info(f"[{account_id}] post_time changed in accounts.csv -> now {new_time}.")
                get_account_logger(account_id).info(f"post_time changed -> {new_time}")

    for account_id in list(account_state.keys()):
        if account_id not in seen_ids:
            log_warn(f"[{account_id}] Removed or disabled in accounts.csv — pausing it.")
            get_account_logger(account_id).warning("Removed/disabled in accounts.csv — pausing.")
            del account_state[account_id]


def build_dashboard_table():
    table = Table(title="Multi-Account Posting Schedule", box=box.ROUNDED, expand=True)
    table.add_column("Account", style="bold cyan")
    table.add_column("Next Post In", justify="center")
    table.add_column("Scheduled At", justify="center")
    table.add_column("Status", justify="center")

    if not account_state:
        table.add_row("—", "—", "—", "[yellow]waiting for accounts.csv[/yellow]")
        return table

    now = datetime.datetime.now()
    for account_id, state in sorted(account_state.items()):
        remaining = (state["next_run"] - now).total_seconds()
        status = "[green]ready[/green]" if remaining > 10 else "[bold yellow]posting soon[/bold yellow]"
        table.add_row(
            account_id,
            format_hms(remaining),
            state["next_run"].strftime("%Y-%m-%d %H:%M"),
            status,
        )
    return table


def run_due_posts():
    now = datetime.datetime.now()
    for account_id, state in list(account_state.items()):
        if now >= state["next_run"]:
            # Wrapped so a failure on one account (bad file, network blip, upload
            # exception, etc.) can never take down the scheduler or skip other accounts.
            try:
                section(f"Posting time reached for {account_id}", style="bold green")
                row = get_next_post(account_id)
                if row is None:
                    log_warn(f"[{account_id}] No unposted rows left in {posts_csv_path(account_id)} — nothing to post today.")
                    get_account_logger(account_id).info(f"No unposted rows left in {posts_csv_path(account_id)}.")
                else:
                    ok = post_media(account_id, state["client"], row["media_path"], row["caption"])
                    if ok:
                        mark_as_posted(account_id, row)
            except Exception as e:
                log_error(f"[{account_id}] Unexpected error while posting: {e}")
                get_account_logger(account_id).error(f"Unexpected error while posting: {e}")
            finally:
                state["next_run"] = next_run_datetime(state["post_time"])
                log_info(f"[{account_id}] Next post rescheduled for "
                         f"{state['next_run'].strftime('%Y-%m-%d %H:%M')}.")
                get_account_logger(account_id).info(f"Next post rescheduled for {state['next_run']}")


def run_instant_mode():
    """Ignore all scheduled times — post the next queued item for every account right now,
    sequentially. Each account is wrapped in its own try/except so one account's failure
    (bad file, expired session, upload error, etc.) never stops the run for the rest —
    every account in accounts.csv is always attempted."""
    section("Instant mode — skipping all scheduled times", style="bold yellow")
    log_info("Posting the next queued item for each account, one after another.\n")

    if not account_state:
        log_warn("No accounts are logged in — check accounts.csv (see diagnostic above).")
        return

    for account_id, state in account_state.items():
        logger = get_account_logger(account_id)
        try:
            row = get_next_post(account_id)
            if row is None:
                log_warn(f"[{account_id}] No unposted rows left in {posts_csv_path(account_id)} — skipping.")
                logger.info(f"No unposted rows left in {posts_csv_path(account_id)}.")
                continue
            ok = post_media(account_id, state["client"], row["media_path"], row["caption"])
            if ok:
                mark_as_posted(account_id, row)
        except Exception as e:
            log_error(f"[{account_id}] Unexpected error during instant post — continuing with next account: {e}")
            logger.error(f"Unexpected error during instant post: {e}")
            continue

    log_success("\nInstant run complete for all accounts.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-account Instagram poster.")
    parser.add_argument(
        "--instant",
        action="store_true",
        help="Skip post_time scheduling entirely — post the next queued item for every "
             "account immediately, sequentially, then exit.",
    )
    args = parser.parse_args()

    os.makedirs(POSTS_DIR, exist_ok=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    os.makedirs(MEDIAS_DIR, exist_ok=True)
    log_info("Dynamic multi-account scheduler starting up.")
    log_info("Edit accounts.csv anytime — new rows log in automatically, removed rows pause.\n")

    diagnose_accounts_csv()

    if args.instant:
        sync_accounts()
        run_instant_mode()
        sys.exit(0)

    with Live(console=console, refresh_per_second=4, transient=False) as live:
        while True:
            sync_accounts()
            live.update(build_dashboard_table())
            run_due_posts()
            time.sleep(CHECK_INTERVAL_SECONDS)