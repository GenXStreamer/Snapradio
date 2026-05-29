# SnapRadio

SnapRadio is a web-based interface for playing Internet radio streams and Twitch streams to local FIFO devices for integration with Snapcast.

The application uses Flask as the web server but can be deployed using your preferred WSGI application server.

## Features

- Play Internet radio stations to a local FIFO.
- Play Twitch stream audio to a separate local FIFO.
- Designed for integration with Snapcast.
- Supports advertisement detection and insertion of custom audio announcements.
- Uses PipeWire FIFO sinks for audio routing.

## Audio Routing

SnapRadio uses the following FIFO devices:

| Source | FIFO |
|----------|----------|
| Radio Player | `/tmp/snapcastDAB` |
| Twitch Player | `/tmp/TwitchFIFO` |
| Advertisements / Announcements | `/tmp/Ads` |

Within Snapserver, the Twitch and Radio FIFOs can be combined with the Ads FIFO to inject messages whenever an advertisement is detected.

> **Note:** These FIFO devices must exist before starting the application.

---

## Requirements

### System Packages

Install the following packages:

- `ffmpeg`
- `pw-cat`

### Python Packages

Install the required Python modules:

```bash
pip install -r requirements.txt
```
---

# Installation

## Set up Python virtual environment

Create a Python virtual environment in your home directory:

```bash
python -m venv ~/venv
```

Activate the virtual environment:

```bash
source ~/venv/bin/activate
```

Install requirements:

```bash
pip install -r requirements.txt
```

---
# Clone repo

```bash
sudo mkdir /opt/Snapradio
sudo chown USER:USER /opt/Snapradio
cd /opt/
git clone https://github.com/GenXStreamer/Snapradio.git
```
If you want to run as a service, there is systemd example. See the Readme.md in user/systemd

# Configuration

```bash
mv .env_example .env
```

Then edit .env to fit your environment
Be sure to update the APP_SECRET and TWITCH_ API settings. See below for TWITCH info

---

## PipeWire Configuration

The following examples have been tested on:

- Linux Mint
- Ubuntu

Create the directory if it does not already exist:

```bash
mkdir -p ~/.config/pipewire/pipewire-pulse.conf.d
```

### Radio Sink

Create:

```text
~/.config/pipewire/pipewire-pulse.conf.d/snapcastDAB.conf
```

Contents:

```ini
pulse.cmd = [
  { cmd = "load-module"
    args = "module-pipe-sink file=/tmp/snapcastDAB sink_name=snapcastDAB format=s16le rate=48000 channels=2"
  }
]
```

### Twitch Sink

Create:

```text
~/.config/pipewire/pipewire-pulse.conf.d/TwitchFIFO.conf
```

Contents:

```ini
pulse.cmd = [
  { cmd = "load-module"
    args = "module-pipe-sink file=/tmp/TwitchFIFO sink_name=TwitchFIFO format=s16le rate=48000 channels=2"
  }
]
```

### Advertisement Sink

Create:

```text
~/.config/pipewire/pipewire-pulse.conf.d/Ads.conf
```

Contents:

```ini
pulse.cmd = [
  { cmd = "load-module"
    args = "module-pipe-sink file=/tmp/Ads sink_name=Ads format=s16le rate=48000 channels=2"
  }
]
```

---

## Restart PipeWire

After creating the configuration files, restart PipeWire:

```bash
systemctl --user restart pipewire pipewire-pulse
```

Verify that the sinks have been created:

```bash
pactl list short sinks
```

---

## Running SnapRadio

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the Flask application:

```bash
python app.py
```

Or deploy using your preferred WSGI server such as:

- Gunicorn
- uWSGI
- Waitress

---

## Snapcast Integration

Typical setup:

```text
Internet Radio ──> /tmp/snapcastDAB ─┐
                                     ├─> Snapserver Input
Twitch Stream ──> /tmp/TwitchFIFO ───┤
                                     │
Advertisements ──> /tmp/Ads ─────────┘
```

This allows SnapRadio to provide continuous audio playback while inserting custom announcements whenever advertisements are detected.


## Snapserver Configuration

Example `snapserver.conf` input configuration:

```ini
source = pipe:///tmp/snapcastDAB?name=DAB&mode=read
source = pipe:///tmp/TwitchFIFO?name=Twitch_Hidden&codec=null
source = pipe:///tmp/Ads?name=Ads&codec=null
source = meta:///Ads/Twitch_Hidden/?name=Twitch
```

### How It Works

| Source | Purpose |
|----------|----------|
| `DAB` | Internet radio output from SnapRadio |
| `Twitch_Hidden` | Raw Twitch audio stream |
| `Ads` | Advertisement or announcement audio |
| `Twitch` | Combined metadata source that mixes Ads and Twitch audio |

### Notes

- The `codec=null` parameter hides a source from the Snapweb interface while still allowing it to be used internally by Snapserver.
- `Twitch_Hidden` and `Ads` are intended as internal sources only.
- The `meta:///Ads/Twitch_Hidden/` source creates a single visible stream called `Twitch`, allowing advertisements or announcements to be injected into the Twitch audio path.
- The `DAB` source remains available as a standalone radio stream.

### Source Layout

```text
                 +----------------+
                 | /tmp/Ads       |
                 +--------+-------+
                          |
                          v
+----------------+    +--------------------+
| /tmp/TwitchFIFO| -> | meta:///Ads/       |
| Twitch_Hidden  |    | Twitch_Hidden/     |
+----------------+    +---------+----------+
                                |
                                v
                           Visible as
                             "Twitch"


+----------------+
| /tmp/snapcastDAB |
+--------+-------+
         |
         v
    Visible as
       "DAB"
```

## Radio Station Database

SnapRadio stores radio station information in:

```text
stations.db
```

This SQLite database contains station names, stream URLs, metadata, and RadioFeeds identifiers.

### Editing Stations

Stations can be managed through the built-in web editor:

```text
http://your-server-ip:8881/edit
```

The editor allows stations to be added, removed, or modified without directly accessing the database.

### Station Sources

Most UK radio stations are sourced from:

http://radiofeeds.co.uk

RadioFeeds provides an extensive and regularly maintained catalogue of Internet radio stream URLs.

---

## RadioFeeds Update Script

A companion update script is included to keep station URLs current.

The script:

1. Reads station records from `stations.db`
2. Uses the stored RadioFeeds station ID to look up the latest stream information
3. Checks for updated stream URLs on RadioFeeds
4. Updates the database automatically when changes are found
5. Prefers AAC streams over MP3 streams when multiple formats are available

This helps ensure stations continue to work when broadcasters change streaming providers or URLs.

---

## Twitch Application Setup

To enable Twitch streaming functionality, you must register this application with Twitch.

### 1. Register the application

- Go to: https://dev.twitch.tv/
- Sign in with your Twitch account
- Navigate to **Your Console**
- Select **Register Your Application**

### 2. Application configuration

When registering your app, use the following settings:

- **Name:** Any descriptive name (e.g. your app name)
- **OAuth Redirect URLs:** `https://localhost` (or any localhost URL if required)
- **Category:** Website Integration
- **Client Type:** Confidential

### 3. Retrieve credentials

After the application is created:

- Open the app via **Manage**
- Copy the **Client ID**
- Click **New Secret** to generate a **Client Secret**
- Store both values securely

### 4. Required for API access

These credentials are required to authenticate your application with the Twitch API and enable streaming features.

## Acknowledgements/

### RadioFeeds UK

Special thanks to:

http://http://radiofeeds.co.uk/
for maintaining one of the most comprehensive and reliable sources of UK Internet radio stream information.

### Snapcast

SnapRadio is designed to work alongside:

https://github.com/snapcast/snapcast

an excellent multi-room audio synchronization system that makes it possible to distribute radio and Twitch audio throughout a home or network with precise synchronization.

## License

TBA
