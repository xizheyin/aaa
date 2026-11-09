"""在实际交易时段进行两次诊断采样；不是自动选股，不回填错过的窗口。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timedelta,timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import uuid

from .intraday import parse_calendar,parse_catalog
from .policy import filter_main_board,target_time,time_gate
from .providers import TencentProvider,SinaProvider
from .transport import CircuitRegistry,HttpTransport
from .types import InvalidData,MarketError,SecurityId,SHANGHAI,utc_now

REQUIRED=('price','amount_cny','turnover_pct','quote_at','is_st','suspended')


def prepare_plan(day,slots,calendar,now):
    if now.utcoffset() is None:raise InvalidData('实际时钟必须包含时区')
    if calendar.captured_at.utcoffset() is None or calendar.captured_at>now:raise InvalidData('日历时间无效或来自未来')
    targets=[target_time(day,slot) for slot in slots]
    if len(set(slots))!=len(slots):raise InvalidData('目标时点重复')
    if slots!=sorted(slots):raise InvalidData('采样时点必须按时间顺序')
    open_day=calendar.is_session(day)
    return {'day':str(day),'created_at':now.isoformat(),'targets':[v.isoformat() for v in targets],
            'calendar_is_session':open_day,'calendar_source':calendar.source,
            'status':'prepared_not_collected' if open_day else 'closed',
            'intraday_evidence_exists':False,'live_selection_enabled':False,'candidates':[]}


def diagnostic_universe(catalog_path,max_symbols,now):
    # 文件不存在时只用明确标为样本的公开示例代码，不伪造完整主板清单或状态。
    if not catalog_path or not Path(catalog_path).exists():
        return [SecurityId('SH','600000'),SecurityId('SZ','000001')],{
            'scope':'two_diagnostic_examples_only','catalog_available':False,
            'full_mainboard_coverage_verified':False,'st_status_verified':False,
            'suspension_status_verified':False,'filter_note':'示例代码不能证明当前主板身份或可交易性，不是候选股票'}
    data=json.loads(Path(catalog_path).read_text());items=parse_catalog(data)
    if not items:
        symbols,scope=diagnostic_universe(None,max_symbols,now)
        scope.update(scope='catalog_empty_diagnostic_examples_only',catalog_count=0,reference_error='交易所证券清单为空；仅做两个示例代码诊断，不能代表主板全集')
        return symbols,scope
    selected=[];excluded=0;unknown_st=0;unknown_halt=0
    for item in items:
        reasons=filter_main_board(item,now)
        hard=[r for r in reasons if r not in {'ST状态不合格或未知','停牌状态不合格或未知'}]
        if hard or item.is_st is True or item.suspended is True:excluded+=1;continue
        unknown_st+=item.is_st is None;unknown_halt+=item.suspended is None
    # 均衡取沪深小样本；未知状态允许被诊断观察，绝不允许进入选股。
    for exchange in ('SH','SZ'):
        eligible=[i for i in items if i.security.exchange==exchange and i.board=='MAIN' and i.instrument_type=='A' and i.is_st is not True and i.suspended is not True and not re.search('ST|退',i.name,re.I) and not any('代码范围' in reason or '来源缺失' in reason for reason in filter_main_board(i,now))]
        selected.extend(i.security for i in eligible[:max_symbols//2])
    if not selected:raise InvalidData('证券身份文件没有可诊断的沪深主板样本')
    return selected,{'scope':'mainboard_identity_sample','catalog_available':True,'catalog_count':len(items),'requested_count':len(selected),'excluded_identity_or_known_risk':excluded,'unknown_st_count':unknown_st,'unknown_suspension_count':unknown_halt,'identity_coverage_claim':data.get('identity_coverage_complete',False),'full_mainboard_coverage_verified':False,'st_status_verified':False,'suspension_status_verified':False,'filter_note':'未知ST/停牌仅可诊断，不能作为通过主板可交易性过滤的候选'}


def summarize_batch(batch,target,universe):
    missing_fields={key:len(batch.missing) for key in REQUIRED};quotes=[]
    for q in batch.quotes:
        for key in REQUIRED:
            if getattr(q,key) is None:missing_fields[key]+=1
        offset=(q.quote_at-target).total_seconds() if q.quote_at else None
        age=(q.collected_at-q.quote_at).total_seconds() if q.quote_at else None
        within=(q.quote_at is not None and q.quote_at.astimezone(SHANGHAI).date()==target.date()
                and abs(offset)<=30 and age>=-3
                and q.quote_at.astimezone(SHANGHAI).time()<datetime.strptime('14:57','%H:%M').time())
        quotes.append({'symbol':q.security.symbol,'quote_at':q.quote_at.isoformat() if q.quote_at else None,
            'collected_at':q.collected_at.isoformat(),'timestamp_age_seconds':age,
            'target_offset_seconds':offset,'timestamp_matches_slot':within,
            'freshness':q.freshness(batch.finished_at),'price_is_positive':q.price is not None and q.price>0,
            'raw_amount_field_present':any(q.raw_metrics.get(k) is not None for k in ['amount_field_37','amount_field_9']),
            'raw_turnover_field_present':q.raw_metrics.get('turnover_field_38') is not None,
            'standardized_amount_verified':q.amount_cny is not None,'turnover_verified':q.turnover_pct is not None,
            'st_status_known':q.is_st is not None,'suspension_status_known':q.suspended is not None,
            'quality_flags':q.quality_flags})
    blockers=[]
    if not batch.complete:blockers.append('请求集合未完整返回')
    if not universe.get('full_mainboard_coverage_verified'):blockers.append('未证明沪深主板全市场覆盖')
    if any(missing_fields.values()):blockers.append('存在关键字段缺失，见missing_fields')
    if not quotes or not all(q['timestamp_matches_slot'] for q in quotes):blockers.append('行情时间不满足目标时点或语义仍待核验')
    if batch.finished_at>target+timedelta(seconds=45):blockers.append('采集结束错过允许窗口')
    if not batch.source.contract_verified:blockers.append('供应商字段语义和单位未完成验收')
    if batch.source.usage_permission.value=='unknown':blockers.append('研究使用和公开展示许可未确认')
    return {'provider':batch.source.provider,'status':'data_not_ready' if blockers else 'diagnostic_checks_passed',
        'started_at':batch.started_at.isoformat(),'finished_at':batch.finished_at.isoformat(),
        'elapsed_seconds':(batch.finished_at-batch.started_at).total_seconds(),'requested_count':len(batch.requested),
        'received_count':len(batch.quotes),'missing_symbols':batch.missing,'errors':batch.errors,
        'complete_for_requested_symbols':batch.complete,'missing_fields':missing_fields,'quotes':quotes,
        'blockers':blockers,'real_candidate_count':0,'candidates':[],'live_selection_enabled':False,
        'public_display_allowed':False}


def collect_slot(target,calendar,securities,universe,providers,clock=utc_now):
    started=clock();gate=time_gate(started,target,calendar)
    record={'target_at':target.isoformat(),'started_at':started.isoformat(),'time_gate':gate,
            'universe':universe,'network_attempted':False,'results':[],'candidates':[],'live_selection_enabled':False}
    if gate!='allowed':
        record.update(status='skipped_'+gate,finished_at=clock().isoformat());return record
    record['network_attempted']=True
    def one(provider):
        try:return summarize_batch(provider.fetch_quotes(securities,deadline=target+timedelta(seconds=45)),target,universe)
        except Exception as exc:return {'provider':provider.source.provider,'status':'failed','error_type':type(exc).__name__,'error':str(exc)[:500],'requested_count':len(securities),'received_count':0,'missing_symbols':[s.symbol for s in securities],'missing_fields':{key:len(securities) for key in REQUIRED},'candidates':[],'live_selection_enabled':False}
    # 不同来源并行各一批；每源仍遵守限速、熔断和时限，绝不重试502/503。
    with ThreadPoolExecutor(max_workers=2) as executor:record['results']=list(executor.map(one,providers))
    record.update(status='collected_diagnostic_only',finished_at=clock().isoformat())
    return record


def wait_for_slot(target,clock=utc_now,sleep=time.sleep,max_wait_seconds=900):
    remaining=(target-clock()).total_seconds()
    if remaining>max_wait_seconds:raise InvalidData('距离目标超过15分钟，拒绝长期驻留等待')
    while remaining>0:
        sleep(min(30,remaining));remaining=(target-clock()).total_seconds()


def save_unique(path,data):
    encoded=(data+'\n').encode() if isinstance(data,str) else (json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode()
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as handle:handle.write(encoded)
    return hashlib.sha256(encoded).hexdigest()


def write_markdown(directory,manifest):
    lines=['# 交易日两时点采集验收记录','',f"目标日期：{manifest['plan']['day']}。本报告不生成候选股票，也不表示行情已接通。",'', '| 目标 | 执行状态 | 来源 | 返回/请求 | 关键字段缺失 |','| --- | --- | --- | --- | --- |']
    for slot in manifest.get('slots',[]):
        results=slot.get('results') or [{}]
        for r in results:lines.append(f"| {slot['target_at']} | {slot['status']} | {r.get('provider','未请求')} | {r.get('received_count',0)}/{r.get('requested_count','—')} | {r.get('missing_fields',r.get('error','未进入采集窗口'))} |")
    lines+=['','行情时间、采集时间、时间偏差、错误和质量标记见同目录JSON。只有盘中真实请求记录才是本轮实测；prepared_not_collected、skipped、单元测试或旧报价都不是实盘成功。','','全市场覆盖、成交额单位、换手率、ST/停牌与授权必须分别验收；缺任何关键条件就不进入真实情绪或选股阶段。']
    save_unique(directory/'report.md','\n'.join(lines))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',required=True)
    parser.add_argument('--slots',default='14:50,14:55')
    parser.add_argument('--calendar',required=True)
    parser.add_argument('--catalog')
    parser.add_argument('--providers',choices=['both','tencent-text','sina-text'],default='both')
    parser.add_argument('--max-symbols',type=int,default=40)
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--report-root',default='/tmp/ashare-session-reports')
    parser.add_argument('--state-dir',default='/tmp/ashare-v2-market')
    args=parser.parse_args()
    if not 2<=args.max_symbols<=40 or args.max_symbols%2:parser.error('每源样本上限需为2至40的偶数')
    calendar=parse_calendar(json.loads(Path(args.calendar).read_text()))
    plan=prepare_plan(date.fromisoformat(args.date),args.slots.split(','),calendar,utc_now())
    directory=Path(args.report_root)/(utc_now().strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]);directory.mkdir(parents=True,exist_ok=False)
    manifest={'plan':plan,'slots':[],'status':'prepared_not_collected','eastmoney_requests':0,'candidates':[],'live_selection_enabled':False}
    save_unique(directory/'plan.json',plan)
    if args.prepare_only or not plan['calendar_is_session']:
        if not plan['calendar_is_session']:manifest['status']='skipped_closed'
        save_unique(directory/'manifest.json',manifest);print(json.dumps({'status':manifest['status'],'report_directory':str(directory)},ensure_ascii=False));return 0
    if utc_now().astimezone(SHANGHAI).date()!=date.fromisoformat(args.date):
        manifest['status']='blocked_wrong_actual_date'
        save_unique(directory/'manifest.json',manifest);print(json.dumps({'status':manifest['status'],'report_directory':str(directory)},ensure_ascii=False));return 2
    circuit=CircuitRegistry(Path(args.state_dir)/'circuits')
    client=HttpTransport(circuit=circuit,allowed_hosts={'qt.gtimg.cn','hq.sinajs.cn'},attempts=1,timeout=10)
    providers=[p for p in [TencentProvider(client),SinaProvider(client)] if args.providers=='both' or p.source.provider==args.providers]
    try:
        for value in plan['targets']:
            target=datetime.fromisoformat(value);wait_for_slot(target)
            securities,universe=diagnostic_universe(args.catalog,args.max_symbols,utc_now())
            slot=collect_slot(target,calendar,securities,universe,providers)
            slot['evidence_sha256']=save_unique(directory/(target.strftime('%H%M')+'.json'),slot)
            manifest['slots'].append(slot);print(f"{value}: {slot['status']}",flush=True)
        manifest['status']='finished_diagnostic_only'
    except Exception as exc:manifest.update(status='failed',error_type=type(exc).__name__,error=str(exc)[:500])
    manifest['finished_at']=utc_now().isoformat();save_unique(directory/'manifest.json',manifest);write_markdown(directory,manifest)
    print(json.dumps({'status':manifest['status'],'report_directory':str(directory),'live_selection_enabled':False},ensure_ascii=False))
    return 0 if manifest['slots'] and all(s['network_attempted'] for s in manifest['slots']) else 2
if __name__=='__main__':sys.exit(main())
