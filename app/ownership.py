from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ChannelOwnership:
    name: str
    producer_owner: str
    delivery_owner: str
    publication_owner: str
    handoff_status: str
    allowed_stages: tuple[str, ...]
    raw: dict[str, Any]

    def assert_stage(self, stage: str, owner: str) -> None:
        if stage not in self.allowed_stages:
            raise RuntimeError(f'Ownership contract rejects stage {stage!r} for {self.name}')
        expected = self.publication_owner if stage == 'USER_REVIEW' else self.producer_owner
        if self.name == 'naver_blog' and stage == 'DRAFT_VERIFIED':
            expected = self.delivery_owner
        if owner != expected:
            raise RuntimeError(
                f'Ownership contract rejects owner {owner!r} for {self.name}:{stage}; '
                f'expected {expected!r}'
            )


@dataclass(frozen=True)
class OperatingContract:
    contract_id: str
    version: int
    channels: dict[str, ChannelOwnership]
    raw: dict[str, Any]

    def channel(self, name: str) -> ChannelOwnership:
        try:
            return self.channels[name]
        except KeyError as exc:
            raise RuntimeError(f'Operating contract has no channel {name!r}') from exc

    def receipt(
        self,
        channel: str,
        *,
        document_id: str,
        source_version: str,
        stage: str,
        owner: str = 'github_actions',
    ) -> dict[str, str]:
        cfg = self.channel(channel)
        cfg.assert_stage(stage, owner)
        if not document_id or not source_version:
            raise RuntimeError('Ownership receipt requires document ID and source version')
        return {
            'contract_id': self.contract_id,
            'channel': channel,
            'document_id': document_id,
            'source_version': source_version,
            'stage': stage,
            'owner': owner,
            'producer_owner': cfg.producer_owner,
            'delivery_owner': cfg.delivery_owner,
            'publication_owner': cfg.publication_owner,
        }


def load_operating_contract(path: str | Path = 'config/operating_contract.yaml') -> OperatingContract:
    source = Path(path)
    data = yaml.safe_load(source.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('contract_id') != 'HAMZZU_OPERATING_CONTRACT_V1':
        raise RuntimeError('Operating contract ID is missing or unsupported')
    environments = data.get('environments') or {}
    required_environments = {'github_actions', 'notion', 'codex_automation_3', 'user'}
    if set(environments) != required_environments:
        raise RuntimeError('Operating contract environments are incomplete')
    channels: dict[str, ChannelOwnership] = {}
    for name, raw in (data.get('channels') or {}).items():
        required = {'producer_owner', 'delivery_owner', 'publication_owner', 'handoff_status', 'allowed_stages'}
        if not required.issubset(raw):
            raise RuntimeError(f'Operating contract channel {name!r} is incomplete')
        owners = {raw['producer_owner'], raw['delivery_owner'], raw['publication_owner']}
        if not owners.issubset(environments):
            raise RuntimeError(f'Operating contract channel {name!r} references an unknown owner')
        channels[name] = ChannelOwnership(
            name=name,
            producer_owner=str(raw['producer_owner']),
            delivery_owner=str(raw['delivery_owner']),
            publication_owner=str(raw['publication_owner']),
            handoff_status=str(raw['handoff_status']),
            allowed_stages=tuple(str(x) for x in raw['allowed_stages']),
            raw=raw,
        )
    if set(channels) != {'naver_blog', 'ppojjugi_shorts', 'japan_shorts'}:
        raise RuntimeError('Operating contract must define exactly the three production channels')
    schedules = data.get('schedules') or {}
    github_crons = [item.get('cron') for item in schedules.get('github_actions', [])]
    if github_crons != ['0 1 * * *', '0 12 * * *']:
        raise RuntimeError('Operating contract contains an unexpected GitHub schedule')
    return OperatingContract(
        contract_id=str(data['contract_id']),
        version=int(data.get('version') or 0),
        channels=channels,
        raw=data,
    )
