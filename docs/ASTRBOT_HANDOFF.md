# AstrBot 测试群交接方案（未执行联调）

2026-10-14 只读研究用户指定的两个插件；没有安装、运行或发群消息。固定观察版本：RSSHub a047f46e47cb2a0f7a795f30b9f82ae027005846、Soulter RSS 57244346672bd1b35098bd8e4adc8913aa412094。feedparser 通过不等于 AstrBot 实际推送验收通过。

## 源码确认的行为

RSSHub 使用 HTTP(S) Feed，`/sub <URL>` 订阅、`/sub_state <ID> off` 停用、`/sub_test <ID/URL>` 会实际发送最新一条，不能把它当作无发送预览。见 [命令文档](https://github.com/AstrBot-Elementary-School/astrbot_plugin_rsshub/blob/a047f46e47cb2a0f7a795f30b9f82ae027005846/docs/usage/commands.md)。

其 XML 解析器借助 feedparser，优先条目 id/guid，轮询保存稳定身份与内容指纹。配置 bootstrap_skip_history 默认 true；无既有历史的首轮可只登记、不发送。见 [配置](https://github.com/AstrBot-Elementary-School/astrbot_plugin_rsshub/blob/a047f46e47cb2a0f7a795f30b9f82ae027005846/docs/usage/configuration.md)、[解析器](https://github.com/AstrBot-Elementary-School/astrbot_plugin_rsshub/blob/a047f46e47cb2a0f7a795f30b9f82ae027005846/src/infrastructure/fetcher/rss/xml_parser.py)、[轮询/去重](https://github.com/AstrBot-Elementary-School/astrbot_plugin_rsshub/blob/a047f46e47cb2a0f7a795f30b9f82ae027005846/src/application/services/feed_polling_service.py)。清空/丢失插件去重库可能重放，不能依靠本项目 GUID 保证任意插件绝不重发。

空 Feed 元信息可解析，但其条目 parser 对无 entries 报错，空 Feed 不会建立条目历史；因此随后第一条真实批准也可能被 bootstrap 当作历史跳过。下面要求用独立合成 Feed 初始化，再观察下一条，而不是直接把正式第一条作为初始化消息。

Soulter `/rss add <URL> [cron]` 首次取最新条目的时间/link 为水位；有 pubDate 时主要按时间大于水位筛选，不依赖 GUID，无 pubDate 才按 link。初始化直接访问 latest_item[0]，空 Feed 不适合直接新增订阅。见 [实现](https://github.com/Soulter/astrbot_plugin_rss/blob/57244346672bd1b35098bd8e4adc8913aa412094/main.py)。日期解析依赖主机时间转换；同时或同秒新条目可能受时间水位影响，需要测试宿主时区与多条发布。本项目不会伪造更晚 pubDate 来迎合它。

## 下一轮测试群步骤

1. 另行由用户确认测试群/机器人/网络目标，备份插件配置和去重历史。只接入隔离测试群，保持正式群停用。核对实际安装版本及命令帮助，版本变化后重新检查行为。
2. 使用独立合成库和 XML（不是本机 36 条旧通知），人工批准一条明确标为合成的初始化条目，导出到只含 XML 的静态目录。HTTP 返回 200、UTF-8 XML，机器人所在网络能读取；不能暴露 SQLite、Key 或诊断目录。
3. 暂停或限制自动推送后订阅测试 URL。RSSHub 保持 bootstrap_skip_history=true，观察日志是否登记了该条历史；Soulter 观察水位和最新链接。具体暂停/初始化方式以现场版本为准，初始订阅前先确认推送限制，不假定“随后 off”能消除订阅瞬间的竞态。
4. 明确完成初始化后，人工批准第二条合成通知，预览新增数 1，再导出。开启测试订阅、观察只有第二条新消息，原文链接、中文、未知资格/日期/AI 表述正确。本轮这些命令未实际在机器人上执行。
5. 再次导出相同 Feed、重复轮询、重启机器人保留数据，均应 0 新消息。另测同批两条、同秒发布时间；RSSHub 应验证 GUID 去重，Soulter 应特别核查时间水位漏项。
6. 修改原文：旧批准失效，未重新审核的新版绝不出现。重新审批更正需显式 ack-correction，同 GUID/pubDate 不当作新赛事；检查群里原消息仍存在，后续更正提醒需要单独设计和批准。
7. 撤销、到期、导出失败各测一次。Feed 移除不等于 QQ 撤回；旧 XML 导出失败时暂停静态发布。测试结束停用订阅、归档审核与导出证据，不能把清空去重库当作日常恢复操作。

不要直接订阅原 data/feed.xml，也不要批量批准旧 36 条来“凑消息”。RSSHub 和 Soulter 的初次行为不同，其他插件也必须单独核验；不能宣称任意订阅器会自动跳过历史。正式接入需以这组现场证据决定，不以源码研究或 feedparser 代替实测。
