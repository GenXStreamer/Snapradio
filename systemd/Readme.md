# Installing Gunicorn

# Installing the User Systemd Service

As the user you are going to run the service as:

```bash
mkdir -p ~/.config/systemd/user
```

Create the service file:

```bash
nano ~/.config/systemd/user/snapradio.service
```

Add the content of snapradio.service to it, replacing the paths as appropriate.

Reload the user systemd daemon:

```bash
systemctl --user daemon-reload
```

Enable the service to start automatically when the user logs in:

```bash
systemctl --user enable snapradio.service
```

Start the service:

```bash
systemctl --user start snapradio.service
```

Check service status:

```bash
systemctl --user status snapradio.service
```

View logs:

```bash
journalctl --user -u snapradio.service -f
```

---

## Running When Not Logged In

To allow the service to continue running after logout:

```bash
sudo loginctl enable-linger Snapradio-User
```

Verify:

```bash
loginctl show-user Snapradio-User | grep Linger
```

The SnapRadio service will now start automatically at boot and continue running even when no user session is active.
