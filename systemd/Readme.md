# Installing the User Systemd Service

As the user you are going to run the service as:

```bash
mkdir -p ~/.config/systemd/user
```

Create the service file:

```bash
nano ~/.config/systemd/user/Snapradio.service
```

Add the content of Snapradio.service to it, replacing the paths as appropriate.

Reload the user systemd daemon:

```bash
systemctl --user daemon-reload
```

Enable the service to start automatically when the user logs in:

```bash
systemctl --user enable Snapradio.service
```

Start the service:

```bash
systemctl --user start Snapradio.service
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

To allow the service to continue running after logout:

```bash
sudo loginctl enable-linger Snapradio-User
```

Verify:

```bash
loginctl show-user Snapradio-User | grep Linger
```

The SnapRadio service will now start automatically at boot and continue running even when no user session is active.


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
