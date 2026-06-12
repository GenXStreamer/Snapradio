# Installing the User Systemd Service

As the user you are going to run the service as:

```bash
mkdir -p ~/.config/systemd/user
```

Create the service file (or copy it from this directory):

```bash
cp /opt/Snapradio/systemd/user/Snapradio.service ~/.config/systemd/user/
```

The service uses the `%h` specifier to automatically find your home directory (assuming the virtual environment is at `~/venv`).

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

To allow the service to continue running after logout (replace `YOUR_USERNAME` with your actual username):

```bash
sudo loginctl enable-linger YOUR_USERNAME
```

Verify:

```bash
loginctl show-user YOUR_USERNAME | grep Linger
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
- **OAuth Redirect URLs:** `http://localhost:8889/callback`
- **Category:** Website Integration
- **Client Type:** Confidential

### 3. Retrieve credentials & Generate Tokens

After the application is created:

- Open the app via **Manage**
- Copy the **Client ID**
- Click **New Secret** to generate a **Client Secret**
- Store both values in your `/opt/Snapradio/.env` file.
- Run the token generator script from the project root:
  ```bash
  python get_twitch_tokens.py
  ```

### 4. Required for API access

These credentials and tokens are required to authenticate your application with the Twitch API and enable streaming features. You also need to add your numeric `TWITCH_USER_ID` to `.env`.
