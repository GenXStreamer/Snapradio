#!/usr/bin/env python3

import os
import subprocess
import threading
import shutil
import signal
import time
import tempfile
import re
import json
import atexit
import queue
import secrets
import requests
import sqlite3
import traceback
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlparse

# Load configuration values from .env file into os.environ
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, request, render_template, redirect, url_for, flash, jsonify, send_from_directory
import yt_dlp
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, APIC
from PIL import Image

app = Flask(__name__)

# Variables from .env
app.secret_key = os.environ.get("SECRET_KEY")
DATABASE = os.environ.get("DATABASE")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

OUTPUT_DIR                  = os.environ.get("OUTPUT_DIR")
STATIC_ADS_WAV              = os.environ.get("STATIC_ADS_WAV")
STATIC_RESUME_WAV           = os.environ.get("STATIC_RESUME_WAV")
STATIC_ENDED_WAV            = os.environ.get("STATIC_ENDED_WAV")
FIFO_PATH                   = os.environ.get("TWITCH_FIFO")
SNAPCAST_FIFO               = os.environ.get("SNAPCAST_FIFO")
FFMPEG_BIN                  = os.environ.get("FFMPEG_BIN")
RECORDING_DIR               = os.environ.get("RECORDING_DIR")
RADIO_CHANNELS              = int(os.environ.get("RADIO_CHANNELS"))
RADIO_SAMPLE_RATE           = int(os.environ.get("RADIO_SAMPLE_RATE"))
STATION_CHECK_INTERVAL_DAYS = int(os.environ.get("STATION_CHECK_INTERVAL_DAYS"))
STATION_CHECK_WORKERS       = int(os.environ.get("STATION_CHECK_WORKERS"))
PWCAT_BIN                   = os.environ.get("PWCAT_BIN")
PIPEWIRE_TARGET             = os.environ.get("PIPEWIRE_TARGET")
TWITCH_PIPEWIRE_TARGET      = os.environ.get("TWITCH_PIPEWIRE_TARGET")
TWITCH_CLIENT_SECRET        = os.environ.get("TWITCH_CLIENT_SECRET")
TWITCH_CLIENT_ID            = os.environ.get("TWITCH_CLIENT_ID")

# defined variables
ALLOWED_LOGO_EXTENSIONS     = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# Thread safety for mutable globals
_state_lock = threading.Lock()

# Globals (Radio)
_radio_stop_event  = threading.Event()
_radio_ffmpeg_proc = None   # ffmpeg subprocess for radio playback
_radio_pwcat_proc  = None   # pw-cat subprocess for radio playback
_current_radio_url = None   # resolved stream URL currently playing (for recording)

# Globals (Twitch)
_stream_fd           = None   # file-like fd from streamlink stream.open()
ffmpeg_process       = None   # single ffmpeg subprocess we control
twitch_pwcat_process = None
ad_break_message     = ""
_stop_event          = threading.Event()   # signals the reader thread to exit cleanly
now_playing_rowid = None
now_playing_streamer = ""

# Globals (Recording)
_record_proc       = None   # ffmpeg subprocess for recording
_recording_path    = None   # current output file path


@contextmanager
def get_db():
    """Context manager that yields a sqlite3 connection with Row factory."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_fifo(path):
    """Ensure named pipe exists at target filesystem block path."""
    if not os.path.exists(path):
        os.mkfifo(path)


def get_bbc_metadata(url):
    """Extract BBC station ID from the URL and pull live metadata from the BBC RMS API."""
    match = re.search(r'/(bbc_[a-zA-Z0-9_]+)', url)
    if not match:
        return None

    station_id = match.group(1)
    track_api = f"https://rms.api.bbc.co.uk/v2/services/{station_id}/segments/latest"
    show_api = f"https://rms.api.bbc.co.uk/v2/broadcasts/poll/{station_id}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; RadioPlayer/1.0)"}

    current_show = ""
    try:
        # 1. Grab current show/DJ context
        show_res = requests.get(show_api, headers=headers, timeout=3)
        if show_res.status_code == 200:
            show_data = show_res.json()
            if "data" in show_data and len(show_data["data"]) > 0:
                current_show = show_data["data"][0].get("titles", {}).get("primary", "")
    except Exception as e:
        app_log(f"[BBC Metadata API Error] Failed fetching show data for {station_id}: {e}")

    # Build show label with a newline character instead of square brackets
    show_prefix = f"{current_show}\n" if current_show else ""

    try:
        # 2. Grab current track item matching the real BBC payload structure
        track_res = requests.get(track_api, headers=headers, timeout=3)
        if track_res.status_code == 200:
            track_data = track_res.json()
            
            if "data" in track_data and len(track_data["data"]) > 0:
                for segment in track_data["data"]:
                    if segment.get("segment_type") == "music":
                        titles = segment.get("titles", {})
                        artist = titles.get("primary", "Unknown Artist")
                        song_title = titles.get("secondary", "Unknown Title")
                        
                        return f"{show_prefix}{artist} : {song_title}"
        
        # Fallback if the segment timeline currently contains no music tracking blocks
        return f"{show_prefix}Talk Segment / DJ Set" if current_show else "Live Broadcast"
        
    except Exception as e:
        app_log(f"[BBC Metadata API Error] Failed fetching track data for {station_id}: {e}")
    
    return f"{show_prefix}Live Broadcast" if current_show else None


def get_icy_metadata(url):
    """Parse ICY metadata from the live audio stream container."""
    if "bbc_" in url.lower():
        bbc_title = get_bbc_metadata(url)
        if bbc_title:
            return bbc_title, True

    headers = {'Icy-MetaData': '1', 'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers, stream=True, timeout=4)
        metaint = response.headers.get('icy-metaint')
        if not metaint:
            return None, False

        metaint = int(metaint)
        stream = response.raw

        stream.read(metaint)
        length_byte = stream.read(1)
        if not length_byte:
            return None, True

        metadata_length = ord(length_byte) * 16
        if metadata_length == 0:
            return None, True

        metadata_raw = stream.read(metadata_length).decode('utf-8', errors='replace')
        match = re.search(r"StreamTitle='(.*?)';", metadata_raw)
        if match:
            return match.group(1).strip(), True
        return None, True
    except Exception as e:
        app_log(f"[Metadata HTTP Error] Failed fetching ICY stream bytes: {e}")
    return None, False


def _radio_metadata_poller_loop():
    """Background polling loop for Radio ICY metadata writing directly to the database."""
    while True:
        try:
            with _state_lock:
                url = _current_radio_url
            
            if url:
                title, is_icy = get_icy_metadata(url)
                if is_icy and title and title.strip():
                    
                    with get_db() as conn:
                        current = conn.execute("SELECT track_title FROM now_playing LIMIT 1").fetchone()
                        db_title = current['track_title'] if current else None

                    if title != db_title:
                        app_log(f"[Metadata Poller] New track hit: '{title.replace(chr(10), ' ')}'. Looking up artwork...")
                        img_url = fetch_track_artwork(title)
                        
                        with get_db() as conn:
                            conn.execute("""
                                UPDATE now_playing 
                                SET track_title = ?, track_image = ?
                            """, (title, img_url))
            else:
                with get_db() as conn:
                    conn.execute("UPDATE now_playing SET track_title = '', track_image = ''")
                    
        except Exception as e:
            app_log(f"[Metadata Poller Exception] {e}")
                
        time.sleep(10)


@app.route('/radio/track_title')
def radio_track_title():
    with get_db() as conn:
        row = conn.execute("SELECT track_title, track_image FROM now_playing LIMIT 1").fetchone()
    
    if row:
        title = row['track_title']
        image = row['track_image']
        is_icy = True if title else False
    else:
        title, image, is_icy = "", "", False

    return jsonify({'track_title': title, 'track_image': image, 'is_icy': is_icy})


def _kill_proc_tree(proc):
    """Terminate a subprocess using process groups, escalating to SIGKILL after 3 s."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if hasattr(proc, 'pid') and proc.pid > 0:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
        
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if hasattr(proc, 'pid') and proc.pid > 0:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
    except OSError:
        try:
            proc.terminate()
            proc.wait(timeout=1)
        except Exception:
            pass


def _shutdown_all_streams():
    """Close the streamlink fd, kill Twitch ffmpeg, kill radio ffmpeg and pw-cat."""
    app_log("[Shutdown] Killing all child streams")
    _stop_event.set()
    _radio_stop_event.set()
    
    with _state_lock:
        fd           = _stream_fd
        twitch_ff    = ffmpeg_process
        twitch_pwcat = twitch_pwcat_process
        radio_ff     = _radio_ffmpeg_proc
        radio_pwcat  = _radio_pwcat_proc
        rec          = _record_proc

    if fd is not None:
        try:
            fd.close()
        except Exception:
            pass
            
    _kill_proc_tree(twitch_ff)
    _kill_proc_tree(twitch_pwcat)
    _kill_proc_tree(radio_ff)
    _kill_proc_tree(radio_pwcat)
    _kill_proc_tree(rec)


atexit.register(_shutdown_all_streams)


def _sigterm_handler(signum, frame):
    _shutdown_all_streams()
    raise SystemExit(0)


signal.signal(signal.SIGTERM, _sigterm_handler)

def clear_nowplaying():
    with get_db() as conn:
        conn.execute("DELETE FROM now_playing")

def clear_nowplaying_twitch():
    with get_db() as conn:
        conn.execute("DELETE FROM TwitchStatus")

def app_log(message):
    log_file = os.path.join(BASE_DIR, 'app.log')
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, 'a') as f:
        f.write(f"[{timestamp}] {message}\n")

def test_stream(url, timeout=5):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
    }
    try:
        response = requests.get(url, allow_redirects=True, timeout=timeout, headers=headers, stream=True)
        return response.status_code >= 200 and response.status_code < 400
    except requests.RequestException:
        return False


def _check_one_station(rowid: int, name: str, url: str) -> tuple[int, bool]:
    try:
        result = test_stream(url)
        app_log(f"[StationChecker] {'OK' if result else 'FAIL'} — {name} ({url})")
        return rowid, result
    except Exception as exc:
        app_log(f"[StationChecker] ERROR — {name}: {exc}")
        return rowid, False


def check_all_stations(interval_days: int = STATION_CHECK_INTERVAL_DAYS) -> None:
    cutoff = (datetime.now() - __import__("datetime").timedelta(days=interval_days)).strftime("%Y-%m-%d")

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT rowid, Name, StreamURL
            FROM   stations
            WHERE  StreamURL IS NOT NULL
              AND  StreamURL != ''
              AND  LOWER(StreamURL) NOT LIKE '%twitch.tv%'
              AND  (WorkingDate IS NULL OR WorkingDate < ?)
            ORDER BY WorkingDate ASC
            """,
            (cutoff,),
        ).fetchall()

    if not rows:
        app_log("[StationChecker] All stations checked recently — nothing to do.")
        return

    app_log(f"[StationChecker] Checking {len(rows)} station(s) "
            f"not tested since {cutoff} (interval={interval_days}d, "
            f"workers={STATION_CHECK_WORKERS})")

    today = datetime.now().strftime("%Y-%m-%d")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=STATION_CHECK_WORKERS) as pool:
        futures = {
            pool.submit(_check_one_station, row["rowid"], row["Name"], row["StreamURL"]): row
            for row in rows
        }

        for future in as_completed(futures):
            try:
                rowid, is_working = future.result()
            except Exception as exc:
                row = futures[future]
                app_log(f"[StationChecker] Unexpected future error for {row['Name']}: {exc}")
                continue

            try:
                with get_db() as conn:
                    conn.execute(
                        "UPDATE stations SET Working = ?, WorkingDate = ? WHERE rowid = ?",
                        (1 if is_working else 0, today, rowid),
                    )
            except Exception as exc:
                app_log(f"[StationChecker] DB write error for rowid={rowid}: {exc}")

    app_log("[StationChecker] Batch complete.")


def _station_checker_loop() -> None:
    interval_seconds = STATION_CHECK_INTERVAL_DAYS * 24 * 3600

    while True:
        try:
            check_all_stations()
        except Exception as exc:
            app_log(f"[StationChecker] Loop error: {exc}")

        elapsed = 0
        while elapsed < interval_seconds:
            time.sleep(60)
            elapsed += 60


def start_station_checker_daemon() -> None:
    """Spawn the station connectivity checker as a background daemon thread."""
    t = threading.Thread(target=_station_checker_loop, daemon=True, name="StationChecker")
    t.start()
    app_log(
        f"[StationChecker] Daemon started "
        f"(interval={STATION_CHECK_INTERVAL_DAYS}d, workers={STATION_CHECK_WORKERS})"
    )

def _radio_stream_thread(url):
    """Fetch a network radio stream and deliver it to PipeWire via pw-cat."""
    global _radio_ffmpeg_proc, _radio_pwcat_proc
    _radio_stop_event.clear()

    ensure_fifo(SNAPCAST_FIFO)
    app_log(f"[Radio] Starting stream: {url}")

    ffmpeg_proc = subprocess.Popen(
        [
            FFMPEG_BIN,
            "-loglevel", "warning",
            "-i", url,
            "-vn",
            "-ac", str(RADIO_CHANNELS),
            "-ar", str(RADIO_SAMPLE_RATE),
            "-f", "s16le",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        preexec_fn=os.setsid
    )

    pwcat_proc = subprocess.Popen(
        [
            PWCAT_BIN,
            "--playback",
            f"--target={PIPEWIRE_TARGET}",
            "--format=s16",
            f"--rate={RADIO_SAMPLE_RATE}",
            f"--channels={RADIO_CHANNELS}",
            "-",
        ],
        stdin=ffmpeg_proc.stdout,
        stderr=subprocess.PIPE,
        text=False,
        preexec_fn=os.setsid
    )
    ffmpeg_proc.stdout.close()

    with _state_lock:
        _radio_ffmpeg_proc = ffmpeg_proc
        _radio_pwcat_proc  = pwcat_proc

    def _log_stderr(proc, label):
        for raw in proc.stderr:
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                app_log(f"[{label}] {line}")

    threading.Thread(target=_log_stderr, args=(ffmpeg_proc, "ffmpeg/radio"), daemon=True).start()
    threading.Thread(target=_log_stderr, args=(pwcat_proc,  "pw-cat/radio"), daemon=True).start()

    while not _radio_stop_event.is_set():
        if ffmpeg_proc.poll() is not None or pwcat_proc.poll() is not None:
            break
        time.sleep(0.3)

    _kill_proc_tree(ffmpeg_proc)
    _kill_proc_tree(pwcat_proc)

    with _state_lock:
        _radio_ffmpeg_proc = None
        _radio_pwcat_proc  = None

    app_log("[Radio] Stream stopped")


def start_stream(url):
    """Stop any existing radio stream then launch a new one in a daemon thread."""
    _radio_stop_event.set()
    with _state_lock:
        old_proc   = _radio_ffmpeg_proc
        old_pwcat  = _radio_pwcat_proc
    _kill_proc_tree(old_proc)
    _kill_proc_tree(old_pwcat)

    t = threading.Thread(target=_radio_stream_thread, args=(url,), daemon=True)
    t.start()


def is_icy_stream(url):
    try:
        response = requests.head(url, timeout=5, allow_redirects=True)
        return any(h.lower().startswith("icy-") for h in response.headers) or \
               "audio" in response.headers.get("Content-Type", "").lower()
    except requests.RequestException as e:
        app_log(f"HEAD request failed: {e}")
        return False

def get_stream_url_from_playlist(url):
    try:
        url_path = url.lower().split('?')[0]
        is_playlist = url_path.endswith(('.pls', '.m3u', '.m3u8'))
        if is_icy_stream(url) and not is_playlist:
            app_log(f"[get_stream_url_from_playlist] Detected ICY or direct audio stream: {url}")
            final_url = url
        else:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
            }
            app_log (f"[get_stream_url_from_playlist] Fetching playlist: {url}")
            response = requests.get(url, timeout=1, headers=headers)
            response.raise_for_status()
            content = response.text.strip()

            if content.lower().startswith("[playlist]"):
                app_log (f"[get_stream_url_from_playlist] Detected PLS format.")
                stream_url = parse_pls(content)
            else:
                app_log (f"[get_stream_url_from_playlist] Assuming M3U format.")
                stream_url = parse_m3u(content)
    
            if not stream_url:
                app_log (f"[get_stream_url_from_playlist] No stream URLs found in the playlist.")
                return None

            app_log (f"[get_stream_url_from_playlist] Found stream URL in playlist: {stream_url}")

            final_response = requests.get(stream_url, stream=True, timeout=10, allow_redirects=True, headers=headers)
            final_url = final_response.url
            app_log (f"[get_stream_url_from_playlist] Resolved final stream URL: {final_url}")
        return final_url

    except requests.RequestException as e:
        app_log (f"[get_stream_url_from_playlist] Error fetching or resolving stream URL: {e}")
        return None

def parse_m3u(content):
    lines = [line.strip() for line in content.splitlines() if line and not line.startswith("#")]
    return lines[0] if lines else None

def parse_pls(content):
    lines = content.splitlines()
    for line in lines:
        if line.lower().startswith("file1="):
            return line.partition("=")[2].strip()
    return None

def get_stream_info(stream_url, timeout=8):
    try:
        app_log(f"[get_stream_info] Probing stream: {stream_url}")
        result = subprocess.run(
            [
                "/usr/bin/ffprobe",
                "-v", "error",
                "-user_agent", "Mozilla/5.0",
                "-protocol_whitelist", "file,http,https,tcp,tls,icy",
                "-show_streams",
                "-show_format",
                "-of", "json",
                stream_url
            ],
            capture_output=True, text=True, check=True, timeout=timeout
        )
        info = json.loads(result.stdout)

        audio = next(
            (s for s in info.get("streams", []) if s.get("codec_type") == "audio"),
            None
        )
        if not audio:
            app_log("[get_stream_info] No audio stream found")
            return None

        codec       = audio.get("codec_name")
        sample_rate = audio.get("sample_rate")
        channels    = audio.get("channels")
        layout      = audio.get("channel_layout")

        bitrate_bps = audio.get("bit_rate") or info.get("format", {}).get("bit_rate")
        bitrate_kbps = int(bitrate_bps) // 1000 if bitrate_bps else None

        app_log(f"[get_stream_info] Codec: {codec}  Sample rate: {sample_rate} Hz  "
                f"Channels: {channels} ({layout})  Bitrate: {bitrate_kbps} kbps")

        return {
            'codec':        codec,
            'sample_rate':  sample_rate,
            'bitrate_kbps': bitrate_kbps,
            'resolved_url': stream_url,
        }

    except subprocess.TimeoutExpired:
        app_log(f"[get_stream_info] Timeout probing {stream_url}")
        return None
    except subprocess.CalledProcessError as e:
        app_log(f"[get_stream_info] Error probing stream: {e}")
        return None
    except Exception as e:
        app_log(f"[get_stream_info] Unexpected error: {e}")
        return None


def probe(url, timeout=1):
    info = get_stream_info(url, timeout=timeout)
    return ({"streams": []}, url) if info is None else (info, url)


def streamlink_thread(url):
    """Open the Twitch stream entirely in Python and stream to TWITCH_PIPEWIRE_TARGET."""
    global _stream_fd, ffmpeg_process, twitch_pwcat_process, ad_break_message

    from streamlink import Streamlink
    from streamlink.options import Options

    _stop_event.clear()

    try:
        session = Streamlink()
        session.set_option("stream-timeout", 30)
        
        options = Options()
        options.set("disable-ads", True)
        options.set("low-latency", True)
        
        streams = session.streams(url, options=options)
    except Exception as e:
        app_log(f"[streamlink_thread] Failed to resolve stream: {e}")
        clear_nowplaying_twitch()
        return

    stream = streams.get("audio_only") or streams.get("worst")
    if not stream:
        app_log(f"[streamlink_thread] No suitable stream found for {url}")
        clear_nowplaying_twitch()
        return

    try:
        fd = stream.open()
    except Exception as e:
        app_log(f"[streamlink_thread] Failed to open stream: {e}")
        clear_nowplaying_twitch()
        return

    with _state_lock:
        _stream_fd = fd

    ffmpeg_proc = subprocess.Popen(
        [FFMPEG_BIN, "-loglevel", "warning",
         "-i", "pipe:0", "-ac", "2", "-f", "s16le", "-ar", "48000", "-"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid
    )
    pwcat_proc = subprocess.Popen(
        [PWCAT_BIN, "--playback",
         f"--target={TWITCH_PIPEWIRE_TARGET}",
         "--format=s16", "--rate=48000", "--channels=2", "-"],
        stdin=ffmpeg_proc.stdout,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid
    )
    ffmpeg_proc.stdout.close()

    with _state_lock:
        ffmpeg_process       = ffmpeg_proc
        twitch_pwcat_process = pwcat_proc

    app_log(f"[streamlink_thread] Stream open — ffmpeg pid={ffmpeg_proc.pid} pw-cat pid={pwcat_proc.pid}")

    def _log_stderr(proc, label):
        for raw in proc.stderr:
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                app_log(f"[{label}] {line}")

    threading.Thread(target=_log_stderr, args=(ffmpeg_proc, "ffmpeg/twitch"), daemon=True).start()
    threading.Thread(target=_log_stderr, args=(pwcat_proc,  "pw-cat/twitch"), daemon=True).start()

    try:
        while not _stop_event.is_set():
            try:
                data = fd.read(8192)
            except Exception as e:
                app_log(f"[streamlink_thread] Read error: {e}")
                break
            if not data:
                app_log("[streamlink_thread] Stream ended (EOF)")
                break
            try:
                ffmpeg_proc.stdin.write(data)
            except BrokenPipeError:
                app_log("[streamlink_thread] ffmpeg stdin closed (BrokenPipe)")
                break
    finally:
        app_log("[streamlink_thread] Cleaning up")
        try:
            fd.close()
        except Exception:
            pass
        try:
            ffmpeg_proc.stdin.close()
        except Exception:
            pass
        _kill_proc_tree(ffmpeg_proc)
        _kill_proc_tree(pwcat_proc)
        with _state_lock:
            _stream_fd           = None
            ffmpeg_process       = None
            twitch_pwcat_process = None
        clear_nowplaying_twitch()

TWITCH_CLIENT_ID     = os.environ.get("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "")

_twitch_access_token = None
_twitch_token_expiry = 0.0   # unix timestamp


def _get_twitch_app_token() -> str | None:
    global _twitch_access_token, _twitch_token_expiry

    if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
        app_log("[Twitch Poller] TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET not set — cannot poll live status")
        return None

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
        app_log("[Twitch Poller] App access token refreshed")
        return _twitch_access_token
    except Exception as e:
        app_log(f"[Twitch Poller] Failed to get app access token: {e}")
        return None


def _extract_twitch_login(url: str) -> str | None:
    m = re.search(r"twitch\.tv/([A-Za-z0-9_]+)", url)
    return m.group(1).lower() if m else None


def twitch_poller():
    """Check live status for all Twitch channels using the Helix Streams API."""
    token = _get_twitch_app_token()
    if not token:
        return

    with get_db() as conn:
        rows = conn.execute("SELECT rowid, Streamer, URL FROM Twitch").fetchall()

    if not rows:
        return

    login_to_rowid = {}
    for row in rows:
        login = _extract_twitch_login(row["URL"])
        if login:
            login_to_rowid[login.lower()] = row["rowid"]
        else:
            app_log(f"[Twitch Poller] Could not parse login from URL: {row['URL']}")

    if not login_to_rowid:
        return

    try:
        params = [("user_login", login) for login in login_to_rowid]
        r = requests.get(
            "https://api.twitch.tv/helix/streams",
            headers={
                "Client-Id":     TWITCH_CLIENT_ID,
                "Authorization": f"Bearer {token}",
            },
            params=params,
            timeout=10,
        )
        r.raise_for_status()
        
        live_logins = {stream["user_login"].lower() for stream in r.json().get("data", [])}
    except Exception as e:
        app_log(f"[Twitch Poller] Helix API error: {e}")
        return

    with get_db() as conn:
        for login, rowid in login_to_rowid.items():
            is_live = login in live_logins
            conn.execute("UPDATE Twitch SET online = ? WHERE rowid = ?", (1 if is_live else 0, rowid))

    live_count = len(live_logins)
    app_log(f"[Twitch Poller] Checked {len(login_to_rowid)} channel(s) — {live_count} live")

def _twitch_poller_loop():
    while True:
        try:
            twitch_poller()
        except Exception as e:
            app_log(f"[Twitch Poller] Error: {e}")
        time.sleep(60)

def start_twitch_poller_daemon():
    t = threading.Thread(target=_twitch_poller_loop, daemon=True)
    t.start()

def fetch_track_artwork(metadata_text):
    """Cleans metadata text and searches Deezer/iTunes for an album cover image url."""
    if not metadata_text or not metadata_text.strip():
        return ""

    # Fix: If the metadata contains a newline character (from the BBC extractor),
    # discard the show info on the first line and focus solely on the Track info on the second line.
    if "\n" in metadata_text:
        lines = metadata_text.split("\n")
        if len(lines) > 1 and lines[1].strip():
            query_source = lines[1]
        else:
            query_source = lines[0]
    else:
        query_source = metadata_text

    app_log(f"[Artwork Poller] Searching for '{query_source}'")

    # Strip bracket contents and clean up formatting characters
    query = re.sub(r'\(.*?\)|\[.*?\]', '', query_source).strip()
    
    if not query or "DJ Set" in query or "Broadcast" in query:
        return ""

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; RadioPlayer/1.0)'}

    # 1. Deezer API Lookup
    try:
        url = "https://api.deezer.com/search"
        response = requests.get(url, params={'q': query}, headers=headers, timeout=3)
        if response.status_code == 200:
            data = response.json()
            if data.get('data'):
                album_art = data['data'][0].get('album', {}).get('cover_medium', '')
                if album_art:
                    app_log(f"[Artwork Poller] Found '{query_source}' in Deezer")
                    return album_art
    except Exception as e:
        app_log(f"[Artwork Lookup] Deezer failed: {e}")

    # 2. iTunes API Backup
    try:
        url = "https://itunes.apple.com/search"
        response = requests.get(url, params={'term': query, 'media': 'music', 'limit': 1}, headers=headers, timeout=3)
        if response.status_code == 200:
            data = response.json()
            if data.get('resultCount', 0) > 0:
                art_url = data['results'][0].get('artworkUrl100', '')
                if art_url:
                    app_log(f"[Artwork Poller] Found '{query_source}' in itunes")
                    return art_url.replace('100x100bb.jpg', '400x400bb.jpg')
    except Exception as e:
        app_log(f"[Artwork Lookup] iTunes fallback failed: {e}")

    return ""

@app.route('/')
def index():
    with get_db() as conn:
        stations = conn.execute("SELECT rowid, * FROM stations").fetchall()
        now_playing_row = conn.execute("""
            SELECT s.rowid, s.Name, s.Code, s.Description, s.Category, s.Comment,
                   s.Bitrate, s."Stereo/Mono", s.StreamURL, s.Working, s.WorkingDate, s.IMG,
                   s.Codec, s.SampleRate
            FROM now_playing np
            JOIN stations s ON s.rowid = np.station_id
            LIMIT 1
        """).fetchone()

        track_row = conn.execute("SELECT track_title FROM now_playing LIMIT 1").fetchone()
        db_has_icy = True if (track_row and track_row['track_title']) else False

    now_playing = None
    if now_playing_row:
        now_playing = dict(now_playing_row)
        with _state_lock:
            now_playing['FinalURL'] = _current_radio_url or now_playing.get('StreamURL')
            now_playing['is_icy'] = db_has_icy

    return render_template("index.html", stations=stations, now_playing=now_playing)

@app.route('/play/<int:station_id>')
def play_station(station_id):
    with get_db() as conn:
        station = conn.execute("SELECT rowid, * FROM stations WHERE rowid=?", (station_id,)).fetchone()

    if not station:
        return redirect(url_for('index'))

    stream_url = station['StreamURL']
    today = datetime.now().strftime("%Y-%m-%d")

    is_working = test_stream(stream_url)

    codec = sample_rate = bitrate_kbps = None
    final_url = None
    if is_working:
        final_url = get_stream_url_from_playlist(stream_url)
        info = get_stream_info(final_url)

        if info:
            codec        = info.get("codec")
            sample_rate  = info.get("sample_rate")
            bitrate_kbps = info.get("bitrate_kbps")

    with get_db() as conn:
        conn.execute("""
            UPDATE stations
            SET Working      = ?,
                WorkingDate  = ?,
                Bitrate      = COALESCE(?, Bitrate),
                Codec        = COALESCE(?, Codec),
                SampleRate   = COALESCE(?, SampleRate)
            WHERE rowid = ?
        """, (1 if is_working else 0, today, bitrate_kbps, codec, sample_rate, station_id))

        if is_working:
            conn.execute("DELETE FROM now_playing")
            conn.execute(
                "INSERT INTO now_playing (station_id, started_at, track_title, track_image) VALUES (?, ?, '', '')",
                (station_id, today)
            )

    if is_working and final_url:
        global _current_radio_url
        with _state_lock:
            _current_radio_url = final_url
            
        threading.Thread(target=start_stream, args=(final_url,), daemon=True).start()

    return redirect(url_for('index'))

@app.route('/stop')
def stop_stream():
    _radio_stop_event.set()
    with _state_lock:
        proc  = _radio_ffmpeg_proc
        pwcat = _radio_pwcat_proc
        global _current_radio_url
        _current_radio_url = None
    _kill_proc_tree(proc)
    _kill_proc_tree(pwcat)
    _stop_recording()
    clear_nowplaying()
    return redirect(url_for('index'))


def _start_recording(url, label):
    global _record_proc, _recording_path

    os.makedirs(RECORDING_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = "".join(c for c in label if c.isalnum() or c in " ._-").strip()
    filename = f"{timestamp}_{safe_label}.mp3"
    out_path = os.path.join(RECORDING_DIR, filename)

    proc = subprocess.Popen(
        [FFMPEG_BIN, "-loglevel", "warning",
         "-i", url,
         "-vn",
         "-codec:a", "libmp3lame",
         "-b:a", "320k",
         out_path],
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid
    )

    def _log_stderr():
        for raw in proc.stderr:
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                app_log(f"[record] {line}")
    threading.Thread(target=_log_stderr, daemon=True).start()

    with _state_lock:
        _record_proc    = proc
        _recording_path = out_path


def _stop_recording():
    global _record_proc, _recording_path
    with _state_lock:
        proc, _record_proc   = _record_proc, None
        path, _recording_path = _recording_path, None
    if proc and proc.poll() is None:
        _kill_proc_tree(proc)


@app.route('/radio/popout/<int:station_id>')
def radio_popout(station_id):
    with get_db() as conn:
        station = conn.execute("SELECT * FROM stations WHERE rowid=?", (station_id,)).fetchone()

    if not station:
        return "Station not found", 404

    return render_template(
        "popout.html",
        name=station['Name'],
        stream_url=station['StreamURL'],
        category=station['Category'],
        img=station['IMG']
    )


@app.route('/radio/resolve_stream')
def resolve_stream():
    url = request.args.get('url', '')
    if not url:
        return jsonify({'url': ''})

    if '.m3u8' in url.lower():
        return jsonify({'url': url})

    if any(ext in url.lower() for ext in ['.m3u', '.pls', 'sharptemp.pls']):
        try:
            app_log(f"[Resolver] Inspecting playlist container: {url}")
            r = requests.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
            lines = r.text.splitlines()

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                if line.startswith('http://') or line.startswith('https://'):
                    app_log(f"[Resolver] Found M3U direct audio endpoint: {line}")
                    return jsonify({'url': line})

                if '=' in line:
                    key, value = line.split('=', 1)
                    if key.strip().lower().startswith('file') and ('http://' in value or 'https://' in value):
                        target_url = value.strip()
                        app_log(f"[Resolver] Found PLS direct audio endpoint: {target_url}")
                        return jsonify({'url': target_url})

        except Exception as e:
            app_log(f"[Resolver] Error parsing container text file {url}: {e}")

    return jsonify({'url': url})

@app.route('/record/start', methods=['POST'])
def record_start():
    data = request.get_json()
    url   = data.get('url')
    label = data.get('label', 'recording')
    if not url:
        return jsonify({'status': 'error', 'message': 'No URL provided'}), 400
    with _state_lock:
        already = _record_proc is not None and _record_proc.poll() is None
    if already:
        return jsonify({'status': 'already_recording'})
    _start_recording(url, label)
    with _state_lock:
        path = _recording_path
    return jsonify({'status': 'recording', 'path': path})


@app.route('/record/stop', methods=['POST'])
def record_stop():
    with _state_lock:
        path = _recording_path
    _stop_recording()
    return jsonify({'status': 'stopped', 'path': path})


@app.route('/record/status')
def record_status():
    with _state_lock:
        active = _record_proc is not None and _record_proc.poll() is None
        path   = _recording_path
    return jsonify({'recording': active, 'path': path})

@app.route('/edit')
def edit_stations():
    with get_db() as conn:
        stations = conn.execute("SELECT rowid, * FROM stations").fetchall()
    return render_template('edit.html', stations=stations)

@app.route('/update_station', methods=['POST'])
def update_station():
    data = request.get_json()
    try:
        rowid = data['rowid']
        with get_db() as conn:
            conn.execute("""
                UPDATE stations
                SET Name=?, Code=?, Description=?, Category=?, Comment=?, Bitrate=?,
                    "Stereo/Mono"=?, StreamURL=?, WorkingDate=?, IMG=?
                WHERE rowid=?
            """, (
                data.get('Name'), data.get('Code'), data.get('Description'),
                data.get('Category'), data.get('Comment'), data.get('Bitrate'),
                data.get('StereoMono'), data.get('StreamURL'), data.get('WorkingDate'),
                data.get('IMG'), rowid
            ))
        return jsonify({'status': 'success'})
    except Exception as e:
        app_log(traceback.format_exc())
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/upload_logo/<filename>', methods=['POST'])
def upload_logo(filename):
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in ALLOWED_LOGO_EXTENSIONS:
        return jsonify({'status': 'error', 'message': 'Invalid file type'}), 400
    file = request.files.get('file')
    if not file:
        return jsonify({'status': 'error', 'message': 'No file provided'}), 400
    save_path = os.path.join(app.static_folder, 'logos', os.path.basename(filename))
    file.save(save_path)
    return jsonify({'status': 'uploaded', 'filename': filename})

@app.route('/add_station', methods=['POST'])
def add_station():
    data = request.get_json()
    try:
        with get_db() as conn:
            conn.execute("""
                INSERT INTO stations (Name, Code, Description, Category, Comment, Bitrate, "Stereo/Mono", StreamURL, WorkingDate, IMG)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get('Name'), data.get('Code'), data.get('Description'),
                data.get('Category'), data.get('Comment'), data.get('Bitrate'),
                data.get('StereoMono'), data.get('StreamURL'), data.get('WorkingDate'),
                data.get('IMG')
            ))
        return jsonify({'status': 'success'})
    except Exception as e:
        app_log(traceback.format_exc())
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/delete_station', methods=['POST'])
def delete_station():
    data = request.get_json()
    rowid = data.get('rowid')
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM stations WHERE rowid = ?", (rowid,))
        return jsonify({'status': 'success'})
    except Exception as e:
        app_log(traceback.format_exc())
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.ico', mimetype='image/vnd.microsoft.icon')


# Twitch app routes
@app.route('/twitch')
def twitch_page():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT rowid, streamer, url, online FROM Twitch ORDER BY streamer ASC"
        ).fetchall()

    enriched = []
    for row in rows:
        enriched.append({
            'rowid':    row['rowid'],
            'streamer': row['streamer'],
            'url':      row['url'],
            'live':     row['online'],
        })
    return render_template("twitch.html", streams=enriched)

@app.route('/twitch/play/<int:rowid>', methods=['POST'])
def play_twitch_stream(rowid):
    global now_playing_rowid, now_playing_streamer, _stream_fd, ffmpeg_process, twitch_pwcat_process

    with get_db() as conn:
        result = conn.execute("SELECT Streamer, URL FROM Twitch WHERE rowid = ?", (rowid,)).fetchone()

    if not result:
        return jsonify({'status': 'error'})

    streamer_name = result['Streamer']
    url = result['URL']

    with _state_lock:
        if _stream_fd is not None:
            app_log(f"[play_stream] Channel change detected. Stopping current stream for new selection: {streamer_name}")
            _stop_event.set()
            old_fd = _stream_fd
            old_ff = ffmpeg_process
            old_pw = twitch_pwcat_process
            _stream_fd = None
            ffmpeg_process = None
            twitch_pwcat_process = None
        else:
            old_fd = old_ff = old_pw = None

    if old_fd:
        try:
            old_fd.close()
        except Exception as e:
            app_log(f"[play_stream] Error closing old stream file descriptor: {e}")

    if old_ff:
        _kill_proc_tree(old_ff)
    if old_pw:
        _kill_proc_tree(old_pw)

    _stop_event.clear()

    with _state_lock:
        now_playing_streamer = streamer_name
        now_playing_rowid = rowid

    with get_db() as conn:
        conn.execute("""
            INSERT INTO TwitchStatus (id, rowid, streamer, started_at)
            VALUES (1, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                rowid=excluded.rowid,
                streamer=excluded.streamer,
                started_at=excluded.started_at
        """, (rowid, streamer_name))

    threading.Thread(target=streamlink_thread, args=(url,), daemon=True).start()
    return jsonify({'status': 'playing', 'streamer': streamer_name})


@app.route('/twitch/stop', methods=['POST'])
def twitch_stop_stream():
    global ffmpeg_process, now_playing_rowid, now_playing_streamer

    clear_nowplaying_twitch()
    _stop_event.set()

    with _state_lock:
        ff_proc, ffmpeg_process = ffmpeg_process, None
        had_stream = _stream_fd is not None
        now_playing_rowid    = None
        now_playing_streamer = ""

    _kill_proc_tree(ff_proc)
    return jsonify({'status': 'stopped' if had_stream else 'not_running'})

@app.route('/twitch/now_playing')
def now_playing_status():
    with get_db() as conn:
        result = conn.execute(
            "SELECT rowid, streamer FROM TwitchStatus WHERE id = 1"
        ).fetchone()

    with _state_lock:
        is_running = _stream_fd is not None

    if result:
        status = 'playing'
        rowid = result['rowid']
        streamer = result['streamer']
    else:
        status = 'stopped'
        rowid = None
        streamer = None

    with _state_lock:
        ad_msg = ad_break_message

    return jsonify({'status': status, 'rowid': rowid, 'streamer': streamer, 'ad_break': ad_msg})

@app.route('/twitch/status')
def twitch_status_msg():
    with _state_lock:
        ad_msg = ad_break_message
    return jsonify({'ad_break': ad_msg})

@app.route("/mixcloud", methods=["GET", "POST"])
def mixcloud():
    if request.method == "POST":
        url = request.form.get("url")
        if not url:
            flash("❌ No URL provided.", "danger")
            return redirect(url_for("mixcloud"))

        tmpdir = tempfile.mkdtemp()
        try:
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"),
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "0",
                }],
                "embedmetadata": True,
                "quiet": True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

            title = info.get("title", "Unknown Title")
            artist = info.get("uploader", "Unknown Artist")

            audio_file = next(
                os.path.join(tmpdir, f) for f in os.listdir(tmpdir) if f.endswith(".mp3")
            )

            parsed = urlparse(url)
            mixcloud_key = parsed.path.strip("/")
            api_url = f"https://api.mixcloud.com/{mixcloud_key}/"
            artwork_path = None

            try:
                r = requests.get(api_url, timeout=10)
                r.raise_for_status()
                data = r.json()
                pictures = data.get("pictures", {})
                art_url = (
                    pictures.get("extra_large")
                    or pictures.get("large")
                    or pictures.get("medium")
                )
                if art_url:
                    artwork_path = os.path.join(tmpdir, "cover.jpg")
                    img_data = requests.get(art_url, timeout=10).content
                    with open(artwork_path, "wb") as f:
                        f.write(img_data)
            except Exception:
                artwork_path = None

            safe_title  = "".join(c for c in title  if c.isalnum() or c in " ._-")
            safe_artist = "".join(c for c in artist if c.isalnum() or c in " ._-")
            final_filename = f"{safe_artist}-{safe_title}.mp3"
            final_path = os.path.join(OUTPUT_DIR, final_filename)

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
                        encoding=3, mime="image/jpeg", type=3, desc="Cover", data=img.read()
                    )

            audio.save()
            shutil.move(audio_file, final_path)

            return render_template("mixcloud_done.html", title=title, artist=artist, path=final_path)

        except Exception as e:
            flash(f"❌ Error: {str(e)}")
            return redirect(url_for("mixcloud"))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    return render_template("mixcloud_index.html")


# ─────────────────────────────────────────────────────────────────────────────
# Gunicorn / Production Startup Routine Hook
# ─────────────────────────────────────────────────────────────────────────────
def master_production_initialize():
    """Initializes system structures and daemons safely inside production environments."""
    app_log("[Startup] Worker process preparing system layout and initial cleanup routines.")
    
    try:
        clear_nowplaying()
        clear_nowplaying_twitch()
    except Exception as e:
        app_log(f"[Startup Initialization Warning] Database setup error: {e}")

    ensure_fifo(SNAPCAST_FIFO)
    app_log(f"[Startup] FIFO validation sequence complete: {SNAPCAST_FIFO}")

    # Fire production-isolated background worker loops
    start_twitch_poller_daemon()
    start_station_checker_daemon()
    threading.Thread(target=_radio_metadata_poller_loop, daemon=True).start()

# Automatically run the production initialization when imported by Gunicorn
master_production_initialize()


if __name__ == '__main__':
    try:
        app.run(debug=True, host='0.0.0.0', port=8881)
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown_all_streams()
