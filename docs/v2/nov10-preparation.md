# 11月10日真实盘中验证：准备交付与执行说明

**当前判断：尚不具备真正计算全市场情绪、题材强度与尾盘选股的条件。11月10日实盘结果尚未产生，不能提前写成成功。**

本批只准备程序、官方日历和手机手动配置。沿用现有项目，没有创建多智能体、购买服务、修改线上Netlify站，也没有在收盘后重复请求报价冒充盘中测试。

## 1. 已确认与仍缺失

| 项目 | 本轮状态 |
| --- | --- |
| 时间 | 11月9日准备时已收盘；实际14:50/14:55测试只能在后续交易时段发生 |
| 下一交易日 | 按沪深交易所2026年休市公告核对为2026-11-10；年度公告不排除未来临时停市，执行前仍应关注公告 |
| 日历 | 已生成有官方出处的2026年交易日历，并通过节假日及11月10日单元检查；不是仅按工作日猜测 |
| 沪深主板完整股票列表 | 尚未取得并完成本轮验收。交易所下载程序已准备，各源最多一次、无重试；本地启动命令被用户消息中断，未产生列表HTTP成功证据 |
| ST、停牌状态 | 未验证；清单缺状态则保留null。没有用“不含ST名称”推定非ST，也没有把价格不变当作停牌 |
| 成交额单位、换手率 | 当前适配器只保留待验原始字段线索，标准字段尚未认可，不补0 |
| 行情时间 | 可解析供应商时间字段，但未在11月10日盘中验证其语义与延迟 |
| 免费与使用许可 | 上轮腾讯/新浪小样本返回历史报价，不证明稳定覆盖；研究、缓存及公开展示授权仍待确认 |
| GitHub实际runner | 本轮未连接、未执行；工作流是待用户上传启用的配置，不是已创建的定时任务 |

官方来源：

- [上交所2026休市公告](https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml)
- [深交所2026休市公告](https://www.szse.cn/disclosure/notice/general/t20251222_618087.html)

日历文件：`research/inputs/exchange-calendar-2026.json`。它只覆盖2026年，年度以外拒绝使用；实际临时停市需再核对。深交所公告在本轮搜索结果中返回了完整年度安排，随后直接网页读取超时；不能把该次超时称为网页连接成功。

## 2. 定时执行能力与手机方案

**没有创建已启用的云端定时任务。** 当前交互工作区未验证无人值守运行保障，GitHub凭据未连接，免费调度也没有分钟级准点保证。因此本批提供手机提前启动的手动工作流，而不承诺准点。

一次性准备：将这批代码上传已有私有仓库 `a-share-short-term-lab`。可以使用完整项目ZIP，或只上传随附的 `ashare-nov10-phone-test-kit.zip` 内部文件到已有仓库根目录。不要新建项目，不要把ZIP本身当源码上传。小包仅含验收所需模块、日历和工作流，不包含网页或部署代码。

11月10日当天，用iPhone Safari打开GitHub仓库：

1. 建议北京时间 **14:45左右** 打开 **Actions → 手机手动盘中数据验收 → Run workflow**。这是提前启动程序，不是提前采集14:50行情。
2. `session_date` 保持 `2026-11-10`，`providers=both`，`prepare_only=false`。`prepare_reference=true` 表示各请求一次交易所证券身份列表；失败会明确记录，不反复请求。
3. 点 **Run workflow** 后不必一直开着页面。程序使用实际时钟等待14:50，再等待14:55，各供应商每个时点只尝试一批。结束后在同一运行的 **Artifacts** 下载 `market-acceptance-2026-11-10-运行号`。

如想今天先检查准备配置，可选择 `prepare_only=true`；只生成计划，不采集报价，不作为实盘成功。

**延迟限制：** GitHub可能排队，Python或网络准备也可能延迟。迟到超过45秒则记 `skipped_missed_window`，不请求后来行情回填。程序拒绝等待超过15分钟，建议不要过早启动。免费工作流、手机触发或本程序都无法保证精确准点。

未实跑远端工作流，不能保证GitHub账号的Actions权限、额度或网络出口可用。需保持私有仓库，查看免费额度并设置预算到限停止，不开启付费或自动充值。每个作业最多15分钟，报告只保留3天；不要因数据不通过而反复点重跑。

## 3. 本轮可运行模块

- `backend/app/market/session_probe.py`：实际时钟两时点采样、时间窗口校验、每来源统计、独占写入证据、JSON及Markdown报告。
- `research/verification/prepare_session_reference.py`：交易所证券身份下载，每源一次、有限超时；不填写未知ST/停牌为false，不请求东方财富报价。
- `.github/workflows/nov10-intraday-manual.yml`：只含 `workflow_dispatch`，**没有cron，没有自动部署，没有云端写站点接口**。
- 原 `app.market.intraday` 严格验收程序继续保留。新的session_probe是诊断采样程序，允许在状态不完整时查看明确标记的样本，以便查清缺失原因，绝不把样本当作筛选通过。

普通本地准备检查（项目根目录，无需联网）：

```bash
PYTHONPATH=backend python -m app.market.session_probe \
  --date 2026-11-10 --slots 14:50,14:55 \
  --calendar research/inputs/exchange-calendar-2026.json \
  --prepare-only --report-root research/session-results
```

当天真实采集：

```bash
PYTHONPATH=backend python -m app.market.session_probe \
  --date 2026-11-10 --slots 14:50,14:55 \
  --calendar research/inputs/exchange-calendar-2026.json \
  --catalog research/private_inputs/exchange-mainboard-identities.json \
  --providers both --max-symbols 40 \
  --report-root research/session-results
```

脚本没有伪造当前时间参数。日期不符、休市、窗口错过或准备数据无效时记录原因。证券身份清单存在时均衡取沪深小样本、每源最多40只；没有清单时仅用两只明确标记的诊断示例。**两种模式都不宣称完成全市场覆盖。** 未知ST/停牌的样本仅用于诊断，不能当候选。

不在目标窗口发起报价请求、不重跑502/503来源；东方财富仍在传输层本批阻断名单中。许可未知的来源不会公开发布，不连接券商，不产生买卖动作。

## 4. 保存的内容与通过标准

每次运行使用单独目录。准备模式只写 `plan.json` 和 `manifest.json`；实际执行后逐时点写 `1450.json`、`1455.json`，并写总 `manifest.json` 和 `report.md`。失败或中断时可能只有已完成时点，不能据此声称两次都完成。文件独占创建并附哈希，已有证据不覆盖。准备状态明确为 `prepared_not_collected`，不冒充观察结果。

每时点记录实际开始/结束、供应商、请求/返回数量、缺失代码、每个关键字段缺失数、供应商时间、采集时间、时间差、窗口偏差、错误与质量标记。完整报价价格、个人记录和持仓不随公开站点发布。

判定真正情绪或选股可用，必须另外证明：主板全集覆盖、可靠ST/停牌/异常状态、成交量额单位和换手率等字段、时间语义/延迟、题材成员和前日对照、适用使用许可。两个时点的小样本成功不能证明长时间稳定或全市场条件通过。

`live_selection_enabled=false` 和空候选始终保留；本批不启用真实情绪、题材强度或筛选。

## 5. 本地检查结果

本次新增16项离线用例，与已有80项数据层用例合计 **96项通过，0.14秒**；Python编译检查及手动工作流YAML解析通过。见 `research/evidence/session-preparation-tests.xml`。所有离线输入明确为合成测试，不能视为11月10日实盘结果。当前只有准备计划和模块验证；未来实际采集报告应以Artifacts中的真实时间记录为准。
