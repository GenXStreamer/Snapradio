import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import requests
from dotenv import load_dotenv

# Load credentials from your existing .env file
load_dotenv()

CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "")
PORT = 8889
REDIRECT_URI = f"http://localhost:{PORT}/callback"

# Scope required by main.py's twitch_get_followed_channels endpoint
SCOPES = "user:read:follows"

if not CLIENT_ID or not CLIENT_SECRET:
    print("Error: TWITCH_CLIENT_ID or TWITCH_CLIENT_SECRET is missing from your .env file!")
    sys.exit(1)

token_data = {}

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global token_data
        parsed_url = urlparse(self.path)
        
        if parsed_url.path == "/callback":
            query_params = parse_qs(parsed_url.query)
            code = query_params.get("code")
            
            if code:
                # Exchange the temporary authorization code for real access & refresh tokens
                print("\n[Twitch] Authorization code captured. Exchanging for tokens...")
                try:
                    res = requests.post(
                        "https://id.twitch.tv/oauth2/token",
                        data={
                            "client_id": CLIENT_ID,
                            "client_secret": CLIENT_SECRET,
                            "code": code[0],
                            "grant_type": "authorization_code",
                            "redirect_uri": REDIRECT_URI,
                        },
                        timeout=10
                    )
                    res.raise_for_status()
                    token_data = res.json()
                    
                    # Respond to the browser
                    self.send_response(200)
                    self.send_header("Content-type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<h1>Success!</h1><p>Tokens captured successfully. You can close this tab and return to the terminal.</p>")
                except Exception as e:
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(f"<h1>Error</h1><p>{str(e)}</p>".encode())
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"<h1>Missing code</h1>")
                
    def log_message(self, format, *args):
        # Suppress standard HTTP logging in terminal
        return

def update_env_file(user_token, refresh_token):
    env_path = ".env"
    lines = []
    
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            lines = f.readlines()
            
    # Track which keys we update
    updated_keys = {"TWITCH_USER_TOKEN": False, "TWITCH_REFRESH_TOKEN": False}
    
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("TWITCH_USER_TOKEN="):
            new_lines.append(f"TWITCH_USER_TOKEN={user_token}\n")
            updated_keys["TWITCH_USER_TOKEN"] = True
        elif stripped.startswith("TWITCH_REFRESH_TOKEN="):
            new_lines.append(f"TWITCH_REFRESH_TOKEN={refresh_token}\n")
            updated_keys["TWITCH_REFRESH_TOKEN"] = True
        else:
            new_lines.append(line)
            
    # If the keys didn't exist in the file, append them to the end
    for key, updated in updated_keys.items():
        if not updated:
            # Add a trailing newline if file doesn't end with one
            if new_lines and not new_lines[-1].endswith("\n"):
                new_lines.append("\n")
            val = user_token if key == "TWITCH_USER_TOKEN" else refresh_token
            new_lines.append(f"{key}={val}\n")
            
    with open(env_path, "w") as f:
        f.writelines(new_lines)
    print("[System] .env file successfully updated with tokens!")

def main():
    # Construct Twitch OAuth Authorize Link
    auth_url = (
        f"https://id.twitch.tv/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={SCOPES}"
    )
    
    server = HTTPServer(("localhost", PORT), OAuthCallbackHandler)
    print(f"======================================================")
    print(f"Starting temporary server on http://localhost:{PORT}")
    print(f"Opening your browser to authorize your Twitch account...")
    print(f"======================================================")
    
    # Open default browser automatically
    webbrowser.open(auth_url)
    
    # Block and wait for a single callback request
    server.handle_request()
    server.server_close()
    
    if token_data and "access_token" in token_data:
        user_token = token_data["access_token"]
        refresh_token = token_data["refresh_token"]
        
        print("\nTokens obtained:")
        print(f"  Access Token:  {user_token[:10]}...")
        print(f"  Refresh Token: {refresh_token[:10]}...")
        
        update_env_file(user_token, refresh_token)
    else:
        print("\nFailed to obtain tokens from Twitch.")

if __name__ == "__main__":
    main()
