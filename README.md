# 盘中行情诊断测试包

本仓库部署了测试包中的 Python 工具，通过 GitHub Actions 在云端运行，不包含网站界面。

## 使用方法

打开 [手机手动盘中数据验收](https://github.com/xizheyin/aaa/actions/workflows/nov10-intraday-manual.yml)，点击 **Run workflow**。

- 提前检查：勾选 `prepare_only`，只生成准备计划，不请求行情。
- 实际采集：目标日期必须是当天。2026-11-10 北京时间 14:45 左右启动，日期填 `2026-11-10`，`providers=both`，取消勾选 `prepare_only`，保留 `prepare_reference`。
- 程序在 14:50、14:55 尝试采集。GitHub 排队可能导致错过窗口，错过会记录原因。
- 运行结束后，在该次运行的 **Artifacts** 下载报告。报告保留 3 天。

自动的“部署准备检查”仅检查代码和生成离线计划，不运行盘中采集。实际采集需手动启动，没有定时任务。

## 本地运行

需要 Python 3.12。

```bash
PYTHONPATH=backend python -m app.market.session_probe --date 2026-11-10 --slots 14:50,14:55 --calendar research/inputs/exchange-calendar-2026.json --prepare-only --report-root research/session-results
```

此工具只做小样本诊断，不执行交易，也不能证明全市场选股已可用。原包文档保留在 `NOV10-PHONE-README.md` 和 `docs/v2/nov10-preparation.md`，其中原仓库名、历史状态与历史测试数字属于原交付记录；本次部署以当前仓库 Actions 结果为准。
