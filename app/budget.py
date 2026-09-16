from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_UP
from pathlib import Path
from typing import Any
from uuid import uuid4


MONEY_QUANTUM = Decimal('0.000001')


class BudgetLimitReached(RuntimeError):
    """Raised before a paid API call when the monthly internal limit is reached."""


def _money(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_UP)


class BudgetGuard:
    """Persistent, conservative monthly cost ledger for paid OpenAI calls.

    The OpenAI project hard limit remains the authoritative final backstop. This
    ledger intentionally rounds estimates upward and is persisted between
    GitHub Actions runs so the application can stop before that hard limit.
    """

    def __init__(
        self,
        path: Path,
        monthly_limit_usd: float = 22.0,
        baseline_month: str = '',
        baseline_usd: float = 0.0,
        require_existing: bool = False,
        now: datetime | None = None,
    ):
        self.path = path
        self.monthly_limit = _money(monthly_limit_usd)
        self.now = now or datetime.now(timezone.utc)
        self.month = self.now.strftime('%Y-%m')
        self.baseline_month = baseline_month
        self.baseline_usd = _money(baseline_usd)
        self.require_existing = require_existing
        self.data = self._load()

    def _new_ledger(self) -> dict[str, Any]:
        starting = self.baseline_usd if self.baseline_month == self.month else _money(0)
        return {
            'month_utc': self.month,
            'estimated_spend_usd': str(starting),
            'monthly_limit_usd': str(self.monthly_limit),
            'events': [],
            'updated_at': self.now.isoformat(),
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            if self.require_existing and self.baseline_month != self.month:
                raise RuntimeError(
                    'OpenAI budget ledger is missing; paid API calls are blocked until '
                    'a current-month baseline is configured'
                )
            data = self._new_ledger()
            self._save(data)
            return data
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError('OpenAI budget ledger is unreadable; paid API calls are blocked') from exc
        if data.get('month_utc') != self.month:
            data = self._new_ledger()
            self._save(data)
        return data

    @property
    def spent(self) -> Decimal:
        return _money(self.data.get('estimated_spend_usd', '0'))

    @property
    def remaining(self) -> Decimal:
        return max(self.monthly_limit - self.spent, _money(0))

    def can_spend(self, amount_usd: Decimal | float | int | str) -> bool:
        amount = _money(amount_usd)
        return amount >= 0 and self.spent + amount < self.monthly_limit

    def require_below_limit(self, category: str) -> None:
        if self.spent >= self.monthly_limit:
            raise BudgetLimitReached(
                f'OpenAI monthly internal budget reached before {category}; '
                f'estimated=${self.spent}, limit=${self.monthly_limit}'
            )

    def reserve(
        self,
        category: str,
        amount_usd: Decimal | float | int | str,
        details: dict[str, Any] | None = None,
    ) -> str:
        amount = _money(amount_usd)
        projected = self.spent + amount
        if projected >= self.monthly_limit:
            raise BudgetLimitReached(
                f'OpenAI monthly internal budget would be reached by {category}; '
                f'estimated=${self.spent}, requested=${amount}, limit=${self.monthly_limit}'
            )
        event_id = uuid4().hex
        self.data['estimated_spend_usd'] = str(projected)
        self.data.setdefault('events', []).append({
            'id': event_id,
            'category': category,
            'reserved_usd': str(amount),
            'settled_usd': None,
            'status': 'reserved',
            'details': details or {},
            'created_at': datetime.now(timezone.utc).isoformat(),
        })
        self._trim_and_save()
        return event_id

    def settle(self, event_id: str, actual_usd: Decimal | float | int | str) -> None:
        actual = _money(actual_usd)
        for event in reversed(self.data.get('events', [])):
            if event.get('id') != event_id:
                continue
            reserved = _money(event.get('reserved_usd', '0'))
            adjusted = max(self.spent - reserved + actual, _money(0))
            self.data['estimated_spend_usd'] = str(adjusted)
            event['settled_usd'] = str(actual)
            event['status'] = 'settled'
            event['settled_at'] = datetime.now(timezone.utc).isoformat()
            self._trim_and_save()
            return
        raise RuntimeError('OpenAI budget reservation was not found')

    def snapshot(self) -> dict[str, Any]:
        return {
            'month_utc': self.month,
            'estimated_spend_usd': float(self.spent),
            'remaining_usd': float(self.remaining),
            'monthly_limit_usd': float(self.monthly_limit),
        }

    def _trim_and_save(self) -> None:
        self.data['events'] = self.data.get('events', [])[-500:]
        self.data['monthly_limit_usd'] = str(self.monthly_limit)
        self.data['updated_at'] = datetime.now(timezone.utc).isoformat()
        self._save(self.data)

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + '.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.path)
