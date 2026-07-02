# Ex_InstaAuto

Multi-account Instagram poster with per-account isolation — login, post, cleanup, then move to next.

## Project Structure

```
InstaAuto/
├── logs/
│   ├── account1.log
│   ├── account1_activity.log
│   ├── account2.log
│   └── account2_activity.log
├── medias/
│   ├── images/
│   │   ├── account1/
│   │   └── account2/
│   └── videos/
│       ├── account1/
│       │   ├── 1.mp4
│       │   ├── 1.mp4.thumbnail.jpg
│       │   └── 1.thumbnail.jpg
│       └── account2/
│           └── 1.mp4
├── posts/
│   ├── account1_posts.csv
│   └── account2_posts.csv
├── sessions/
├── .gitignore
├── accounts.csv
├── insta.py
└── tree.py
```

## Setup

```bash
pip install instagrapi opencv-python rich
pip install "instagrapi[video]"
pip install --no-deps "moviepy==2.2.1"
```

FFmpeg must be installed and available on PATH for video posting.

## Configuration

### `accounts.csv`

| Column | Description |
|--------|-------------|
| `account_id` | Unique identifier (used for folders, filenames) |
| `username` | Instagram login email/username |
| `password` | Instagram password |
| `proxy` | Optional proxy (`http://user:pass@host:port`) — leave blank for direct |
| `post_time` | Scheduled daily time in `HH:MM` 24-hour format |
| `enabled` | `yes` or `no` to enable/disable |

### `posts/account*_posts.csv`

Queue of media to post per account:

| Column | Description |
|--------|-------------|
| `media_path` | Path to media file (relative to project root) |
| `caption` | Instagram caption |
| `posted` | `yes` after successful upload |

## Usage

**Instant mode** — post all queued items now, one account at a time:

```bash
python insta.py --instant
```

**Daemon mode** — runs continuously, posts at each account's scheduled `post_time`:

```bash
python insta.py
```

Each account is processed independently: login → post → session deleted → next account.

## Challenge Handler

If Instagram requires verification (2FA, login challenge), the script prompts for the 6-digit code sent via SMS/email. Check the Instagram app or email, then enter the code.
