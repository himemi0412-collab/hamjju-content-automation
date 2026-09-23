from __future__ import annotations
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.readonly',
]


class YouTubePrivateUploader:
    """YouTube uploader requiring an explicit reviewed privacy value."""

    def __init__(self, client_secrets: Path, token_file: Path):
        self.client_secrets = client_secrets
        self.token_file = token_file

    def authorize_interactively(self) -> None:
        flow = InstalledAppFlow.from_client_secrets_file(str(self.client_secrets), SCOPES)
        creds = flow.run_local_server(port=0)
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(creds.to_json(), encoding='utf-8')

    def _credentials(self) -> Credentials:
        if not self.token_file.exists():
            raise RuntimeError('YouTube token file missing. Run setup-youtube-auth once on a device with a browser.')
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

    def verify_uploaded(self, url: str, privacy_status: str) -> bool:
        """Confirm the video exists under these credentials with the expected privacy."""
        parsed = urlparse(url)
        video_ids = parse_qs(parsed.query).get('v', [])
        if (parsed.scheme != 'https' or parsed.netloc != 'www.youtube.com'
                or parsed.path != '/watch' or len(video_ids) != 1
                or re.fullmatch(r'[A-Za-z0-9_-]{11}', video_ids[0]) is None
                or privacy_status not in {'private', 'public'}):
            raise ValueError('Invalid reviewed YouTube video identity')
        youtube = build('youtube', 'v3', credentials=self._credentials(), cache_discovery=False)
        response = youtube.videos().list(part='status', id=video_ids[0]).execute()
        items = response.get('items') or []
        return len(items) == 1 and items[0].get('id') == video_ids[0] and (
            items[0].get('status') or {}).get('privacyStatus') == privacy_status

    def upload_private(self, video: Path, title: str, description: str = '', tags: list[str] | None = None) -> str:
        return self.upload_reviewed(video, title, description, tags, privacy_status='private')

    def upload_reviewed(
        self, video: Path, title: str, description: str = '',
        tags: list[str] | None = None, privacy_status: str = 'private',
    ) -> str:
        if privacy_status not in {'private', 'public'}:
            raise ValueError('YouTube privacy must be private or public')
        youtube = build('youtube', 'v3', credentials=self._credentials(), cache_discovery=False)
        body = {
            'snippet': {
                'title': title[:100],
                'description': description[:5000],
                'tags': (tags or [])[:50],
                'categoryId': '22',
            },
            'status': {'privacyStatus': privacy_status},
        }
        request = youtube.videos().insert(
            part='snippet,status',
            body=body,
            media_body=MediaFileUpload(str(video), chunksize=-1, resumable=True),
        )
        response = None
        while response is None:
            _, response = request.next_chunk()
        return f"https://www.youtube.com/watch?v={response['id']}"
