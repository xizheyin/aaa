#!/usr/bin/env python3
"""准备真实交易所日历与证券身份输入；不补造ST、停牌状态，不请求行情。"""
from datetime import date,datetime,timedelta,timezone
from io import BytesIO
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from app.market.transport import CircuitRegistry,HttpTransport

NOTICE_SSE='https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml'
NOTICE_SZSE='https://www.szse.cn/disclosure/notice/general/t20251222_618087.html'
# 逐条核对上述交易所2026公告后的闭市区间；不用于2027年。
CLOSURES=[('2026-01-01','2026-01-03'),('2026-02-15','2026-02-23'),('2026-04-04','2026-04-06'),('2026-05-01','2026-05-05'),('2026-06-19','2026-06-21'),('2026-09-25','2026-09-27'),('2026-10-01','2026-10-07')]

def build_calendar(captured_at):
    closed=set()
    for first,last in CLOSURES:
        day=date.fromisoformat(first)
        while day<=date.fromisoformat(last):closed.add(day);day+=timedelta(days=1)
    days=[];day=date(2026,1,1)
    while day<=date(2026,12,31):
        if day.weekday()<5 and day not in closed:days.append(str(day))
        day+=timedelta(days=1)
    return {'sessions':days,'valid_from':'2026-01-01','valid_until':'2026-12-31','source':NOTICE_SSE+' ; '+NOTICE_SZSE,'captured_at':captured_at,'synthetic':False,'method':'交易所2026休市公告逐条人工核对；排除周末及公告节假日','notice_checked_at':'2026-11-09','limitations':['年度公告不代替后续临时停市公告；超出2026覆盖时拒绝使用']}


def identity(exchange,code,name,when,source,industry=None):
    return {'exchange':exchange,'code':str(code).zfill(6),'name':str(name),'board':'MAIN','instrument_type':'A','is_st':True if 'ST' in str(name).upper() else None,'suspended':None,'metadata_at':when,'identity_source':source,'industry':industry,'status_note':'名称中的ST仅作排除线索；无ST字样不能证明非ST，停牌状态未提供'}


def prepare():
    dest=ROOT/'research/inputs';dest.mkdir(exist_ok=True)
    now=datetime.now(timezone.utc).isoformat()
    (dest/'exchange-calendar-2026.json').write_text(json.dumps(build_calendar(now),ensure_ascii=False,indent=2)+'\n')
    client=HttpTransport(circuit=CircuitRegistry(Path('/tmp/ashare-v2-reference-circuits')),allowed_hosts={'query.sse.com.cn','www.szse.cn'},attempts=1,timeout=12)
    attempts=[];records=[]
    params={'STOCK_TYPE':'1','sqlId':'COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L','COMPANY_STATUS':'2,4,5,7,8','type':'inParams','isPagination':'true','pageHelp.pageSize':'10000','pageHelp.pageNo':'1','pageHelp.beginPage':'1','pageHelp.endPage':'1','pageHelp.cacheSize':'1'}
    sse='https://query.sse.com.cn/sseQuery/commonQuery.do?'+urlencode(params)
    szse='https://www.szse.cn/api/report/ShowReport?'+urlencode({'SHOWTYPE':'xlsx','CATALOGID':'1110','TABKEY':'tab1'})
    for exchange,url in [('SH',sse),('SZ',szse)]:
        entry={'exchange':exchange,'requested_at':datetime.now(timezone.utc).isoformat(),'status':'failed','attempts':1}
        try:
            response=client.get(url,headers={'Referer':'https://www.sse.com.cn/assortment/stock/list/share/'} if exchange=='SH' else None)
            entry.update(http_status=response.status,response_sha256=hashlib.sha256(response.body).hexdigest(),collected_at=response.collected_at.isoformat())
            source='https://www.sse.com.cn/assortment/stock/list/share/' if exchange=='SH' else 'https://www.szse.cn/market/product/stock/list/index.html'
            selected=[]
            if exchange=='SH':
                payload=json.loads(response.body)
                rows=payload['result'];declared=int(payload['pageHelp']['total'])
                if declared!=len(rows):raise ValueError('上交所返回条数不等于声明总数，未认定完整')
                for row in rows:
                    code=str(row['A_STOCK_CODE'])
                    if not code.startswith(('600','601','603','605')):raise ValueError('上交所主板列表含异常代码')
                    selected.append(identity('SH',code,row['SEC_NAME_CN'],entry['collected_at'],source))
                entry['source_rows']=len(rows)
            else:
                import openpyxl
                worksheet=openpyxl.load_workbook(BytesIO(response.body),read_only=True,data_only=True).active
                rows=iter(worksheet.iter_rows(values_only=True));header=list(next(rows))
                if not {'板块','A股代码','A股简称'}.issubset(header):raise ValueError('深交所表头不符')
                source_rows=0
                for values in rows:
                    row=dict(zip(header,values))
                    if row.get('A股代码') is None:continue
                    source_rows+=1
                    if str(row['板块']).strip()!='主板':continue
                    value=row['A股代码'];code=str(int(value)).zfill(6) if isinstance(value,(int,float)) else str(value).strip().zfill(6)
                    if not code.startswith(('000','001','002','003')):raise ValueError('深交所主板列表含异常代码')
                    selected.append(identity('SZ',code,row['A股简称'],entry['collected_at'],source,row.get('所属行业')))
                entry['source_rows']=source_rows
            if not selected or len({r['code'] for r in selected})!=len(selected):raise ValueError('空表或重复证券代码')
            records.extend(selected);entry.update(status='identity_rows_returned',mainboard_identity_rows=len(selected),st_status_verified=False,suspension_status_verified=False)
        except Exception as exc:entry.update(error_type=type(exc).__name__,error=str(exc)[:400])
        attempts.append(entry);print(exchange,entry['status'],entry.get('mainboard_identity_rows'),entry.get('error',''),flush=True)
    result={'source':'交易所股票列表，见identity_source；仅验证身份，不验证实时状态','expected_count':len(records),'instruments':records,'synthetic':False,'prepared_at':now,'identity_coverage_complete':len(records)>0 and all(e['status']=='identity_rows_returned' for e in attempts),'trade_state_coverage_complete':False,'evidence':attempts}
    private=ROOT/'research/private_inputs';private.mkdir(exist_ok=True)
    (private/'exchange-mainboard-identities.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    (ROOT/'research/evidence/reference-preparation.json').write_text(json.dumps({'prepared_at':now,'calendar_sources':[NOTICE_SSE,NOTICE_SZSE],'next_session':'2026-11-10','attempts':attempts,'instrument_count':len(records),'identity_coverage_complete':result['identity_coverage_complete'],'trade_state_coverage_complete':False},ensure_ascii=False,indent=2)+'\n')
    print('已准备交易所日历及身份文件；未知ST/停牌没有填写为false。',flush=True)
if __name__=='__main__':prepare()
