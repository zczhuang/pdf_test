"""
One-time script to get a Google OAuth2 refresh token for personal app access.

Examples:
  python get_refresh_token.py --mode drive
  python get_refresh_token.py --mode photos
"""

import argparse
import json
from google_auth_oauthlib.flow import InstalledAppFlow

MODES = {
    "drive": {
        "title": "Google Drive OAuth2 — Get Refresh Token",
        "scope": ["https://www.googleapis.com/auth/drive.file"],
        "env_name": "GOOGLE_REFRESH_TOKEN",
        "secret_name": "journal-google-refresh-token",
    },
    "photos": {
        "title": "Google Photos Picker OAuth2 — Get Refresh Token",
        "scope": ["https://www.googleapis.com/auth/photospicker.mediaitems.readonly"],
        "env_name": "GOOGLE_PHOTOS_REFRESH_TOKEN",
        "secret_name": "journal-google-photos-refresh-token",
    },
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=sorted(MODES.keys()), default="drive")
    args = parser.parse_args()
    mode = MODES[args.mode]

    print("=" * 60)
    print(f"  {mode['title']}")
    print("=" * 60)
    print()
    print("Before running this, you need an OAuth2 Client ID:")
    print("  1. Go to console.cloud.google.com/apis/credentials")
    print("  2. Click '+ CREATE CREDENTIALS' → 'OAuth client ID'")
    print("  3. Application type: 'Desktop app'")
    print("  4. Download the JSON file")
    print()

    creds_file = input("Path to your OAuth client JSON file: ").strip()
    if not creds_file:
        print("No file provided. Exiting.")
        return

    flow = InstalledAppFlow.from_client_secrets_file(creds_file, mode["scope"])
    creds = flow.run_local_server(port=0)

    # Read client ID and secret from the downloaded file
    with open(creds_file) as f:
        client_config = json.load(f)
    installed = client_config.get("installed", client_config.get("web", {}))

    print()
    print("=" * 60)
    print("  SUCCESS! Save these values as secrets:")
    print("=" * 60)
    print()
    print(f"GOOGLE_CLIENT_ID={installed['client_id']}")
    print(f"GOOGLE_CLIENT_SECRET={installed['client_secret']}")
    print(f"{mode['env_name']}={creds.refresh_token}")
    print()
    print("Store them in GCP Secret Manager with these commands:")
    print()
    print(f'echo -n "{installed["client_id"]}" | gcloud secrets create journal-google-client-id --data-file=- --replication-policy=automatic')
    print(f'echo -n "{installed["client_secret"]}" | gcloud secrets create journal-google-client-secret --data-file=- --replication-policy=automatic')
    print(f'echo -n "{creds.refresh_token}" | gcloud secrets create {mode["secret_name"]} --data-file=- --replication-policy=automatic')


if __name__ == "__main__":
    main()
