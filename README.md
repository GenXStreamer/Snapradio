# SnapRadio

SnapRadio is a modern, web-based interface for playing global Internet radio and Twitch streams, designed for seamless integration with Snapcast via PipeWire/PulseAudio FIFOs.

Built with **FastAPI** and **Tailwind CSS**, it provides a responsive mobile-friendly experience for managing your radio listening and media downloads.

## Features

- **Global Radio Discovery:** Browses tens of thousands of stations worldwide via the [Radio-Browser.info](https://www.radio-browser.info/) API.
- **Twitch Integration:** Play Twitch audio streams directly to a dedicated Snapcast source.
- **Media Downloader:** A versatile background downloader for Mixcloud, YouTube, and other platforms with automatic MP3 conversion, ID3 tagging, and artwork embedding.
- **Snapcast Ready:** Orchestrates audio routing to local FIFOs for multi-room synchronization.
- **Mobile Optimized:** A clutter-free, responsive UI that works perfectly on phones and tablets.
- **Track History:** Automatically logs played tracks and fetches album artwork via Deezer.

## Architecture

SnapRadio has been refactored for performance and simplicity:
- **FastAPI Backend:** Replacing the old Flask implementation for better concurrency and speed.
- **Global Data Source:** No longer maintains a local `stations.db`. It pulls live data from the Radio-Browser community project.
- **Simplified Routing:** Removed legacy Ads/Announcement FIFO mixing in favor of direct, high-quality streams.

## Audio Routing

SnapRadio routes audio to the following PipeWire/PulseAudio devices (configured in `.env`):

| Source | Default FIFO | Purpose |
|----------|----------|----------|
| Radio Player | `/tmp/snapcastDAB` | Main radio stream output |
| Twitch Player | `/tmp/Twitch` | Dedicated Twitch audio output |

---

## Requirements

### System Packages
- `ffmpeg`: Required for audio transcoding and downloading.
- `pw-cat`: Required for PipeWire audio playback.
- `streamlink`: Required for Twitch stream extraction.

### Python Environment
SnapRadio requires Python 3.10+.

```bash
pip install -r requirements.txt
```

---

# Installation & Setup

## 1. Clone & Prepare
```bash
git clone https://github.com/GenXStreamer/Snapradio.git /opt/Snapradio
cd /opt/Snapradio
cp .env_example .env
```

## 2. PipeWire Configuration
Create PipeWire pulse-sink configurations to host the FIFOs. Example for `snapcastDAB`:

File: `~/.config/pipewire/pipewire-pulse.conf.d/snapcastDAB.conf`
```ini
pulse.cmd = [
  { cmd = "load-module"
    args = "module-pipe-sink file=/tmp/snapcastDAB sink_name=snapcastDAB format=s16le rate=48000 channels=2"
  }
]
```
Repeat for the Twitch sink as defined in your `.env`.

## 3. Running the Application
The main entry point is `main.py`.

```bash
python main.py
```
By default, the UI is available at `http://localhost:8882`.

---

## Media Downloader
The built-in Downloader (formerly Mixcloud Downloader) supports a wide range of URLs. 
- Processes jobs in a background queue.
- Shows real-time status: **Queued** → **Downloading** → **Encoding** → **Done**.
- Status persists across page refreshes and navigation.
- Saves tagged MP3s to the `downloads/` directory.

## Twitch Setup
To use Twitch features, you must provide a Client ID and Secret in your `.env`. 
1. Register an app at [Twitch Dev Console](https://dev.twitch.tv/).
2. Set the Redirect URL to `http://localhost`.
3. Generate a User Token using the provided `get_twitch_token.py` (if available) or similar OAuth flow.

## Snapserver Configuration
Example `snapserver.conf` input:
```ini
source = pipe:///tmp/snapcastDAB?name=Radio&mode=read
source = pipe:///tmp/Twitch?name=Twitch&mode=read
```

---

## Acknowledgements

- **[Radio-Browser.info](https://www.radio-browser.info/):** For providing the incredible open-source station database.
- **[Snapcast](https://github.com/snapcast/snapcast):** The foundation of this multi-room setup.
- **[yt-dlp](https://github.com/yt-dlp/yt-dlp):** Powering the media downloader.

## License
TBA
