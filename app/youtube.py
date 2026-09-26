"""Read-only YouTube identity verification for optional account checks."""
from __future__ import annotations

from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/youtube.readonly']


class YouTubeIdentityReader:
    def __init__(self, client_secrets: Path, token_file: Path):
        self.client_secrets = client_secrets
        self.token_file = token_file

    def authorize_interactively(self) -> None:
        flow = InstalledAppFlow.from_client_secrets_file(str(self.client_secrets), SCOPES)
        creds = flow.run_local_server(
            host='127.0.0.1', bind_addr='127.0.0.1', port=8765,
            open_browser=True, timeout_seconds=300, prompt='select_account',
        )
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(creds.to_json(), encoding='utf-8')

    def _credentials(self) -> Credentials:
        if not self.token_file.exists():
            raise RuntimeError('YouTube token file missing. Run setup-youtube-auth on a device with a browser.')
        creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self.token_file.write_text(creds.to_json(), encoding='utf-8')
        if not creds.valid:
            raise RuntimeError('YouTube credentials are invalid')
        return creds

    def current_channel(self) -> dict[str, str]:
        youtube = build('youtube', 'v3', credentials=self._credentials(), cache_discovery=False)
        response = youtube.channels().list(part='snippet', mine=True).execute()
        items = response.get('items') or []
        if len(items) != 1:
            raise RuntimeError(f'Expected one authorized YouTube channel, found {len(items)}')
        item = items[0]
        return {
            'id': str(item.get('id') or ''),
            'title': str((item.get('snippet') or {}).get('title') or ''),
        }
