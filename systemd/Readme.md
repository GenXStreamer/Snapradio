# Installing the User Systemd Service

As the user you are going to run the service as:

```bash
mkdir -p ~/.config/systemd/user
```

Create the service file (or copy it from this directory):

```bash
cp /opt/Snapradio/systemd/user/* ~/.config/systemd/user/
```

The service uses the `%h` specifier to automatically find your home directory (assuming the virtual environment is at `~/venv`).

Reload the user systemd daemon:

```bash
systemctl --user daemon-reload
```

Enable the service to start automatically when the user logs in:

```bash
systemctl --user enable Snapradio.service reload-stations.timer
```

Start the service:

```bash
systemctl --user start Snapradio.service reload-stations.timer
```

Check service status:

```bash
systemctl --user status Snapradio.service
```

View logs:

```bash
journalctl --user -u Snapradio.service -f
```

---

## Running When Not Logged In

To allow the service to continue running after logout (replace `YOUR_USERNAME` with your actual username):

```bash
sudo loginctl enable-linger YOUR_USERNAME
```

Verify:

```bash
loginctl show-user YOUR_USERNAME | grep Linger
```

The SnapRadio service will now start automatically at boot and continue running even when no user session is active.
Radio stations will be updated every 12hrs

