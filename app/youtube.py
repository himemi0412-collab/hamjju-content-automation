from __future__ import annotations
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ['https://www.googleapis.com/auth/youtube.upload']


class YouTubePrivateUploader:
    """YouTube uploader with a hard safety invariant: privacy is always private."""

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

    def upload_private(self, video: Path, title: str, description: str = '', tags: list[str] | None = None) -> str:
        youtube = build('youtube', 'v3', credentials=self._credentials(), cache_discovery=False)
        body = {
            'snippet': {
                'title': title[:100],
                'description': description[:5000],
                'tags': (tags or [])[:50],
                'categoryId': '22',
            },
            'status': {'privacyStatus': 'private'},
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
