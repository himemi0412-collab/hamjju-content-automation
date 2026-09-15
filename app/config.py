from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import yaml


@dataclass(frozen=True)
class ChannelConfig:
    name: str
    source: str
    notion_channel_value: str | None
    ready_status: str
    processing_status: str
    success_status: str
    revision_status: str
    content_kind: str
    prompt_file: str
    media_generation: bool = False
    publish_policy: str | None = None
    youtube_privacy: str | None = None


def load_channels(path: str | Path = 'config/channels.yaml') -> dict[str, ChannelConfig]:
    data = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    out: dict[str, ChannelConfig] = {}
    for name, raw in data['channels'].items():
        out[name] = ChannelConfig(name=name, **raw)
    return out
