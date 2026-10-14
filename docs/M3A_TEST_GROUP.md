# M3-A 隔离合成联调与下一轮测试群手册

本轮只执行本地环回 HTTP 与 Mock，未安装 AstrBot、未发 QQ、未连接生产。下一轮需用户明确批准测试机器人、测试群与可达网络地址。**订阅命令本身和 /sub_test 都可能发送消息，不能拿它们当作无发送预览。**

## 当前确认的 RSSHub 契约

观察版本 a047f46e47cb2a0f7a795f30b9f82ae027005846。其 bootstrap_skip_history 默认 true，无已保存条目历史的首轮会登记并跳过推送。非空条目有 id/GUID 时用稳定身份去重，保留历史；空条目解析失败，不会建立该历史。见 [配置](https://github.com/AstrBot-Elementary-School/astrbot_plugin_rsshub/blob/a047f46e47cb2a0f7a795f30b9f82ae027005846/docs/usage/configuration.md)、[轮询及去重](https://github.com/AstrBot-Elementary-School/astrbot_plugin_rsshub/blob/a047f46e47cb2a0f7a795f30b9f82ae027005846/src/application/services/feed_polling_service.py)、[XML 解析](https://github.com/AstrBot-Elementary-School/astrbot_plugin_rsshub/blob/a047f46e47cb2a0f7a795f30b9f82ae027005846/src/infrastructure/fetcher/rss/xml_parser.py)。实际安装版本必须另行核对，Mock 不等于运行该插件。

不能订阅空正式 Feed 后立刻加入第一条真实通知并认为它必定推送：它可能被当作首次历史而跳过。必须先使用非空、完整隔离的合成初始化 Feed，确认历史建立后才加入下一条。

## 创建全新合成实验

下面目录必须不存在，不允许覆盖或导入用户原库。命令生成独立 SQLite 和双重实验标识，只有通过标识检查的实验库允许后续动作；不创建评测标签，也不接入旧 36 条。每个实验使用不同的合成 URL 命名空间，GUID 不与别的实验混用，重启同一实验仍稳定。

```powershell
.\.venv\Scripts\python.exe -m cdu_radar rss-synthetic init --directory artifacts/m3a-group-lab --ttl 3600
.\.venv\Scripts\python.exe -m cdu_radar serve-approved-rss --db artifacts/m3a-group-lab/synthetic.sqlite3 --state-dir artifacts/m3a-group-lab/http-state --bind 127.0.0.1 --port 8765 --interval 10 --max-age 30
```

init 只在新实验库中创建并批准一条明确带“合成初始化”的协议夹具，这不是自动批准任何真实通知。ttl 是实验维护期，1–86400 秒；避免在初始化轮询完成前到期。另一个终端用 Invoke-WebRequest 读取 `/approved.xml`，应为一条合成消息；`/health` 200 表示当前可用，变更后的短暂 503 要等刷新完成。不要以根目录 `/` 作为 Feed URL。

实验操作命令（仅该实验目录）：

```powershell
# 确认订阅历史已建立后再加第二条：
.\.venv\Scripts\python.exe -m cdu_radar rss-synthetic add --directory artifacts/m3a-group-lab --ttl 3600
# 模拟源变化；该条重新待审，不会自动恢复批准：
.\.venv\Scripts\python.exe -m cdu_radar rss-synthetic update 2 --directory artifacts/m3a-group-lab
# 仅实验库允许夹具再次批准，同 GUID/pubDate：
.\.venv\Scripts\python.exe -m cdu_radar rss-synthetic approve 2 --directory artifacts/m3a-group-lab --ttl 3600
.\.venv\Scripts\python.exe -m cdu_radar rss-synthetic revoke 2 --directory artifacts/m3a-group-lab
```

到期用短 ttl 的实验条目等待，或用现有自动化 FakeClock 回归；不改真实审核历史。导出失败由自动化测试注入，实际测试群如需模拟权限/磁盘故障，先备份且只改实验目录，禁止拿生产目录演练。

## 用户下一步要执行和确认的事项

1. 明确机器人实例、测试群、插件版本和网络目标；只用隔离测试群，生产订阅停用。本轮未安装插件，用户在测试实例自行安装/确认版本后再开始。数据库、Feed URL 和去重历史均与正式环境分开。
2. 确保机器人能读 HTTP 200 的合成 Feed。机器人若在另一台机器/容器，127.0.0.1 不代表 Windows 宿主；需要同宿主部署或另行授权的受控代理。当前服务不会直接监听 LAN/公网，代理不能提供旧缓存回退。
3. 核查 bootstrap_skip_history=true，只保留初始化一条，再在授权测试会话执行 `/sub <测试 RSS URL>`。等待日志明确“首次初始化跳过历史 1 个”或现场核查去重历史非空。不要假定 `/sub_state ... off` 后后台还会完成初始化，也不要在确认前加入第二条。
4. 初始化应 0 条业务推送（订阅命令可能有确认回复）。再执行 add，等待刷新与下一轮轮询，应只有新条目推送 1 次。检查中文、原文链接与所有未知日期/资格/AI 提示；不读取或批准真实历史通知来凑样本。
5. 相同 Feed 重复轮询、重启本项目服务、重启机器人保留去重库，均应 0 新消息；再加两条验证同批/同秒条目。不要清空去重库或换实验库仍沿用相同测试 URL 后宣称是恢复测试。
6. update 后新请求应停止提供旧条目，安全刷新后条目移除；approve 恢复同 GUID/pubDate，不应被当作新比赛。revoke/到期同样不能继续返回旧 200/旧 304。已发群消息仍存在，更正提醒需单独设计和授权，RSS 移除不等于撤回。
7. 暂时 503 不应推进订阅历史，恢复后只处理真正的新身份。检查插件是否有失败队列、代理缓存、首次初始化或历史窗口导致重发/漏发；最终保存 HTTP、GUID/pubDate、插件去重日志和测试群观察结果再决定后续正式接入。

`/sub_state <ID> off` 可在测试结束停用订阅；`/sub_test <ID/URL>` 会强制推最新一条，若用户未授权发送就不要执行。命令细节见 [固定版本命令文档](https://github.com/AstrBot-Elementary-School/astrbot_plugin_rsshub/blob/a047f46e47cb2a0f7a795f30b9f82ae027005846/docs/usage/commands.md)。本手册只是待用户执行的方案，没有宣称真实群联调通过。
