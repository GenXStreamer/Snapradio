import os
import sys
import gzip
import sqlite3
import re
import time
import json
import datetime
import threading
import signal
import subprocess
import requests
import urllib.parse
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Response, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


# Load configuration values from .env file into os.environ
load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────────────
_SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))

SNAPCAST_FIFO   = os.environ.get("SNAPCAST_FIFO",   "/tmp/snapcastDAB")
RECORDING_DIR   = os.environ.get("RECORDING_DIR",   os.path.join(_SCRIPT_DIR, "recordings"))
LOCAL_LOGOS_DIR = os.environ.get("LOCAL_LOGOS_DIR",  os.path.join(_SCRIPT_DIR, "local_logos"))
HISTORY_DB      = os.environ.get("HISTORY_DB",       os.path.join(_SCRIPT_DIR, "history.db"))
STATIONS_JSON   = os.environ.get("STATIONS_JSON",    os.path.join(_SCRIPT_DIR, "radiobrowser_stations_latest.json.gz"))
STATIONS_JSON_URL = os.environ.get("STATIONS_JSON_URL", "http://backups.radio-browser.info/radiobrowser_stations_latest.json.gz")
FFMPEG_BIN      = os.environ.get("FFMPEG_BIN",       "ffmpeg")
PWCAT_BIN       = os.environ.get("PWCAT_BIN",        "pw-cat")
PIPEWIRE_TARGET = os.environ.get("PIPEWIRE_TARGET",  "snapcastDAB")
RADIO_CHANNELS  = int(os.environ.get("RADIO_CHANNELS",   "2"))
RADIO_SAMPLE_RATE = int(os.environ.get("RADIO_SAMPLE_RATE", "48000"))
FAVOURITES_FILE = os.environ.get("FAVOURITES_FILE",  os.path.join(_SCRIPT_DIR, "favourites.json"))
DEFAULT_ART     = "/logos/default_fallback.jpg"
APP_HOST        = os.environ.get("APP_HOST", "0.0.0.0")
APP_PORT        = int(os.environ.get("APP_PORT", "8882"))
DEFAULT_COUNTRY = os.environ.get("DEFAULT_COUNTRY", "GB").upper()

DOWNLOADS_DIR   = os.environ.get("DOWNLOADS_DIR",   os.path.join(_SCRIPT_DIR, "downloads"))
TEMPLATES_DIR   = os.path.join(_SCRIPT_DIR, "templates")

TWITCH_CLIENT_ID     = os.environ.get("TWITCH_CLIENT_ID",     "")
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "")
TWITCH_USER_ID       = os.environ.get("TWITCH_USER_ID",       "")   # your numeric Twitch user ID
TWITCH_USER_TOKEN    = os.environ.get("TWITCH_USER_TOKEN",    "")
TWITCH_REFRESH_TOKEN = os.environ.get("TWITCH_REFRESH_TOKEN", "")
TWITCH_FIFO          = os.environ.get("TWITCH_FIFO",          "/tmp/Twitch")
TWITCH_PW_TARGET     = os.environ.get("TWITCH_PW_TARGET",     "snapcastDAB")

os.makedirs(RECORDING_DIR,   exist_ok=True)
os.makedirs(LOCAL_LOGOS_DIR, exist_ok=True)
os.makedirs(DOWNLOADS_DIR,   exist_ok=True)


# ── Downloader queue ──────────────────────────────────────────────────────────
import queue as _queue
import tempfile
import shutil

_dl_jobs: dict = {}          # job_id -> {status, url, title, artist, error, path}
_dl_queue = _queue.Queue()
_dl_lock  = threading.Lock()

def _dl_worker():
    """Background thread — processes download jobs one at a time."""
    while True:
        job_id, url = _dl_queue.get()
        _dl_set_status(job_id, status="downloading")
        tmpdir = tempfile.mkdtemp()
        try:
            import yt_dlp
            from mutagen.mp3 import MP3
            from mutagen.id3 import ID3, TIT2, TPE1, APIC
            from urllib.parse import urlparse as _urlparse

            def progress_hook(d):
                if d["status"] == "finished":
                    _dl_set_status(job_id, status="encoding")

            ydl_opts = {
                "format":    "bestaudio/best",
                "outtmpl":   os.path.join(tmpdir, "%(title)s.%(ext)s"),
                "postprocessors": [{
                    "key":              "FFmpegExtractAudio",
                    "preferredcodec":   "mp3",
                    "preferredquality": "0",
                }],
                "embedmetadata": True,
                "quiet":         True,
                "progress_hooks": [progress_hook],
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

            title  = info.get("title",    "Unknown Title")
            artist = info.get("uploader", "Unknown Artist")

            audio_file = next(
                os.path.join(tmpdir, f) for f in os.listdir(tmpdir) if f.endswith(".mp3")
            )

            # Fetch artwork from Mixcloud API if applicable
            artwork_path = None
            if "mixcloud.com" in url:
                try:
                    mc_key  = _urlparse(url).path.strip("/")
                    api_url = f"https://api.mixcloud.com/{mc_key}/"
                    r = requests.get(api_url, timeout=10)
                    r.raise_for_status()
                    pics    = r.json().get("pictures", {})
                    art_url = pics.get("extra_large") or pics.get("large") or pics.get("medium")
                    if art_url:
                        artwork_path = os.path.join(tmpdir, "cover.jpg")
                        with open(artwork_path, "wb") as f:
                            f.write(requests.get(art_url, timeout=10).content)
                except Exception:
                    pass

            # Embed ID3 tags
            audio = MP3(audio_file, ID3=ID3)
            try:
                audio.add_tags()
            except Exception:
                pass
            audio.tags["TIT2"] = TIT2(encoding=3, text=title)
            audio.tags["TPE1"] = TPE1(encoding=3, text=artist)
            if artwork_path and os.path.exists(artwork_path):
                with open(artwork_path, "rb") as img:
                    audio.tags["APIC"] = APIC(
                        encoding=3, mime="image/jpeg",
                        type=3, desc="Cover", data=img.read()
                    )
            audio.save()

            safe_title  = "".join(ch for ch in title  if ch.isalnum() or ch in " ._-").strip()
            safe_artist = "".join(ch for ch in artist if ch.isalnum() or ch in " ._-").strip()
            final_name  = f"{safe_artist} - {safe_title}.mp3"
            final_path  = os.path.join(DOWNLOADS_DIR, final_name)
            shutil.move(audio_file, final_path)

            _dl_set_status(job_id, status="done", title=title, artist=artist,
                    path=final_path, filename=final_name)
            print(f"[Downloader] Done: {final_name}")

        except Exception as e:
            print(f"[Downloader] Error: {e}")
            _dl_set_status(job_id, status="error", error=str(e))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
            _dl_queue.task_done()

def _dl_set_status(job_id, **kwargs):
    with _dl_lock:
        _dl_jobs.setdefault(job_id, {}).update(kwargs)

# Start the single background worker thread
threading.Thread(target=_dl_worker, daemon=True).start()

# ── Twitch state ─────────────────────────────────────────────────────────────
_twitch_access_token  = None
_twitch_token_expiry  = 0.0
_twitch_stream_fd     = None
_twitch_ffmpeg_proc   = None
_twitch_pwcat_proc    = None
_twitch_stop_event    = threading.Event()
_twitch_lock          = threading.Lock()
_twitch_now_playing   = {"login": None, "display_name": None, "title": None, "game": None}

# ── Station data ──────────────────────────────────────────────────────────────
# In-memory store: { "GB": [station, ...], "US": [...], ... }
_stations_by_country: dict[str, list] = {}
_stations_lock = threading.Lock()

def _download_stations_json():
    """Download the latest station dump from RadioBrowser backups."""
    print(f"Downloading station data from {STATIONS_JSON_URL} ...")
    r = requests.get(STATIONS_JSON_URL, stream=True, timeout=60)
    r.raise_for_status()
    with open(STATIONS_JSON, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            f.write(chunk)
    print(f"Downloaded to {STATIONS_JSON}")

_load_status = {"state": "not_started", "detail": "", "stations": 0, "countries": 0}

def load_stations_from_file():
    """
    Parse the gzipped JSON dump into _stations_by_country.
    Filters out broken stations (lastcheckok != 1).
    Called at startup and by the nightly refresh endpoint.
    """
    global _stations_by_country, _load_status

    _load_status = {"state": "loading", "detail": f"Checking for {STATIONS_JSON}", "stations": 0, "countries": 0}

    if not os.path.exists(STATIONS_JSON):
        print(f"Station JSON not found at {STATIONS_JSON} — downloading now...")
        _load_status["detail"] = "Downloading station data..."
        try:
            _download_stations_json()
        except Exception as e:
            msg = f"Download failed: {e}"
            print(msg)
            _load_status = {"state": "error", "detail": msg, "stations": 0, "countries": 0}
            return

    print(f"Loading station data from {STATIONS_JSON} ...")
    _load_status["detail"] = f"Parsing {STATIONS_JSON}"
    t0 = time.time()

    try:
        opener = gzip.open if STATIONS_JSON.endswith(".gz") else open
        with opener(STATIONS_JSON, "rt", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        msg = f"Failed to parse {STATIONS_JSON}: {e}"
        print(msg)
        _load_status = {"state": "error", "detail": msg, "stations": 0, "countries": 0}
        return

    print(f"Parsed {len(raw):,} raw records, filtering and indexing...")

    # Sample first record so we can verify field names
    if raw:
        sample = raw[0]
        print(f"Sample record keys: {list(sample.keys())[:10]}")
        print(f"Sample lastcheckok={sample.get('lastcheckok')!r}  countrycode={sample.get('countrycode')!r}  iso_3166_1={sample.get('iso_3166_1')!r}")

    by_country: dict[str, list] = {}
    skipped = 0
    for s in raw:
        # lastcheckok can be int 1/0 or string "1"/"0" depending on dump version
        lco = s.get("lastcheckok")
        if lco is not None and str(lco) != "1":
            skipped += 1
            continue

        # The backup dump uses different field names to the live API —
        # normalise them here so the rest of the app is consistent
        if "iso_3166_1" in s and "countrycode" not in s:
            s["countrycode"]   = s["iso_3166_1"]
        if "url_stream" in s and "url" not in s:
            s["url"]           = s["url_stream"]
            s["url_resolved"]  = s["url_stream"]
        if "url_homepage" in s and "homepage" not in s:
            s["homepage"]      = s["url_homepage"]
        if "url_favicon" in s and "favicon" not in s:
            s["favicon"]       = s["url_favicon"]

        cc = (s.get("countrycode") or "").strip().upper()
        if not cc:
            continue
        by_country.setdefault(cc, []).append(s)

    with _stations_lock:
        _stations_by_country = by_country

    total = sum(len(v) for v in by_country.values())
    elapsed = time.time() - t0
    msg = f"Loaded {total:,} stations across {len(by_country)} countries ({skipped:,} skipped as broken) in {elapsed:.1f}s"
    print(msg)
    _load_status = {"state": "ok", "detail": msg, "stations": total, "countries": len(by_country)}

# ── History DB ────────────────────────────────────────────────────────────────
def init_history_db():
    con = sqlite3.connect(HISTORY_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS track_history (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            station_uuid TEXT,
            station_name TEXT NOT NULL,
            track_title  TEXT NOT NULL,
            played_at    TEXT NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_played_at ON track_history(played_at DESC)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_station   ON track_history(station_name)")
    con.commit()
    con.close()

def record_track(station_uuid, station_name, track_title):
    if not track_title or not station_name:
        return
    try:
        con = sqlite3.connect(HISTORY_DB)
        row = con.execute(
            "SELECT track_title FROM track_history WHERE station_uuid=? ORDER BY id DESC LIMIT 1",
            (station_uuid,)
        ).fetchone()
        if row and row[0] == track_title:
            con.close()
            return
        played_at = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        con.execute(
            "INSERT INTO track_history (station_uuid, station_name, track_title, played_at) VALUES (?,?,?,?)",
            (station_uuid, station_name, track_title, played_at)
        )
        con.commit()
        con.close()
    except Exception as e:
        print(f"History DB error: {e}")

# ── Radio engine ──────────────────────────────────────────────────────────────
class RadioEngine:
    def __init__(self):
        self.ffmpeg_proc = None
        self.pwcat_proc  = None
        self.record_proc = None
        self.current_url          = None
        self.current_station_data = None
        self.current_title        = ""
        self.current_artwork      = ""
        self.is_recording         = False
        self.stop_event           = threading.Event()
        self.lock                 = threading.Lock()

        self.favourites = []
        if os.path.exists(FAVOURITES_FILE):
            try:
                with open(FAVOURITES_FILE, "r") as f:
                    self.favourites = json.load(f)
            except Exception:
                self.favourites = []

        threading.Thread(target=self._metadata_poller_loop, daemon=True).start()

    def save_favourites(self):
        try:
            with open(FAVOURITES_FILE, "w") as f:
                json.dump(self.favourites, f, indent=4)
        except Exception as e:
            print(f"Error saving favourites: {e}")

    def toggle_favourite(self, station_data):
        uuid     = station_data.get("stationuuid")
        existing = next((s for s in self.favourites if s.get("stationuuid") == uuid), None)
        if existing:
            self.favourites.remove(existing)
        else:
            self.favourites.append(station_data)
        self.save_favourites()

    def is_favourite(self, uuid):
        return any(s for s in self.favourites if s.get("stationuuid") == uuid)

    def play(self, station_data):
        self.stop()
        url = station_data.get("url_resolved") or station_data.get("url")
        with self.lock:
            self.current_url          = url
            self.current_station_data = station_data
            self.current_title        = "Connecting..."
            self.current_artwork      = ""
            self.stop_event.clear()

            if not os.path.exists(SNAPCAST_FIFO):
                os.mkfifo(SNAPCAST_FIFO)

            self.ffmpeg_proc = subprocess.Popen(
                [FFMPEG_BIN, "-loglevel", "warning",
                 "-i", url, "-vn",
                 "-ac", str(RADIO_CHANNELS),
                 "-ar", str(RADIO_SAMPLE_RATE),
                 "-f", "s16le", "-"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                preexec_fn=None if sys.platform == "win32" else os.setsid
            )
            self.pwcat_proc = subprocess.Popen(
                [PWCAT_BIN, "--playback",
                 f"--target={PIPEWIRE_TARGET}",
                 "--format=s16",
                 f"--rate={RADIO_SAMPLE_RATE}",
                 f"--channels={RADIO_CHANNELS}", "-"],
                stdin=self.ffmpeg_proc.stdout,
                stderr=subprocess.DEVNULL,
                preexec_fn=None if sys.platform == "win32" else os.setsid
            )
            self.ffmpeg_proc.stdout.close()

    def stop(self):
        with self.lock:
            self.current_url          = None
            self.current_station_data = None
            self.current_title        = ""
            self.current_artwork      = ""
            self.stop_event.set()

            if self.is_recording:
                self.stop_recording()

            for proc in [self.ffmpeg_proc, self.pwcat_proc]:
                if proc and proc.poll() is None:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                        proc.wait(timeout=1)
                    except Exception:
                        try:
                            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                        except Exception:
                            pass
            self.ffmpeg_proc = None
            self.pwcat_proc  = None

    def toggle_record(self):
        if not self.current_station_data:
            return
        if self.is_recording:
            self.stop_recording()
        else:
            url  = self.current_url
            name = "".join(
                ch if ch.isalnum() or ch in " -" else "_"
                for ch in self.current_station_data.get("name", "radio")
            ).strip().replace(" ", "-")
            ts       = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
            filename = os.path.join(RECORDING_DIR, f"{ts}_{name}.mp3")
            try:
                self.record_proc = subprocess.Popen(
                    [FFMPEG_BIN, "-loglevel", "warning",
                     "-i", url, "-vn",
                     "-acodec", "libmp3lame", "-ab", "192k",
                     filename],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    preexec_fn=None if sys.platform == "win32" else os.setsid
                )
                self.is_recording = True
            except Exception as e:
                print(f"Failed background recording: {e}")

    def stop_recording(self):
        if self.record_proc and self.record_proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.record_proc.pid), signal.SIGTERM)
                self.record_proc.wait(timeout=1)
            except Exception:
                try:
                    os.killpg(os.getpgid(self.record_proc.pid), signal.SIGKILL)
                except Exception:
                    pass
        self.record_proc  = None
        self.is_recording = False

    def _get_bbc_metadata(self, url):
        match = re.search(r'/(bbc_[a-zA-Z0-9_]+)', url)
        if not match:
            return None
        station_id = match.group(1)
        try:
            res = requests.get(
                f"https://rms.api.bbc.co.uk/v2/services/{station_id}/segments/latest",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=2
            )
            if res.status_code == 200:
                data = res.json()
                for segment in data.get("data", []):
                    if segment.get("segment_type") == "music":
                        titles = segment.get("titles", {})
                        return f"{titles.get('primary')} - {titles.get('secondary')}"
        except Exception:
            pass
        return ""

    def _get_icy_metadata(self, url):
        if "bbc_" in url.lower():
            return self._get_bbc_metadata(url)
        headers = {"Icy-MetaData": "1", "User-Agent": "Mozilla/5.0"}
        try:
            response = requests.get(url, headers=headers, stream=True, timeout=3)
            metaint  = response.headers.get("icy-metaint")
            if metaint:
                metaint = int(metaint)
                stream  = response.raw
                stream.read(metaint)
                length_byte = stream.read(1)
                if length_byte:
                    metadata_length = ord(length_byte) * 16
                    if metadata_length > 0:
                        raw   = stream.read(metadata_length).decode("utf-8", errors="replace")
                        match = re.search(r"StreamTitle='(.*?)';", raw)
                        if match:
                            t = match.group(1).strip()
                            if t.lower() not in ["unknown", "live", "live stream", "ad"]:
                                return t
        except Exception:
            pass
        return ""

    def _fetch_artwork(self, track_title):
        if not track_title or len(track_title) < 3:
            return ""
        query = re.sub(r"\(.*?\)|\[.*?\]", "", track_title).strip()
        try:
            r = requests.get(
                "https://api.deezer.com/search",
                params={"q": query}, timeout=2
            )
            if r.status_code == 200 and r.json().get("data"):
                return r.json()["data"][0].get("album", {}).get("cover_medium", "")
        except Exception:
            pass
        return ""

    def _metadata_poller_loop(self):
        last_title = None
        while True:
            url = self.current_url
            if url:
                title = self._get_icy_metadata(url)
                if title != last_title:
                    last_title = title
                    if title:
                        self.current_title   = title
                        self.current_artwork = self._fetch_artwork(title)
                        if self.current_station_data:
                            record_track(
                                self.current_station_data.get("stationuuid", ""),
                                self.current_station_data.get("name", ""),
                                title
                            )
                    else:
                        self.current_title   = "Live Broadcast (No Data)"
                        self.current_artwork = ""
            else:
                last_title = None
            time.sleep(7)


# ═══════════════════════════════════════════════════════════════════════════════
# TWITCH ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def _twitch_refresh_user_token():
    """Use the refresh token to get a new user access token, update .env and globals."""
    global TWITCH_USER_TOKEN, TWITCH_REFRESH_TOKEN
    if not TWITCH_REFRESH_TOKEN:
        return None
    try:
        r = requests.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "grant_type":    "refresh_token",
                "refresh_token": TWITCH_REFRESH_TOKEN,
                "client_id":     TWITCH_CLIENT_ID,
                "client_secret": TWITCH_CLIENT_SECRET,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        TWITCH_USER_TOKEN    = data["access_token"]
        TWITCH_REFRESH_TOKEN = data.get("refresh_token", TWITCH_REFRESH_TOKEN)
        print("[Twitch] User token refreshed successfully")
        return TWITCH_USER_TOKEN
    except Exception as e:
        print(f"[Twitch] Token refresh error: {e}")
        return None


def _twitch_get_app_token():
    """Fetch/cache a Twitch app access token via client credentials (for streams/games)."""
    global _twitch_access_token, _twitch_token_expiry
    if _twitch_access_token and time.time() < _twitch_token_expiry - 60:
        return _twitch_access_token
    try:
        r = requests.post(
            "https://id.twitch.tv/oauth2/token",
            params={
                "client_id":     TWITCH_CLIENT_ID,
                "client_secret": TWITCH_CLIENT_SECRET,
                "grant_type":    "client_credentials",
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        _twitch_access_token = data["access_token"]
        _twitch_token_expiry = time.time() + data.get("expires_in", 3600)
        return _twitch_access_token
    except Exception as e:
        print(f"[Twitch] App token error: {e}")
        return None


def _twitch_user_headers():
    """Headers using the user token — required for /channels/followed."""
    token = (TWITCH_USER_TOKEN or "").strip() or _twitch_refresh_user_token()
    if not token:
        return None
    return {"Client-Id": TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}"}


def _twitch_app_headers():
    """Headers using the app token — for /streams, /games etc."""
    token = _twitch_get_app_token()
    if not token:
        return None
    return {"Client-Id": TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}"}


def twitch_get_followed_channels():
    """
    Fetch all channels the configured user follows via Helix /channels/followed.
    Requires TWITCH_USER_ID and TWITCH_USER_TOKEN (user access token with user:read:follows).
    Auto-retries once after refreshing token on 401.
    """
    if not TWITCH_USER_ID:
        return []

    def _fetch(hdrs):
        channels, cursor = [], None
        while True:
            params = {"user_id": TWITCH_USER_ID, "first": 100}
            if cursor:
                params["after"] = cursor
            r = requests.get(
                "https://api.twitch.tv/helix/channels/followed",
                headers=hdrs, params=params, timeout=10
            )
            if r.status_code == 401:
                return None  # signal token expired
            r.raise_for_status()
            data = r.json()
            channels.extend(data.get("data", []))
            cursor = data.get("pagination", {}).get("cursor")
            if not cursor or not data.get("data"):
                break
        return channels

    hdrs = _twitch_user_headers()
    if not hdrs:
        return []

    try:
        result = _fetch(hdrs)
        if result is None:
            # Token expired — refresh and retry once
            print("[Twitch] User token expired, refreshing...")
            new_token = _twitch_refresh_user_token()
            if not new_token:
                return []
            hdrs   = _twitch_user_headers()
            result = _fetch(hdrs) or []
        return result
    except Exception as e:
        print(f"[Twitch] Followed channels error: {e}")
        return []


def twitch_get_live_status(broadcaster_ids: list):
    """
    Check which broadcaster IDs are currently live.
    Returns dict {broadcaster_id: stream_info} for live channels.
    """
    if not broadcaster_ids:
        return {}
    hdrs = _twitch_app_headers()
    if not hdrs:
        return {}
    live = {}
    # Helix accepts up to 100 user_ids per request
    for i in range(0, len(broadcaster_ids), 100):
        batch = broadcaster_ids[i:i+100]
        try:
            r = requests.get(
                "https://api.twitch.tv/helix/streams",
                headers=hdrs,
                params=[("user_id", bid) for bid in batch],
                timeout=10,
            )
            r.raise_for_status()
            for stream in r.json().get("data", []):
                live[stream["user_id"]] = stream
        except Exception as e:
            print(f"[Twitch] Live status error: {e}")
    return live


def _twitch_kill(proc):
    if proc and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=3)
        except Exception:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass


def _twitch_streamlink_thread(login: str, display_name: str, title: str, game: str):
    """Open Twitch stream via streamlink → ffmpeg → pw-cat → TWITCH_FIFO."""
    global _twitch_stream_fd, _twitch_ffmpeg_proc, _twitch_pwcat_proc

    try:
        from streamlink import Streamlink
    except ImportError:
        print("[Twitch] streamlink not installed — pip install streamlink")
        return

    url = f"https://www.twitch.tv/{login}"
    print(f"[Twitch] Opening stream: {url}")
    _twitch_stop_event.clear()

    try:
        session = Streamlink()
        session.set_option("stream-timeout", 30)
        streams = session.streams(url)
    except Exception as e:
        print(f"[Twitch] streamlink resolve error: {e}")
        return

    stream = streams.get("audio_only") or streams.get("worst")
    if not stream:
        print(f"[Twitch] No stream found for {login}")
        return

    try:
        fd = stream.open()
    except Exception as e:
        print(f"[Twitch] stream.open() error: {e}")
        return

    # Ensure FIFO exists
    if not os.path.exists(TWITCH_FIFO):
        os.mkfifo(TWITCH_FIFO)

    ffmpeg_proc = subprocess.Popen(
        [FFMPEG_BIN, "-loglevel", "warning",
         "-i", "pipe:0",
         "-ac", "2", "-ar", "48000", "-f", "s16le", "-"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid,
    )
    pwcat_proc = subprocess.Popen(
        [PWCAT_BIN, "--playback",
         f"--target={TWITCH_PW_TARGET}",
         "--format=s16", "--rate=48000", "--channels=2", "-"],
        stdin=ffmpeg_proc.stdout,
        stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid,
    )
    ffmpeg_proc.stdout.close()

    with _twitch_lock:
        _twitch_stream_fd   = fd
        _twitch_ffmpeg_proc = ffmpeg_proc
        _twitch_pwcat_proc  = pwcat_proc
        _twitch_now_playing.update(
            login=login, display_name=display_name,
            title=title, game=game
        )

    print(f"[Twitch] Playing {display_name} — ffmpeg={ffmpeg_proc.pid} pwcat={pwcat_proc.pid}")

    try:
        while not _twitch_stop_event.is_set():
            try:
                data = fd.read(8192)
            except Exception as e:
                print(f"[Twitch] Read error: {e}")
                break
            if not data:
                print("[Twitch] EOF")
                break
            try:
                ffmpeg_proc.stdin.write(data)
            except BrokenPipeError:
                break
    finally:
        try: fd.close()
        except Exception: pass
        try: ffmpeg_proc.stdin.close()
        except Exception: pass
        _twitch_kill(ffmpeg_proc)
        _twitch_kill(pwcat_proc)
        with _twitch_lock:
            _twitch_stream_fd   = None
            _twitch_ffmpeg_proc = None
            _twitch_pwcat_proc  = None
            _twitch_now_playing.update(
                login=None, display_name=None, title=None, game=None
            )
        print("[Twitch] Stream stopped")


def twitch_stop():
    _twitch_stop_event.set()
    with _twitch_lock:
        fd = _twitch_stream_fd
        ff = _twitch_ffmpeg_proc
        pw = _twitch_pwcat_proc
    if fd:
        try: fd.close()
        except Exception: pass
    _twitch_kill(ff)
    _twitch_kill(pw)


# ── Startup ───────────────────────────────────────────────────────────────────
init_history_db()
load_stations_from_file()
engine = RadioEngine()

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    # Rename process as seen by ps/top/journalctl
    try:
        import ctypes
        ctypes.cdll.LoadLibrary('libc.so.6').prctl(15, b'Snapradio', 0, 0, 0)
    except Exception:
        pass
    try:
        from setproctitle import setproctitle
        setproctitle("Snapradio")
    except ImportError:
        pass
    yield
    # Shutdown — kill any running audio processes
    print("Lifespan shutdown: stopping audio...")
    engine.stop()
    if _play_rec_proc and _play_rec_proc.poll() is None:
        try:
            os.killpg(os.getpgid(_play_rec_proc.pid), signal.SIGTERM)
        except Exception:
            pass

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(lifespan=lifespan)
app.mount("/logos",  StaticFiles(directory=LOCAL_LOGOS_DIR), name="logos")
app.mount("/static", StaticFiles(directory=os.path.join(_SCRIPT_DIR, "static")), name="static")

@app.get("/manifest.json")
def manifest():
    p = os.path.join(_BASE_DIR, "manifest.json")
    return FileResponse(p, media_type="application/manifest+json")

_BASE_DIR = _SCRIPT_DIR

def clean_station_name(station_name):
    return "".join(c for c in station_name if c.isalnum()).strip().lower()

class PlayRequest(BaseModel):
    station: dict

# ── Page routes ───────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(TEMPLATES_DIR, "index.html")) as f:
        return f.read()

@app.get("/history", response_class=HTMLResponse)
def history_page():
    with open(os.path.join(TEMPLATES_DIR, "history.html")) as f:
        return f.read()

@app.get("/recordings", response_class=HTMLResponse)
def recordings_page():
    with open(os.path.join(TEMPLATES_DIR, "recordings.html")) as f:
        return f.read()

# ── Status ────────────────────────────────────────────────────────────────────
@app.get("/api/status")
def api_status():
    with _stations_lock:
        countries = len(_stations_by_country)
        total     = sum(len(v) for v in _stations_by_country.values())
    return {
        "load_status":    _load_status,
        "stations_file":  STATIONS_JSON,
        "file_exists":    os.path.exists(STATIONS_JSON),
        "file_size_mb":   round(os.path.getsize(STATIONS_JSON) / 1048576, 1) if os.path.exists(STATIONS_JSON) else 0,
        "countries_loaded": countries,
        "stations_loaded":  total,
    }

# ── Station data API ──────────────────────────────────────────────────────────
@app.get("/api/countries")
def api_get_countries():
    # Map of ISO codes to full names for common countries
    COUNTRY_NAMES = {
        "AD":"Andorra","AE":"UAE","AF":"Afghanistan","AG":"Antigua","AL":"Albania",
        "AM":"Armenia","AO":"Angola","AR":"Argentina","AT":"Austria","AU":"Australia",
        "AZ":"Azerbaijan","BA":"Bosnia","BD":"Bangladesh","BE":"Belgium","BF":"Burkina Faso",
        "BG":"Bulgaria","BH":"Bahrain","BJ":"Benin","BO":"Bolivia","BR":"Brazil",
        "BY":"Belarus","CA":"Canada","CD":"DR Congo","CF":"Central African Rep.",
        "CG":"Congo","CH":"Switzerland","CI":"Côte d'Ivoire","CL":"Chile","CM":"Cameroon",
        "CN":"China","CO":"Colombia","CR":"Costa Rica","CU":"Cuba","CY":"Cyprus",
        "CZ":"Czech Republic","DE":"Germany","DJ":"Djibouti","DK":"Denmark","DZ":"Algeria",
        "EC":"Ecuador","EE":"Estonia","EG":"Egypt","ES":"Spain","ET":"Ethiopia",
        "FI":"Finland","FJ":"Fiji","FR":"France","GA":"Gabon","GB":"United Kingdom",
        "GE":"Georgia","GH":"Ghana","GN":"Guinea","GQ":"Eq. Guinea","GR":"Greece",
        "GT":"Guatemala","HK":"Hong Kong","HN":"Honduras","HR":"Croatia","HT":"Haiti",
        "HU":"Hungary","ID":"Indonesia","IE":"Ireland","IL":"Israel","IN":"India",
        "IQ":"Iraq","IR":"Iran","IS":"Iceland","IT":"Italy","JM":"Jamaica","JO":"Jordan",
        "JP":"Japan","KE":"Kenya","KG":"Kyrgyzstan","KH":"Cambodia","KR":"South Korea",
        "KW":"Kuwait","KZ":"Kazakhstan","LA":"Laos","LB":"Lebanon","LK":"Sri Lanka",
        "LT":"Lithuania","LU":"Luxembourg","LV":"Latvia","LY":"Libya","MA":"Morocco",
        "MD":"Moldova","MK":"North Macedonia","ML":"Mali","MM":"Myanmar","MN":"Mongolia",
        "MO":"Macao","MR":"Mauritania","MT":"Malta","MU":"Mauritius","MX":"Mexico",
        "MY":"Malaysia","MZ":"Mozambique","NA":"Namibia","NE":"Niger","NG":"Nigeria",
        "NI":"Nicaragua","NL":"Netherlands","NO":"Norway","NP":"Nepal","NZ":"New Zealand",
        "OM":"Oman","PA":"Panama","PE":"Peru","PG":"Papua New Guinea","PH":"Philippines",
        "PK":"Pakistan","PL":"Poland","PR":"Puerto Rico","PS":"Palestine","PT":"Portugal",
        "PY":"Paraguay","QA":"Qatar","RO":"Romania","RS":"Serbia","RU":"Russia",
        "RW":"Rwanda","SA":"Saudi Arabia","SD":"Sudan","SE":"Sweden","SG":"Singapore",
        "SI":"Slovenia","SK":"Slovakia","SL":"Sierra Leone","SN":"Senegal","SO":"Somalia",
        "SR":"Suriname","SV":"El Salvador","SY":"Syria","TG":"Togo","TH":"Thailand",
        "TJ":"Tajikistan","TM":"Turkmenistan","TN":"Tunisia","TR":"Turkey","TT":"Trinidad",
        "TW":"Taiwan","TZ":"Tanzania","UA":"Ukraine","UG":"Uganda","US":"United States",
        "UY":"Uruguay","UZ":"Uzbekistan","VE":"Venezuela","VN":"Vietnam","YE":"Yemen",
        "ZA":"South Africa","ZM":"Zambia","ZW":"Zimbabwe",
    }
    with _stations_lock:
        result = [
            {
                "code":         cc,
                "name":         COUNTRY_NAMES.get(cc, cc),
                "stationcount": len(stations)
            }
            for cc, stations in _stations_by_country.items()
        ]
    result.sort(key=lambda x: x["name"])
    return {"countries": result, "default": DEFAULT_COUNTRY}

@app.get("/api/fetch-stations")
def api_fetch_stations(countrycode: str = DEFAULT_COUNTRY):
    cc = countrycode.strip().upper()
    with _stations_lock:
        raw_stations = list(_stations_by_country.get(cc, []))

    codecs   = set()
    bitrates = set()
    for s in raw_stations:
        codec = (s.get("codec") or "").strip().upper()
        if codec:
            codecs.add(codec)
        try:
            b = int(s.get("bitrate") or 0)
            if b > 0:
                bitrates.add(b)
        except Exception:
            pass

    return {
        "stations": raw_stations,
        "codecs":   sorted(codecs),
        "bitrates": sorted(bitrates),
    }

@app.post("/api/reload-stations")
def api_reload_stations(download: bool = False):
    """
    Reload station data from disk.
    Pass ?download=true to re-fetch the gz from RadioBrowser first.
    Intended for use by the nightly cron job.
    """
    try:
        if download:
            _download_stations_json()
        load_stations_from_file()
        with _stations_lock:
            total = sum(len(v) for v in _stations_by_country.values())
        return {"status": "ok", "stations_loaded": total}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

# ── Favourites ────────────────────────────────────────────────────────────────
@app.get("/api/favorites")
def api_get_favorites():
    return engine.favourites

@app.post("/api/favorites/toggle")
def api_toggle_favorite(station: dict):
    engine.toggle_favourite(station)
    return {"is_favourite": engine.is_favourite(station.get("stationuuid"))}

# ── Playback ──────────────────────────────────────────────────────────────────
@app.post("/api/local-play")
def api_local_play(req: PlayRequest):
    """
    Register the station for metadata polling without starting server-side
    audio playback. Used by the browser's local (device) player.
    """
    with engine.lock:
        engine.current_station_data = req.station
        engine.current_url          = req.station.get("url_resolved") or req.station.get("url_stream") or req.station.get("url")
        engine.current_title        = "Connecting…"
        engine.current_artwork      = ""
        engine.stop_event.clear()
    return {"status": "tracking"}

@app.post("/api/play")
def api_play(req: PlayRequest):
    engine.play(req.station)
    return {"status": "playing"}

@app.post("/api/stop")
def api_stop():
    engine.stop()
    return {"status": "stopped"}

@app.post("/api/record/toggle")
def api_toggle_record():
    engine.toggle_record()
    return {"is_recording": engine.is_recording}

@app.get("/api/now-playing")
def api_now_playing():
    station_name = engine.current_station_data.get("name", "") if engine.current_station_data else ""
    remote_fav   = engine.current_station_data.get("favicon", "") if engine.current_station_data else ""

    cleaned      = clean_station_name(station_name)
    fallback_logo = DEFAULT_ART
    if cleaned:
        for ext in [".png", ".jpg", ".jpeg", ".webp"]:
            if os.path.exists(os.path.join(LOCAL_LOGOS_DIR, f"{cleaned}{ext}")):
                fallback_logo = f"/logos/{cleaned}{ext}"
                break
    if fallback_logo == DEFAULT_ART and remote_fav:
        fallback_logo = (
            f"/proxy-image?url={urllib.parse.quote_plus(remote_fav)}"
            f"&station_name={urllib.parse.quote_plus(station_name)}"
        )

    return {
        "active":       engine.current_station_data is not None,
        "station_name": station_name or "Select a station to begin",
        "track_title":  engine.current_title or "Not Playing",
        "artwork":      engine.current_artwork if engine.current_artwork else fallback_logo,
        "is_recording": engine.is_recording,
    }

@app.get("/proxy-image")
def handle_proxy_request(url: str = Query(...), station_name: str = Query(None)):
    if station_name:
        cleaned = clean_station_name(station_name)
        if cleaned:
            for ext in [".png", ".jpg", ".jpeg", ".webp"]:
                local_check = os.path.join(LOCAL_LOGOS_DIR, f"{cleaned}{ext}")
                if os.path.exists(local_check):
                    return FileResponse(local_check)
    try:
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
        if res.status_code == 200:
            content_type = res.headers.get("Content-Type", "image/png")
            image_data   = res.content
            if station_name:
                cleaned = clean_station_name(station_name)
                if cleaned:
                    ext = ".png"
                    if "jpeg" in content_type.lower(): ext = ".jpg"
                    elif "webp" in content_type.lower(): ext = ".webp"
                    save_path = os.path.join(LOCAL_LOGOS_DIR, f"{cleaned}{ext}")
                    if not os.path.exists(save_path):
                        with open(save_path, "wb") as f:
                            f.write(image_data)
            return Response(content=image_data, media_type=content_type)
    except Exception:
        pass
    return Response(content="", status_code=404)



# ── Downloader ────────────────────────────────────────────────────────────────
@app.get("/downloader", response_class=HTMLResponse)
def downloader_page():
    with open(os.path.join(TEMPLATES_DIR, "downloader.html")) as f:
        return f.read()

@app.post("/api/downloads/download")
def api_download(data: dict):
    url = (data.get("url") or "").strip()
    if not url:
        return Response(status_code=400)

    import secrets as _sec
    job_id = _sec.token_hex(8)
    _dl_set_status(job_id, status="queued", url=url, title=None,
            artist=None, error=None, path=None, filename=None)
    _dl_queue.put((job_id, url))
    return {"job_id": job_id}

@app.get("/api/downloads/status/{job_id}")
def api_download_status(job_id: str):
    with _dl_lock:
        job = dict(_dl_jobs.get(job_id, {}))
    if not job:
        return Response(status_code=404)
    return job

@app.get("/api/downloads/jobs")
def api_downloads_jobs():
    with _dl_lock:
        return _dl_jobs

@app.get("/api/downloads")
def api_downloads_list():
    """List completed downloads on disk."""
    try:
        files = []
        for fn in sorted(os.listdir(DOWNLOADS_DIR), reverse=True):
            if fn.lower().endswith(".mp3"):
                fp = os.path.join(DOWNLOADS_DIR, fn)
                files.append({"filename": fn, "size_bytes": os.path.getsize(fp)})
        return {"downloads": files}
    except Exception as e:
        return {"downloads": [], "error": str(e)}

@app.delete("/api/downloads/{filename}")
def api_download_delete(filename: str):
    if "/" in filename or ".." in filename:
        return Response(status_code=400)
    fp = os.path.join(DOWNLOADS_DIR, filename)
    if not os.path.exists(fp):
        return Response(status_code=404)
    os.remove(fp)
    return {"status": "deleted"}

_dl_play_ffmpeg = None
_dl_play_pwcat  = None

@app.post("/api/downloads/play/{filename}")
def api_download_play(filename: str):
    global _dl_play_ffmpeg, _dl_play_pwcat
    if "/" in filename or ".." in filename:
        return Response(status_code=400)
    fp = os.path.join(DOWNLOADS_DIR, filename)
    if not os.path.exists(fp):
        return Response(status_code=404)
    for proc in [_dl_play_ffmpeg, _dl_play_pwcat]:
        if proc and proc.poll() is None:
            try: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception: pass
    try:
        ff = subprocess.Popen(
            [FFMPEG_BIN, "-loglevel", "warning", "-i", fp, "-vn",
             "-ac", str(RADIO_CHANNELS), "-ar", str(RADIO_SAMPLE_RATE),
             "-f", "s16le", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid
        )
        pw = subprocess.Popen(
            [PWCAT_BIN, "--playback", f"--target={PIPEWIRE_TARGET}",
             "--format=s16", f"--rate={RADIO_SAMPLE_RATE}",
             f"--channels={RADIO_CHANNELS}", "-"],
            stdin=ff.stdout, stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid
        )
        ff.stdout.close()
        _dl_play_ffmpeg = ff
        _dl_play_pwcat  = pw
        return {"status": "playing", "filename": filename}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/downloads/stop")
def api_download_stop():
    global _dl_play_ffmpeg, _dl_play_pwcat
    for proc in [_dl_play_ffmpeg, _dl_play_pwcat]:
        if proc and proc.poll() is None:
            try: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception: pass
    _dl_play_ffmpeg = None
    _dl_play_pwcat  = None
    return {"status": "stopped"}

# ── Twitch page & API ─────────────────────────────────────────────────────────
@app.get("/twitch", response_class=HTMLResponse)
def twitch_page():
    with open(os.path.join(TEMPLATES_DIR, "twitch.html")) as f:
        return f.read()


@app.get("/api/twitch/validate-token")
def api_twitch_validate_token():
    """
    Calls Twitch /oauth2/validate to check the current user token.
    Returns the token info or the error — useful for debugging 401s.
    """
    token = TWITCH_USER_TOKEN.strip() if TWITCH_USER_TOKEN else ""
    if not token:
        return {"error": "TWITCH_USER_TOKEN is empty in .env"}
    try:
        r = requests.get(
            "https://id.twitch.tv/oauth2/validate",
            headers={"Authorization": f"OAuth {token}"},
            timeout=10,
        )
        data = r.json()
        if r.status_code != 200:
            return {"error": f"Token invalid ({r.status_code})", "detail": data,
                    "hint": "Re-run get_twitch_token.py to get a fresh token"}
        return {
            "status":     "valid",
            "client_id":  data.get("client_id"),
            "login":      data.get("login"),
            "user_id":    data.get("user_id"),
            "scopes":     data.get("scopes"),
            "expires_in": data.get("expires_in"),
            "needs_scope": "user:read:follows" not in (data.get("scopes") or []),
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/twitch/channels")
def api_twitch_channels():
    """
    Returns followed channels merged with live status.
    Each entry: {broadcaster_id, login, display_name, is_live, title, game, viewer_count, thumbnail_url}
    """
    try:
        channels = twitch_get_followed_channels()
        if not channels:
            return {"channels": [], "error": "No followed channels found — check TWITCH_USER_ID"}

        broadcaster_ids = [ch["broadcaster_id"] for ch in channels]
        live            = twitch_get_live_status(broadcaster_ids)

        result = []
        for ch in channels:
            bid    = ch["broadcaster_id"]
            stream = live.get(bid, {})
            thumb  = stream.get("thumbnail_url", "")
            if thumb:
                thumb = thumb.replace("{width}", "440").replace("{height}", "248")
            result.append({
                "broadcaster_id": bid,
                "login":          ch["broadcaster_login"],
                "display_name":   ch["broadcaster_name"],
                "is_live":        bid in live,
                "title":          stream.get("title", ""),
                "game":           stream.get("game_name", ""),
                "viewer_count":   stream.get("viewer_count", 0),
                "thumbnail_url":  thumb,
            })

        # Live channels first, then alphabetical
        result.sort(key=lambda x: (not x["is_live"], x["display_name"].lower()))
        return {"channels": result}
    except Exception as e:
        return {"channels": [], "error": str(e)}

@app.post("/api/twitch/play")
def api_twitch_play(data: dict):
    login        = data.get("login", "")
    display_name = data.get("display_name", login)
    title        = data.get("title", "")
    game         = data.get("game", "")
    if not login:
        return Response(status_code=400)
    # Stop any existing Twitch stream first
    twitch_stop()
    time.sleep(0.3)
    threading.Thread(
        target=_twitch_streamlink_thread,
        args=(login, display_name, title, game),
        daemon=True
    ).start()
    return {"status": "playing", "login": login}

@app.post("/api/twitch/stop")
def api_twitch_stop():
    twitch_stop()
    return {"status": "stopped"}

@app.get("/api/twitch/now-playing")
def api_twitch_now_playing():
    with _twitch_lock:
        np = dict(_twitch_now_playing)
        active = _twitch_stream_fd is not None
    return {"active": active, **np}

# ── History ───────────────────────────────────────────────────────────────────
@app.get("/api/history")
def api_history(limit: int = 200, offset: int = 0, station: str = ""):
    try:
        con = sqlite3.connect(HISTORY_DB)
        con.row_factory = sqlite3.Row
        if station:
            rows  = con.execute(
                "SELECT * FROM track_history WHERE station_name LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (f"%{station}%", limit, offset)
            ).fetchall()
            total = con.execute(
                "SELECT COUNT(*) FROM track_history WHERE station_name LIKE ?",
                (f"%{station}%",)
            ).fetchone()[0]
        else:
            rows  = con.execute(
                "SELECT * FROM track_history ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
            total = con.execute("SELECT COUNT(*) FROM track_history").fetchone()[0]
        con.close()
        return {"total": total, "rows": [dict(r) for r in rows]}
    except Exception as e:
        return {"total": 0, "rows": [], "error": str(e)}

@app.delete("/api/history")
def api_clear_history():
    try:
        con = sqlite3.connect(HISTORY_DB)
        con.execute("DELETE FROM track_history")
        con.commit()
        con.close()
        return {"status": "cleared"}
    except Exception as e:
        return {"error": str(e)}

# ── Recordings ────────────────────────────────────────────────────────────────
@app.get("/api/recordings")
def api_list_recordings():
    try:
        files = []
        for fn in sorted(os.listdir(RECORDING_DIR), reverse=True):
            if fn.lower().endswith(".mp3"):
                fp = os.path.join(RECORDING_DIR, fn)
                files.append({"filename": fn, "size_bytes": os.path.getsize(fp)})
        return {"recordings": files}
    except Exception as e:
        return {"recordings": [], "error": str(e)}

@app.delete("/api/recordings/{filename}")
def api_delete_recording(filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        return Response(status_code=400)
    fp = os.path.join(RECORDING_DIR, filename)
    if not os.path.exists(fp):
        return Response(status_code=404)
    os.remove(fp)
    return {"status": "deleted"}

_play_rec_proc = None

@app.post("/api/recordings/play/{filename}")
def api_play_recording(filename: str):
    global _play_rec_proc
    if "/" in filename or ".." in filename:
        return Response(status_code=400)
    fp = os.path.join(RECORDING_DIR, filename)
    if not os.path.exists(fp):
        return Response(status_code=404)
    if _play_rec_proc and _play_rec_proc.poll() is None:
        try:
            os.killpg(os.getpgid(_play_rec_proc.pid), signal.SIGTERM)
        except Exception:
            pass
    try:
        ffmpeg = subprocess.Popen(
            [FFMPEG_BIN, "-loglevel", "warning", "-i", fp, "-vn",
             "-ac", str(RADIO_CHANNELS), "-ar", str(RADIO_SAMPLE_RATE),
             "-f", "s16le", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid
        )
        pwcat = subprocess.Popen(
            [PWCAT_BIN, "--playback",
             f"--target={PIPEWIRE_TARGET}",
             "--format=s16", f"--rate={RADIO_SAMPLE_RATE}",
             f"--channels={RADIO_CHANNELS}", "-"],
            stdin=ffmpeg.stdout, stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid
        )
        ffmpeg.stdout.close()
        _play_rec_proc = pwcat
        return {"status": "playing", "filename": filename}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/recordings/stop")
def api_stop_recording_playback():
    global _play_rec_proc
    if _play_rec_proc and _play_rec_proc.poll() is None:
        try:
            os.killpg(os.getpgid(_play_rec_proc.pid), signal.SIGTERM)
        except Exception:
            pass
    _play_rec_proc = None
    return {"status": "stopped"}

if __name__ == "__main__":
    def _shutdown(sig, frame):
        print(f"\nShutting down (signal {sig})...")
        engine.stop()
        if _play_rec_proc and _play_rec_proc.poll() is None:
            try:
                os.killpg(os.getpgid(_play_rec_proc.pid), signal.SIGTERM)
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Set process title after uvicorn takes over the process
    # ctypes approach works even without setproctitle package
    try:
        import ctypes
        ctypes.cdll.LoadLibrary('libc.so.6').prctl(15, b'Snapradio', 0, 0, 0)
    except Exception:
        pass
    try:
        from setproctitle import setproctitle
        setproctitle("Snapradio")
    except ImportError:
        pass

    uvicorn.run(app, host=APP_HOST, port=APP_PORT)
