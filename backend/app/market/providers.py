"""不依赖AKShare的按代码文本报价适配器；格式和授权均未被视为官方SLA。"""
from datetime import datetime
import math
import re
from typing import Sequence

from .transport import HttpTransport
from .types import (InvalidData, MarketError, Quote, QuoteBatch, QuoteProvider,
                    SecurityId, SHANGHAI, SourceInfo, utc_now)


def number(value):
    try:
        parsed=float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError,ValueError):return None


def quote_time(value, fmt):
    try:return datetime.strptime(value,fmt).replace(tzinfo=SHANGHAI)
    except (ValueError,TypeError):return None


def make_quote(security,name,provider,collected_at,**values):
    fields={k:None for k in ['price','previous_close','open','high','low','change_pct','volume_shares','amount_cny','turnover_pct','volume_ratio','limit_up','limit_down','quote_at']}
    fields.update(values)
    return Quote(security=security,name=name,provider=provider,collected_at=collected_at,**fields)


def parse_tencent(text, collected_at):
    quotes=[]
    for prefix,code,value in re.findall(r'v_(sh|sz|bj)(\d{6})="([^"\r\n]*)";',text):
        fields=value.split('~')
        if len(fields)<35:raise InvalidData('腾讯报价字段不足35项，未猜测结构')
        if fields[2]!=code:raise InvalidData('腾讯返回代码与响应标识不一致')
        at=quote_time(fields[30],'%Y%m%d%H%M%S')
        raw={name:number(fields[index]) for name,index in [('volume_field_6',6),('amount_field_37',37),('turnover_field_38',38),('volume_ratio_field_49',49)] if len(fields)>index}
        # 未核验单位的字段不提升为标准成交量/额；保留原始位置用于后续交叉验收。
        quotes.append(make_quote(SecurityId(prefix.upper(),code),fields[1],'tencent-text',collected_at,
            price=number(fields[3]),previous_close=number(fields[4]),open=number(fields[5]),
            high=number(fields[33]),low=number(fields[34]),change_pct=number(fields[32]),quote_at=at,
            quality_flags=('unofficial_schema','volume_amount_units_unverified','trade_state_unverified')+(() if at else ('quote_time_missing',)),raw_metrics=raw))
    return quotes


def parse_sina(text,collected_at):
    quotes=[]
    for prefix,code,value in re.findall(r'(?:var\s+)?hq_str_(sh|sz|bj)(\d{6})="([^"\r\n]*)";',text):
        fields=value.split(',')
        if len(fields)<32:raise InvalidData('新浪报价字段不足32项，未猜测结构')
        at=quote_time(fields[30]+' '+fields[31],'%Y-%m-%d %H:%M:%S')
        price,previous=number(fields[3]),number(fields[2])
        change=(price/previous-1)*100 if price is not None and previous is not None and previous>0 else None
        quotes.append(make_quote(SecurityId(prefix.upper(),code),fields[0],'sina-text',collected_at,
            price=price,previous_close=previous,open=number(fields[1]),high=number(fields[4]),low=number(fields[5]),change_pct=change,quote_at=at,
            quality_flags=('unofficial_schema','volume_amount_units_unverified','turnover_volume_ratio_missing','trade_state_unverified')+(() if at else ('quote_time_missing',)),
            raw_metrics={'volume_field_8':number(fields[8]),'amount_field_9':number(fields[9])}))
    return quotes


class TextQuoteProvider:
    def __init__(self,transport:HttpTransport,source:SourceInfo,base_url,parser,headers=None,chunk_size=40,max_batches=100,clock=utc_now):
        if not 1<=chunk_size<=50 or max_batches<1:raise ValueError('分批参数超出范围')
        self.transport=transport;self.source=source;self.base_url=base_url;self.parser=parser
        self.headers=headers or {};self.chunk_size=chunk_size;self.max_batches=max_batches;self.clock=clock

    def fetch_quotes(self,securities:Sequence[SecurityId],deadline=None)->QuoteBatch:
        requested=tuple(item.symbol for item in securities)
        if len(set(requested))!=len(requested):raise InvalidData('请求证券重复，无法证明覆盖范围')
        if not requested:raise InvalidData('必须提供证券主数据中的明确代码')
        start=self.clock();quotes={};errors=[]
        for batch_no,offset in enumerate(range(0,len(requested),self.chunk_size)):
            if deadline is not None and self.clock()>deadline:
                errors.append('missed_window：停止继续请求晚到数据');break
            if batch_no>=self.max_batches:
                errors.append('request_budget_exhausted：分批预算不足，未认定完整覆盖');break
            try:
                response=self.transport.get(self.base_url+','.join(requested[offset:offset+self.chunk_size]),headers=self.headers)
                text=response.body.decode('gb18030',errors='strict')
                parsed=self.parser(text,response.collected_at)
                for quote in parsed:
                    key=quote.security.symbol
                    if key not in requested:raise InvalidData('返回了未请求的证券')
                    if key in quotes:raise InvalidData('响应含重复证券，不能静默去重')
                    quotes[key]=quote
            except (MarketError,UnicodeError) as exc:
                errors.append(f'{type(exc).__name__}: {exc}');break
        missing=tuple(key for key in requested if key not in quotes)
        return QuoteBatch(self.source,requested,tuple(quotes.values()),missing,tuple(errors),start,self.clock())


class TencentProvider(TextQuoteProvider):
    def __init__(self,transport,**kwargs):
        super().__init__(transport,SourceInfo('tencent-text',('quotes_by_symbol',),license_url='https://www.qq.com/contract.shtml',notes=('直接文本报价，非官方开发者服务承诺','不提供全市场证券清单','公开展示授权未确认')),'https://qt.gtimg.cn/q=',parse_tencent,**kwargs)


class SinaProvider(TextQuoteProvider):
    def __init__(self,transport,**kwargs):
        super().__init__(transport,SourceInfo('sina-text',('quotes_by_symbol',),license_url='https://www.sina.com.cn/intro/copyright.shtml',notes=('直接文本报价，非官方开发者服务承诺','缺换手率和量比','公开展示授权未确认')),'https://hq.sinajs.cn/list=',parse_sina,headers={'Referer':'https://finance.sina.com.cn/'},**kwargs)


class ProviderRegistry:
    def __init__(self):self.providers={}
    def register(self,provider:QuoteProvider):
        if provider.source.provider in self.providers:raise InvalidData('数据供应商重复注册')
        self.providers[provider.source.provider]=provider
    def select(self,name,required=()):
        if name not in self.providers:raise InvalidData('没有配置该供应商；不会自动改用其他数据')
        provider=self.providers[name]
        if not set(required).issubset(provider.source.capabilities):raise InvalidData('供应商不具备所需数据能力')
        return provider
