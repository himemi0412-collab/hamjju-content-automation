from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    openai_api_key: str = ''
    notion_access_token: str = ''
    blog_data_source_id: str = '21a60556-f086-4b7a-96b3-81995c77edef'
    shorts_data_source_id: str = 'e7ad07b7-0652-4747-956d-e7b071d4bde9'

    text_model: str = 'gpt-5.6-luna'
    qa_model: str = 'gpt-5.6-luna'
    image_model: str = 'gpt-image-2'
    image_quality: str = 'medium'
    tts_model: str = 'gpt-4o-mini-tts'
    tts_voice: str = 'coral'

    openai_monthly_budget_usd: float = 22.0
    openai_budget_ledger: Path = Path('output/openai-cost-ledger.json')
    openai_budget_baseline_month: str = ''
    openai_budget_baseline_usd: float = 0.0
    openai_budget_require_existing_ledger: bool = False
    openai_image_estimated_cost_usd: float = 0.05
    openai_tts_estimated_cost_usd: float = 0.03
    openai_text_reserve_usd: float = 0.04
    openai_web_text_reserve_usd: float = 0.08
    max_generation_output_tokens: int = 12000
    max_qa_output_tokens: int = 6000

    max_jobs_per_run: int = 3
    enable_web_research: bool = True
    enable_media_generation: bool = False
    auto_private_youtube_upload: bool = False

    youtube_client_secrets_file: Path = Path('secrets/youtube_client_secret.json')
    youtube_token_file: Path = Path('secrets/youtube_token.json')
    youtube_ppojjugi_token_file: Path = Path('secrets/youtube_ppojjugi_token.json')
    youtube_japan_token_file: Path = Path('secrets/youtube_japan_token.json')
    youtube_ppojjugi_channel_id: str = 'UCjCEzw6WQmQRZeOV-O8cYpg'
    youtube_japan_channel_id: str = 'UCXezqjpy6AgXEynvTxSjzmg'

    state_db: Path = Path('output/state.db')
    output_dir: Path = Path('output')
    card_font_path: str | None = None
    log_level: str = 'INFO'

    @property
    def notion_ready(self) -> bool:
        return bool(self.notion_access_token)

    @property
    def openai_ready(self) -> bool:
        return bool(self.openai_api_key)
