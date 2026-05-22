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

## License

Add your preferred license here.
