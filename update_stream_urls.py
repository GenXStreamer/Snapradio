#!/usr/bin/env python3
"""
update_stream_urls.py
─────────────────────
For each station in stations.db that has a Code, fetches http://lsn.to/<code>,
parses the available stream URLs, picks the best one (highest-bitrate AAC/AAC+
preferred, falling back to highest-bitrate MP3), and optionally writes it back
to the database.

Preference order:  AAC+ > AAC > MP3  — then highest bitrate within each tier.

How the page is parsed
──────────────────────
lsn.to station pages group stream links under section headers like:
    "Listen in AAC+ format:"
    "Listen in AAC/AAC+ (HLS) format:"
    "Listen in mp3 format:"

Each header is inside a <font color="#FF0000"> tag; the stream links follow
in the next <big> block. Bitrate comes from the image filename (e.g. 320s.png),
with a fallback to the ?bitrate= URL parameter used by BBC/lsn.lv links.

Usage
─────
    # Dry-run — print what would change, touch nothing
    python3 update_stream_urls.py --test

    # Single station (with or without brackets)
    python3 update_stream_urls.py --test --code 45R
    python3 update_stream_urls.py --test --code [BR1]

    # Live run — actually UPDATE the database
    python3 update_stream_urls.py

    # Live run, limit to first N stations (sanity check)
    python3 update_stream_urls.py --limit 10

Options
───────
    --db PATH       Path to stations.db  (default: stations.db next to this script)
    --code CODE     Only process this station code (with or without brackets)
    --limit N       Stop after processing N stations
    --test          Dry-run: print results, do not write to DB
    --delay SECS    Pause between HTTP requests (default: 1.0 — be polite!)
    --timeout SECS  HTTP request timeout (default: 8)
"""

import argparse
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Constants ──────────────────────────────────────────────────────────────────

BASE_URL        = "http://lsn.to"
DEFAULT_DB      = Path(__file__).with_name("stations.db")
DEFAULT_DELAY   = 1.0
DEFAULT_TIMEOUT = 8

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

# Higher = more preferred
CODEC_RANK = {"aac+": 3, "aac": 2, "mp3": 1}


# ── Codec detection from section header text ───────────────────────────────────

def _codec_from_label(label: str) -> str:
    """
    Map a section header string to a normalised codec name.

    Examples:
        "Listen in AAC+ format:"         → "aac+"
        "Listen in AAC/AAC+ (HLS) format:" → "aac+"   (AAC+ mentioned)
        "Listen in AAC/AAC+ (HLS) format:" → "aac"    (no AAC+ mention — BBC HLS)
        "Listen in mp3 format:"           → "mp3"

    For BBC HLS pages the header says "AAC/AAC+ (HLS)" — we treat this as plain
    AAC because the individual stream links don't distinguish between AAC-LC and
    HE-AAC; the best available is selected by bitrate alone.
    """
    low = label.lower()
    if "aac+" in low or "aacp" in low or "he-aac" in low or "aacplus" in low:
        # "AAC/AAC+ (HLS)" contains both — count as aac+ since at least some
        # of these streams are HE-AAC
        return "aac+"
    if "aac" in low:
        return "aac"
    if "mp3" in low:
        return "mp3"
    return "unknown"


# ── HTML parser ────────────────────────────────────────────────────────────────

def parse_streams(html: str) -> list[dict]:
    """
    Parse a lsn.to station page and return a list of stream dicts:
        [{"url": "...", "codec": "aac+", "bitrate": 48}, ...]

    Strategy: find each red <font color="#FF0000"> section header, read the
    codec from its trailing text, then harvest links from the next <big> block.
    """
    soup = BeautifulSoup(html, "html.parser")
    streams = []

    for font_tag in soup.find_all("font", color="#FF0000"):
        # The label text immediately follows the <font> tag as a text node
        label = ""
        for sibling in font_tag.next_siblings:
            if isinstance(sibling, str):
                label += sibling
            else:
                break
        label = label.strip()

        if not label.lower().startswith("listen in"):
            continue  # not a stream-section header

        codec = _codec_from_label(label)
        if codec == "unknown":
            continue

        # Grab the next <big> block — it contains the stream links
        big_block = font_tag.find_next("big")
        if not big_block:
            continue

        for a in big_block.find_all("a", href=True):
            href = a["href"].strip()

            # Bitrate: image filename first (e.g. 320s.png → 320 kbps)
            bitrate = 0
            img = a.find("img", src=re.compile(r"\d+s\.png", re.I))
            if img:
                m = re.search(r"(\d+)s\.png", img["src"], re.I)
                if m:
                    bitrate = int(m.group(1))

            # Fallback: ?bitrate=NNNN param in URL (BBC lsn.lv links, value in bps)
            if bitrate == 0:
                m2 = re.search(r"[?&]bitrate=(\d+)", href)
                if m2:
                    bitrate = int(m2.group(1)) // 1000

            streams.append({"url": href, "codec": codec, "bitrate": bitrate})

    return streams


# ── Selection ──────────────────────────────────────────────────────────────────

def best_stream(streams: list[dict]) -> dict | None:
    """Pick the best stream: highest codec rank, then highest bitrate."""
    if not streams:
        return None
    return max(streams, key=lambda s: (CODEC_RANK.get(s["codec"], 0), s["bitrate"]))


# ── Database ───────────────────────────────────────────────────────────────────

@contextmanager
def get_db(path: Path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_stations(db_path: Path, code_filter: str | None) -> list:
    with get_db(db_path) as conn:
        if code_filter:
            bare = code_filter.strip("[]").upper()
            return conn.execute(
                "SELECT rowid, Name, Code, StreamURL FROM stations "
                "WHERE UPPER(TRIM(Code, '[]')) = ? ORDER BY Name",
                (bare,),
            ).fetchall()
        return conn.execute(
            "SELECT rowid, Name, Code, StreamURL FROM stations "
            "WHERE Code IS NOT NULL AND Code != '' ORDER BY Name"
        ).fetchall()


def write_url(db_path: Path, rowid: int, url: str) -> None:
    with get_db(db_path) as conn:
        conn.execute("UPDATE stations SET StreamURL = ? WHERE rowid = ?", (url, rowid))


# ── HTTP ───────────────────────────────────────────────────────────────────────

def fetch_page(code: str, timeout: int) -> str | None:
    bare = code.strip("[]")
    url = f"{BASE_URL}/{bare}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code == 200:
            return r.text
        print(f"    ✗ HTTP {r.status_code}  ({url})")
    except requests.RequestException as exc:
        print(f"    ✗ Request failed: {exc}")
    return None


# ── Main ───────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    db_path = Path(args.db)
    if not db_path.exists():
        sys.exit(f"Database not found: {db_path}")

    stations = load_stations(db_path, args.code)
    if not stations:
        sys.exit("No matching stations found.")

    if args.limit:
        stations = stations[: args.limit]

    mode = "TEST MODE — no changes written" if args.test else "LIVE MODE — database will be updated"
    print(f"\n{'─'*62}")
    print(f"  StreamURL Updater  │  {mode}")
    print(f"  Stations to process: {len(stations)}")
    print(f"{'─'*62}\n")

    updated = unchanged = skipped = failed = 0

    for i, row in enumerate(stations, 1):
        name    = row["Name"]
        code    = row["Code"]
        rowid   = row["rowid"]
        old_url = row["StreamURL"] or ""

        print(f"[{i}/{len(stations)}] {name}  ({code})")

        html = fetch_page(code, args.timeout)
        if html is None:
            failed += 1
            print()
            time.sleep(args.delay)
            continue

        streams = parse_streams(html)

        if not streams:
            print(f"    ⚠  No streams found on lsn.to/{code.strip('[]')}")
            skipped += 1
            print()
            time.sleep(args.delay)
            continue

        if args.test:
            print(f"    Candidates ({len(streams)}):")
            pick = best_stream(streams)
            for s in sorted(streams, key=lambda x: (CODEC_RANK.get(x["codec"], 0), x["bitrate"]), reverse=True):
                marker = "★" if s is pick else " "
                print(f"      {marker} [{s['codec'].upper():5s}] {s['bitrate']:>4} kbps  {s['url']}")

        pick = best_stream(streams)
        new_url = pick["url"]

        if new_url == old_url:
            print(f"    ✓  No change  [{pick['codec'].upper()}  {pick['bitrate']} kbps]")
            unchanged += 1
        else:
            print(f"    old: {old_url}")
            print(f"    new: {new_url}  [{pick['codec'].upper()}  {pick['bitrate']} kbps]")
            if not args.test:
                write_url(db_path, rowid, new_url)
                print(f"    ✔  Written to DB")
            else:
                print(f"    ✎  Would update (test mode)")
            updated += 1

        print()
        time.sleep(args.delay)

    print(f"{'─'*62}")
    print(f"  Updated: {updated}   Unchanged: {unchanged}   "
          f"No streams: {skipped}   Errors: {failed}")
    if args.test and updated:
        print(f"  Re-run without --test to apply {updated} change(s).")
    print(f"{'─'*62}\n")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Update StreamURL in stations.db from lsn.to feed pages.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--db",      default=str(DEFAULT_DB), help="Path to stations.db")
    p.add_argument("--code",    default=None,  help="Single station code, e.g. 45R or [BR1]")
    p.add_argument("--limit",   type=int,      help="Process at most N stations")
    p.add_argument("--test",    action="store_true", help="Dry-run, do not write to DB")
    p.add_argument("--delay",   type=float, default=DEFAULT_DELAY,   help="Seconds between requests (default: 1.0)")
    p.add_argument("--timeout", type=int,   default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds (default: 8)")
    run(p.parse_args())


if __name__ == "__main__":
    main()
