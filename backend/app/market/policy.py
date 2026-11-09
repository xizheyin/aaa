"""主板、交易日和时间窗口检查；只输出诊断，不产生研究候选。"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from .types import InvalidData, QuoteBatch, SecurityId, SHANGHAI


@dataclass(frozen=True)
class Instrument:
    security: SecurityId
    name: str
    board: str
    instrument_type: str
    is_st: bool | None
    suspended: bool | None
    metadata_at: datetime
    metadata_source: str


def filter_main_board(item:Instrument,as_of:datetime):
    reasons=[]
    if item.security.exchange not in {'SH','SZ'} or item.board!='MAIN' or item.instrument_type!='A':reasons.append('非沪深主板A股')
    prefixes=('600','601','603','605') if item.security.exchange=='SH' else ('000','001','002','003')
    if not item.security.code.startswith(prefixes):reasons.append('代码范围与主板身份不一致')
    if item.is_st is not False:reasons.append('ST状态不合格或未知')
    if item.suspended is not False:reasons.append('停牌状态不合格或未知')
    if re.search(r'ST|退',item.name,re.IGNORECASE):reasons.append('名称提示ST或退市风险')
    if item.metadata_at.utcoffset() is None or as_of.utcoffset() is None:raise InvalidData('证券状态时间缺少时区')
    age=(as_of-item.metadata_at).total_seconds()
    if not item.metadata_source or age<0 or age>86400:reasons.append('证券状态来源缺失、过期或来自未来')
    return reasons


@dataclass(frozen=True)
class TradingCalendar:
    sessions:frozenset[date]
    valid_from:date
    valid_until:date
    source:str
    captured_at:datetime

    def is_session(self,day:date):
        if not self.source or not self.valid_from<=day<=self.valid_until:raise InvalidData('交易日历未覆盖目标日期，不能按周一至周五猜测')
        return day in self.sessions


def target_time(day:date,slot:str):
    if slot not in {'14:50','14:55'}:raise InvalidData('目标时间仅支持北京时间14:50或14:55')
    hour,minute=map(int,slot.split(':'))
    return datetime(day.year,day.month,day.day,hour,minute,tzinfo=SHANGHAI)


def time_gate(now:datetime,target:datetime,calendar:TradingCalendar,late_seconds=45):
    if now.utcoffset() is None or target.utcoffset() is None:raise InvalidData('时间必须包含时区')
    if now.astimezone(SHANGHAI).date()!=target.astimezone(SHANGHAI).date():return 'wrong_session_date'
    if calendar.captured_at.utcoffset() is None or calendar.captured_at>now:raise InvalidData('日历采集时间无时区或来自未来')
    if not calendar.is_session(target.date()):return 'closed'
    if now<target:return 'too_early'
    if now>target+timedelta(seconds=late_seconds):return 'missed_window'
    if now.astimezone(SHANGHAI).time()>=datetime.strptime('14:57','%H:%M').time():return 'closing_auction'
    return 'allowed'


def inspect_batch(batch:QuoteBatch,expected:set[str],target:datetime,tolerance_seconds=30):
    reasons=[]
    if set(batch.requested)!=expected:reasons.append('请求集合不等于本次证券主数据过滤后的集合')
    if not batch.complete:reasons.append('报价未完整返回或存在请求错误')
    if batch.source.contract_verified is not True:reasons.append('供应商格式、单位和时间语义未验收')
    if batch.finished_at>target+timedelta(seconds=45):reasons.append('采集结束已错过允许窗口')
    for quote in batch.quotes:
        label=quote.security.symbol
        if quote.quote_at is None:reasons.append(label+':缺行情时间');continue
        if quote.quote_at.astimezone(SHANGHAI).date()!=target.date():reasons.append(label+':行情非目标交易日')
        if abs((quote.quote_at-target).total_seconds())>tolerance_seconds:reasons.append(label+':行情时间偏离目标窗口')
        if quote.quote_at>quote.collected_at+timedelta(seconds=3):reasons.append(label+':行情时间来自未来')
        if quote.quote_at.astimezone(SHANGHAI).time()>=datetime.strptime('14:57','%H:%M').time():reasons.append(label+':收盘集合竞价数据不可冒充尾盘目标快照')
        if quote.price is None or quote.price<=0:reasons.append(label+':价格无效')
        if quote.volume_shares is None or quote.amount_cny is None:reasons.append(label+':成交量额或单位未验证')
        if quote.quality_flags:reasons.append(label+':存在数据质量标记')
    return reasons
