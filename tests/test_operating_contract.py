from pathlib import Path
import re

import pytest

from app.ownership import load_operating_contract


def test_operating_contract_fixes_one_owner_per_delivery_boundary():
    contract = load_operating_contract()

    blog = contract.channel('naver_blog')
    assert blog.producer_owner == 'github_actions'
    assert blog.delivery_owner == 'local_browser_worker'
    assert blog.publication_owner == 'user'
    assert blog.handoff_status == '네이버 저장 요청'

    for channel in ('ppojjugi_shorts', 'japan_shorts'):
        shorts = contract.channel(channel)
        assert shorts.producer_owner == 'github_actions'
        assert shorts.delivery_owner == 'github_actions'
        assert shorts.publication_owner == 'user'


def test_operating_contract_rejects_cross_environment_stage_claims():
    contract = load_operating_contract()

    receipt = contract.receipt(
        'naver_blog', document_id='page-1', source_version='sha256',
        stage='DRAFT_VERIFIED', owner='local_browser_worker',
    )
    assert receipt['owner'] == 'local_browser_worker'

    with pytest.raises(RuntimeError, match='expected'):
        contract.receipt(
            'naver_blog', document_id='page-1', source_version='sha256',
            stage='DRAFT_VERIFIED', owner='github_actions',
        )


def test_operating_contract_has_only_permanent_github_schedules():
    contract = load_operating_contract()
    crons = [item['cron'] for item in contract.raw['schedules']['github_actions']]
    workflow = Path('.github/workflows/daily.yml').read_text(encoding='utf-8')
    workflow_crons = re.findall(r"^\s+- cron: '([^']+)'$", workflow, flags=re.MULTILINE)

    assert crons == ['45 0 * * *', '0 1 * * *', '0 12 * * *']
    assert workflow_crons == crons
    assert 'HAMZZU_OPERATING_CONTRACT_V1' in Path('docs/OPERATING_CONTRACT.md').read_text(encoding='utf-8')
