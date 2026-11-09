"""行业/概念统一分页器：页数/总数/ID/必填值严格检查，不保存半成品。"""
from dataclasses import dataclass
from datetime import datetime
import math
from typing import Callable
from .cache import JsonCache
from .types import IncompleteData, InvalidData


@dataclass(frozen=True)
class Page:
    items: tuple[dict,...]
    total: int
    next_cursor: str | None
    observed_at: str | None = None


@dataclass(frozen=True)
class BoardSchema:
    id_field: str = 'board_id'
    required: tuple[str,...] = ('board_id','name','change_pct','amount_cny')
    numeric: tuple[str,...] = ('change_pct','amount_cny')


def validate_board(item,schema):
    if not isinstance(item,dict):raise InvalidData('板块记录不是对象')
    for key in schema.required:
        if item.get(key) is None or item.get(key)=='':raise InvalidData(f'板块必填字段缺失：{key}')
    if not isinstance(item[schema.id_field],str) or not isinstance(item['name'],str) or not item[schema.id_field].strip() or not item['name'].strip():raise InvalidData('板块ID和名称必须为非空字符串')
    for key in schema.numeric:
        value=item.get(key)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise InvalidData(f'板块数值非法：{key}')
    if item['amount_cny']<0:raise InvalidData('板块成交额不能为负')


def fetch_complete(fetch_page:Callable[[str|None],Page],*,schema=BoardSchema(),max_pages=100,max_items=10000):
    cursor=None;seen_cursors=set();seen_ids=set();items=[];expected=None;observed=None
    for page_no in range(max_pages):
        page=fetch_page(cursor)
        if isinstance(page.total,bool) or not isinstance(page.total,int) or not 0<=page.total<=max_items:raise InvalidData('分页总数非法或超过上限')
        if expected is None:expected=page.total;observed=page.observed_at
        if page.total!=expected:raise IncompleteData('分页期间上游总数变化，不能证明完整性')
        if page.next_cursor is not None and (not isinstance(page.next_cursor,str) or not page.next_cursor.strip()):raise InvalidData('分页游标格式错误')
        if page.observed_at is not None:
            try:
                if datetime.fromisoformat(page.observed_at).utcoffset() is None:raise ValueError('no timezone')
            except (TypeError,ValueError):raise InvalidData('板块观测时间缺少时区或格式错误') from None
        if page.observed_at!=observed:raise IncompleteData('分页观测时间不一致，不能合并为同一快照')
        if not page.items and expected!=0:raise IncompleteData('非空数据集提前返回空页')
        for item in page.items:
            validate_board(item,schema)
            key=item[schema.id_field]
            if key in seen_ids:raise IncompleteData('重复板块ID或重复分页，未静默丢弃')
            seen_ids.add(key);items.append(item)
        if len(items)>expected:raise IncompleteData('返回记录超过上游声明总数')
        if page.next_cursor is None:
            if len(items)!=expected:raise IncompleteData('分页提前结束，实际条数少于总数')
            return items,observed
        if page.next_cursor in seen_cursors:raise IncompleteData('分页游标循环')
        seen_cursors.add(page.next_cursor);cursor=page.next_cursor
    raise IncompleteData('达到最大分页数，不能将部分结果当完整数据')


class BoardRepository:
    """fetch_page由有许可且可用的供应商提供；本批不请求失败的东方财富源。"""
    def __init__(self,provider,fetch_page,cache:JsonCache,ttl=300,max_pages=100):
        self.provider=provider;self.fetch_page=fetch_page;self.cache=cache;self.ttl=ttl;self.max_pages=max_pages
    def load(self,kind,force_refresh=False):
        if kind not in {'industry','concept'}:raise InvalidData('板块类别只能为行业或概念')
        return self.cache.get_or_fetch(self.provider,{'kind':kind},lambda:fetch_complete(lambda cursor:self.fetch_page(kind,cursor),max_pages=self.max_pages),ttl=self.ttl,force_refresh=force_refresh)


def unique_members(boards):
    return sorted({symbol for board in boards for symbol in board.get('members',[])})
