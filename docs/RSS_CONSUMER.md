# 定向 RSS 离线简报（Issue #21）

`rss-consumer` 是一次性本机消费命令：固定 RSS v2 URL → 校验方向与覆盖 → 持久去重 → Markdown 草稿。它不调用模型、不连接机器人、不投递消息。只允许字面量 `http://127.0.0.1:PORT`，不跟随重定向、不读取环境代理；不访问官方原文或附件。远程接入留给未来 adapter。

## 使用

先启动现有的本机 Radar 发布器，建立以下私有配置。端口必须与实际发布器一致；不要使用生产机器人地址。

```json
{
  "schema_version": 1,
  "subscriptions": [
    {"name": "本科生综合信息", "url": "http://127.0.0.1:8765/rss/v2/all.xml?audience=undergraduate"},
    {"name": "AI与计算机", "url": "http://127.0.0.1:8765/rss/v2/all.xml?topic=ai&audience=undergraduate"},
    {"name": "竞赛双创精选", "url": "http://127.0.0.1:8765/rss/v2/featured.xml?domain=competition"}
  ]
}
```

```powershell
.\.venv\Scripts\python.exe -m cdu_radar rss-consumer poll --config data/consumer.json --state-db data/consumer/state.sqlite3 --directory artifacts/briefing-001
```

输出 `result.json` 与 `briefing.md`，每次必须指定新目录。退出码 0 表示本轮全部方向有效，1 表示某方向有明确消费故障，2 表示配置/文件/状态错误。运行频率由操作者控制，本命令不创建后台任务。

每方向可设置 `template`，使用 Python `string.Template` 的 `$title`、`$summary`、`$source`、`$source_date`、`$registration`、`$url`、`$change`；七项均须保留，未知字段或执行表达式不支持。标题、摘要及发布单位转义 Markdown，链接限制为官方 CDU 域名；参与资格默认提示核对原文。摘要来自源 Feed，不再编造或二次总结。

## 去重与恢复

订阅身份取规范化 URL，包括方向、筛选、发布器地址与端口。参数顺序和默认值不会产生新身份。GUID 在不同订阅中独立去重，改名和换模板不重置水位。改变端口/筛选会成为新订阅，首次仍不补发历史。

首次合法 Feed（包括合法空 Feed）只登记现有 GUID/内容哈希。后续新 GUID 生成新增草稿；相同 GUID 的来源正文、标题或分类等消费内容变化生成更新草稿；重复轮询和进程重启不重复生成。首次 pubDate 不作为“源发布日期”，不靠日期水位去重。

私有 SQLite 单事务保存水位和完整草稿记录，串行处理并发水位变化。它记录的是**草稿已生成**，不是消息已送达。文件输出失败后可恢复已提交的结果，无需再次轮询：

```powershell
.\.venv\Scripts\python.exe -m cdu_radar rss-consumer export --state-db data/consumer/state.sqlite3 --run-id 2 --directory artifacts/recovered-002
```

不使用状态库保存的历史草稿冒充新鲜报道；导出旧 run 是显式恢复操作。Feed 只保证当前最多 100 条窗口，消费者拒绝截断窗口，不能承诺长时间停机期间无漏收；历史草稿无法代表原文当前仍有效。

## 状态与限制

| 状态 | 本轮行为 |
| --- | --- |
| `initialized` | 建立首次水位，不补发历史 |
| `ready` / `empty` | 有效 Feed；没有新增草稿不等于没有重要信息 |
| `unavailable`（503） | 不更新水位，不用缓存生成新报道 |
| `rate_limited`（429） | 单独报告，不立即重试 |
| `featured_incomplete` | 精选覆盖不足，即使 HTTP 200 也暂停该方向草稿 |
| `timeout` / `connection_failed` | 单独报告，不更新水位 |
| `invalid_xml` / `filter_mismatch` / `truncated_feed` | 拒绝整份 Feed，保留已有水位 |

最多 10 个方向，每份 100 条/1 MiB，请求零自动重试。v2 Feed 必须提供方向、分类及 `undergraduate_state` 元数据，消费者同时校验规范路径和条目标签。现有只读源质量门禁、自动发布及旧审批数据不改。

## 复现与未来接入清单

```powershell
.\.venv\Scripts\python.exe scripts/m10_offline_acceptance.py --directory artifacts/my-m10-loopback
```

该脚本使用全新合成数据库与实际环回 HTTP，生成三个方向的新增/更新草稿，验证首次不补发、重复轮询、重启和实际 503，退出时关闭自己的监听。异常 XML、429、超时、方向混入与数据库保护由离线测试验证。

真实机器人接入仍需明确：唯一消费/发送入口、固定订阅地址、目标测试会话、消息预算、去重与发送回执映射、凭据权限、停止恢复方案。#26 的准入条件继续生效，本轮 **QQ 实际投递 0 条**。
