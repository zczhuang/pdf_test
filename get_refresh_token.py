"""
One-time script to get a Google OAuth2 refresh token for personal Drive access.

Run this ONCE on your laptop (not on Cloud Run):
  python get_refresh_token.py

It will open a browser for you to log in with your Google account,
then print the refresh token to store as a secret.
"""

import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def main():
    print("=" * 60)
    print("  Google Drive OAuth2 — Get Refresh Token")
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

    flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
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
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print()
    print("Store them in GCP Secret Manager with these commands:")
    print()
    print(f'echo -n "{installed["client_id"]}" | gcloud secrets create journal-google-client-id --data-file=- --replication-policy=automatic')
    print(f'echo -n "{installed["client_secret"]}" | gcloud secrets create journal-google-client-secret --data-file=- --replication-policy=automatic')
    print(f'echo -n "{creds.refresh_token}" | gcloud secrets create journal-google-refresh-token --data-file=- --replication-policy=automatic')


if __name__ == "__main__":
    main()
