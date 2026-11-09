"""手动盘中数据验收CLI；不是选股任务，不等待、不伪造执行时间。"""
import argparse
from dataclasses import asdict
from datetime import date,datetime,timezone
import json
from pathlib import Path
import sys
import uuid

from .policy import Instrument,TradingCalendar,filter_main_board,target_time,time_gate,inspect_batch
from .providers import TencentProvider,SinaProvider
from .transport import CircuitRegistry,HttpTransport
from .types import InvalidData,MarketError,SecurityId,utc_now


def parse_calendar(data):
    if data.get('synthetic') is True:raise InvalidData('模拟日历不得用于联网盘中验收')
    calendar=TradingCalendar(frozenset(date.fromisoformat(day) for day in data['sessions']),date.fromisoformat(data['valid_from']),date.fromisoformat(data['valid_until']),data['source'],datetime.fromisoformat(data['captured_at']))
    if any(not calendar.valid_from<=day<=calendar.valid_until for day in calendar.sessions):raise InvalidData('日历日期超出声明覆盖范围')
    return calendar


def parse_catalog(data):
    if data.get('synthetic') is True:raise InvalidData('模拟证券清单不得用于联网盘中验收')
    if not data.get('source') or data.get('expected_count')!=len(data['instruments']):raise InvalidData('证券清单来源缺失或数量与声明不符')
    items=[]
    for row in data['instruments']:
        if any(row.get(key) is not None and type(row[key]) is not bool for key in ['is_st','suspended']):raise InvalidData('ST和停牌状态必须为布尔或null，不能用字符串代替')
        items.append(Instrument(SecurityId(row['exchange'],row['code']),row['name'],row['board'],row['instrument_type'],row.get('is_st'),row.get('suspended'),datetime.fromisoformat(row['metadata_at']),data['source']))
    if len({i.security.symbol for i in items})!=len(items):raise InvalidData('证券清单有重复代码')
    return items


def run(args,clock=utc_now):
    started=clock();local=started.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai'))
    day=date.fromisoformat(args.session_date) if args.session_date else local.date()
    target=target_time(day,args.target)
    report={'kind':'intraday_data_validation_only','started_at':started.isoformat(),'target_at':target.isoformat(),'provider':args.provider,'status':'blocked','network_attempted':False,'candidates':[],'live_selection_enabled':False,'reasons':[]}
    try:
        if not args.calendar or not args.catalog:raise InvalidData('必须提供有来源的交易日历和证券主数据；缺失时不请求行情')
        calendar=parse_calendar(json.loads(Path(args.calendar).read_text()))
        items=parse_catalog(json.loads(Path(args.catalog).read_text()))
        gate=time_gate(started,target,calendar);report['time_gate']=gate
        if gate!='allowed':
            report['status']='skipped_'+gate;return report
        eligible=[];excluded={}
        for item in items:
            reasons=filter_main_board(item,started)
            if reasons:excluded[item.security.symbol]=reasons
            else:eligible.append(item)
        report.update(catalog_count=len(items),eligible_count=len(eligible),excluded=excluded,coverage_scope='caller_supplied_catalog_not_independently_certified_as_full_market')
        if not eligible:raise InvalidData('没有通过主板和状态检查的证券；未制造候选')
        if args.dry_run:
            report['status']='dry_run_no_network';report['reasons']=['仅检验输入与时间，未证明行情可用'];return report
        transport=HttpTransport(circuit=CircuitRegistry(Path(args.state_dir)/'circuits'),allowed_hosts={'qt.gtimg.cn','hq.sinajs.cn'},attempts=1,timeout=10)
        provider=TencentProvider(transport) if args.provider=='tencent-text' else SinaProvider(transport)
        report['network_attempted']=True
        batch=provider.fetch_quotes([item.security for item in eligible],deadline=target.replace(second=45))
        report.update(received=len(batch.quotes),missing=batch.missing,request_errors=batch.errors,collection_finished_at=batch.finished_at.isoformat(),usage_permission=batch.source.usage_permission.value)
        report['reasons']=inspect_batch(batch,{i.security.symbol for i in eligible},target)
        report['status']='data_checks_failed' if report['reasons'] else 'requested_catalog_data_checks_passed'
        report['quotes']=[{'symbol':q.security.symbol,'quote_at':q.quote_at.isoformat() if q.quote_at else None,'collected_at':q.collected_at.isoformat(),'freshness':q.freshness(batch.finished_at),'quality_flags':q.quality_flags} for q in batch.quotes]
    except (MarketError,ValueError,KeyError,TypeError,OSError) as exc:
        report['status']='blocked';report['reasons']=[f'{type(exc).__name__}: {exc}']
    finally:report['finished_at']=clock().isoformat()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider',choices=['tencent-text','sina-text'],default='tencent-text')
    parser.add_argument('--target',choices=['14:50','14:55'],required=True)
    parser.add_argument('--session-date',help='目标交易日；默认实际北京时间日期，不改变程序时钟')
    parser.add_argument('--calendar');parser.add_argument('--catalog')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--state-dir',default='/tmp/ashare-v2-market')
    parser.add_argument('--report-dir',default='/tmp/ashare-v2-intraday-reports')
    args=parser.parse_args();result=run(args)
    directory=Path(args.report_dir);directory.mkdir(parents=True,exist_ok=True)
    path=directory/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]+'.json')
    with path.open('x') as handle:json.dump(result,handle,ensure_ascii=False,indent=2)
    path.chmod(0o600)
    print(json.dumps({'status':result['status'],'network_attempted':result['network_attempted'],'reasons':result['reasons'],'report_file':str(path)},ensure_ascii=False,indent=2))
    return 0 if result['status'] in {'dry_run_no_network','requested_catalog_data_checks_passed','skipped_closed'} else 2
if __name__=='__main__':sys.exit(main())
