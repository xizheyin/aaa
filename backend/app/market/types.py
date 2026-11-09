from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
import re
from typing import Protocol, Sequence, TYPE_CHECKING
if TYPE_CHECKING:
    from .pagination import Page
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo('Asia/Shanghai')


class MarketError(Exception):
    code = 'market_error'


class ProviderUnavailable(MarketError):
    code = 'provider_unavailable'


class CircuitOpen(MarketError):
    code = 'circuit_open'


class IncompleteData(MarketError):
    code = 'incomplete_data'


class InvalidData(MarketError):
    code = 'invalid_data'


class UsagePermission(str, Enum):
    UNKNOWN = 'unknown'
    PRIVATE_RESEARCH = 'private_research_only'
    PUBLIC_DISPLAY = 'public_display_allowed'


@dataclass(frozen=True)
class SecurityId:
    exchange: str
    code: str

    def __post_init__(self):
        if self.exchange not in {'SH', 'SZ', 'BJ'} or not re.fullmatch(r'\d{6}', self.code):
            raise InvalidData('证券标识必须包含明确交易所和六位代码')

    @property
    def symbol(self):
        return self.exchange.lower() + self.code


@dataclass(frozen=True)
class SourceInfo:
    provider: str
    capabilities: tuple[str, ...]
    usage_permission: UsagePermission = UsagePermission.UNKNOWN
    license_url: str | None = None
    contract_verified: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Quote:
    security: SecurityId
    name: str
    price: float | None
    previous_close: float | None
    open: float | None
    high: float | None
    low: float | None
    change_pct: float | None
    volume_shares: float | None
    amount_cny: float | None
    turnover_pct: float | None
    volume_ratio: float | None
    limit_up: float | None
    limit_down: float | None
    quote_at: datetime | None
    collected_at: datetime
    provider: str
    quality_flags: tuple[str, ...] = ()
    raw_metrics: dict = field(default_factory=dict)
    industry: str | None = None
    themes: tuple[str, ...] = ()
    is_st: bool | None = None
    suspended: bool | None = None

    def __post_init__(self):
        for time_value in (self.quote_at, self.collected_at):
            if time_value is not None and time_value.utcoffset() is None:
                raise InvalidData('时间必须包含时区，不能猜测行情日期或时间')
        for name in ('price','previous_close','open','high','low','change_pct','volume_shares','amount_cny','turnover_pct','volume_ratio','limit_up','limit_down'):
            value=getattr(self,name)
            if value is not None and (isinstance(value,bool) or not math.isfinite(value)):
                raise InvalidData(f'{name}不是有限数值')

    def freshness(self, now: datetime, fresh_seconds=30):
        if now.utcoffset() is None:
            raise InvalidData('当前时间必须有时区')
        if self.quote_at is None:
            return 'unknown'
        age=(now-self.quote_at).total_seconds()
        if age < -3:
            return 'unavailable'
        if self.quote_at.astimezone(SHANGHAI).date()!=now.astimezone(SHANGHAI).date():
            return 'historical'
        return 'fresh_timestamp' if age<=fresh_seconds else 'delayed'


@dataclass(frozen=True)
class QuoteBatch:
    source: SourceInfo
    requested: tuple[str, ...]
    quotes: tuple[Quote, ...]
    missing: tuple[str, ...]
    errors: tuple[str, ...]
    started_at: datetime
    finished_at: datetime
    scope: str = 'requested_symbols_only'

    @property
    def complete(self):
        return not self.missing and not self.errors and len(self.quotes)==len(self.requested)


class QuoteProvider(Protocol):
    source: SourceInfo
    def fetch_quotes(self, securities: Sequence[SecurityId], deadline: datetime | None = None) -> QuoteBatch: ...


class BoardPageProvider(Protocol):
    source: SourceInfo
    def board_page(self, kind: str, cursor: str | None) -> 'Page': ...


def utc_now():
    return datetime.now(timezone.utc)
