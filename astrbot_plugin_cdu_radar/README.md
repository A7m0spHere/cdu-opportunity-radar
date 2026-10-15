# CDU Radar 固定会话 RSS 插件（离线实现，未安装）

面向 AstrBot 4.25.5 的最小消费端，仅访问人工配置的一个 M3-A RSS URL，仅向一个完整 QQ unified_msg_origin 提交纯文本。没有聊天命令、事件订阅、Web API、导入导出管理或 LLM 工具，不读取/调用 AIHOT、模型 provider 或其他插件配置。仅使用已有 httpx 和 Python 标准库，不提供会自动安装全局依赖的 requirements.txt；如果现场 httpx/API 不兼容，停止安装。

默认 `enabled=false`，目标为空；不创建轮询器、HTTP 客户端或去重库。源码 ZIP 是可审查的安装候选，不是生产验收通过。安装和加载本身会改变生产插件注册表和专属配置，仍须批准。

v0.1.1 修复停止/超时的底层调用吞掉取消信号后返回 True 的边界：一律保守记为 uncertain，并在停止后跳过下一轮休眠。私有 SQLite Schema 仍为 v1，已有去重与尝试预算不清零；v0.1.0 安装候选仅保留作历史审查。

## 管理员配置

- `feed_url`：固定 `http://127.0.0.1:8765/approved.xml`，也可用明确环回 IP 和端口；不接受域名、URL 凭据、query/fragment 或重定向，不使用环境代理。
- `target_umo`：人工核对的完整 `平台实例ID:GroupMessage:QQ群号` 或 `平台实例ID:FriendMessage:QQ号`。QQ ID 必须为非零开头 5–20 位数字。不接受通配符、空目标、默认平台或多个目标；Feed 正文无法覆盖目标。
- `enabled`：管理员明确启用才开始；配置通过受信管理界面/专属文件维护，普通用户没有本插件的配置命令。插件不替代 AstrBot 管理界面自身的身份认证。
- `max_send_attempts`：默认 **2**，是该 URL+目标在持久库中的**累计尝试预算**，含明确失败、不确定结果和重启前已预留尝试，不是每轮/每天预算。
- `retry_limit`：默认 **0**，范围 0–2。仅 `Context.send_message()` 明确返回 False 时允许延迟重试；每条最多 1+retry_limit 次，且仍受累计预算约束。
- `retry_delay_seconds`：默认 60，最小 30。`poll_interval_seconds` 默认 30，最小 15。

设置完整目标后再审批 enabled=true。SDK 重新加载配置时只允许已注册的确切本插件，不使用未知名称的 reload 或全量重载；是否支持安全单插件加载要现场单独确认。URL/目标改变会与旧去重库绑定不一致并拒绝启动，不自动删库或重新初始化。提高预算等配置变更需管理员审批并重新加载，已有尝试不会清零。

运行中观察到配置对象变化时，原轮询器停止授权；在每次抓取后、每次提交前再次核对完整快照。**只改磁盘文件不保证运行时对象同步**，紧急停发应使用已验证的单插件停止/卸载流程；不要只凭改了文件就认定发送已停。

## 去重与失败状态

独立 `StarTools.get_data_dir('astrbot_plugin_cdu_radar')/state.sqlite3`，不访问 AstrBot 聊天数据库。跨进程 OS 锁拒绝第二个消费者；SQLite 事务在外部调用前提交尝试预留。文件损坏、未知 Schema、配置绑定变化或计数不一致均拒绝启动，不重建库。50,000 个 GUID 达到容量后停止处理，不自动淘汰旧记录造成重放。

私有 SQLite 连接显式要求 synchronous=FULL，不依赖运行时构建的默认刷盘级别；它不更改正式 Radar 库或聊天库的配置。

首个完整、非空的有效 RSS 200 只登记历史，不提交旧条目。空 Feed 不完成初始化；首次有效消息可能作为历史跳过，必须使用专属合成初始化条目。随后每轮最多提交一个尚未处理的 GUID；同批其他条目保留 pending，下一轮重新从当前 Feed 验证，不存储正文用于离线补发。

| 状态 | 含义 |
| --- | --- |
| baseline | 首次历史，永不作为新消息发送 |
| pending | 后续新 GUID，等待预算及当前有效 Feed |
| inflight | 尝试已持久预留，外部提交尚未完成 |
| submitted | Context 返回 True；**不是已验证 QQ 送达** |
| failed | Context 明确返回 False（4.25.5 表示未找到平台）；只允许有限重试 |
| uncertain | 超时、异常、取消、意外返回值或重启时发现 inflight；不自动重试 |
| withdrawn / withheld | 未完成项已从有效 Feed 消失，或正文在提交前变化；保留去重，不补发 |

503、其他非 200、网络/解析失败不改变去重水位或尝试预算，不使用上次成功正文。重试必须重新取得当前有效 Feed，缺项/变更即停止该项。RSS XML 只接受 UTF-8 RSS 2.0，拒绝 DTD/实体、缺失字段、重复 GUID、过大响应/条目与超长消息；不静默截掉“未知”事实，也不解释正文中的命令或目标指令。

不承诺 exactly-once：QQ/网络/平台内部重试可能重复；提交后数据库落盘前崩溃、API 超时或停止期间，消息是否已发可能未知。重启将 inflight 标为 uncertain，不自动再发，代价是可能漏发需人工核验。恢复旧备份或手动改库可能重放；不得通过删库“重试”。`submitted` 仅表示平台调用已返回；真实送达仍需测试会话观察。

terminate 会取消并等待唯一后台任务及其投递子协程，关闭 HTTP 客户端和 SQLite/OS 锁，没有独立重试线程或任务队列。它依赖 SDK 协程正常响应取消，不能撤回已进入网络或 QQ 的消息；若第三方发送函数不响应取消，停止会等待，不释放锁后假装已停止。

AstrBot 4.25.5 核心还会触发全局插件加载/卸载事件，即使指定单个插件也是如此。本插件自有清理不改其他插件，仍须在现场只读确认既有事件钩子的影响，再批准安装/停用；不能承诺平台的全局事件无间接副作用。卸载前先验证精确停用成功，保留配置和数据；SDK 的卸载入口会捕获终止错误后继续移除源码，不能把“目录删掉了”当作任务已停止。

## 打包与验收

仓库根目录执行 `python scripts/package_cdu_plugin.py`，只打包六个白名单源码/文档文件，拒绝覆盖已有 ZIP；不安装或上传。ZIP 根目录包含 metadata.yaml，使用 AstrBot 本地安装入口时须单独批准。

离线测试位于 tests/test_cdu_radar_plugin.py：Mock SDK、HTTP MockTransport、M3-A 隔离合成 Feed。测试不需要安装 AstrBot，也不发送 QQ。4.25.5 的官方 Context.send_message 返回语义见 [固定版本源码](https://github.com/AstrBotDevs/AstrBot/blob/v4.25.5/astrbot/core/star/context.py)。元数据版本范围不代表已兼容所有定制核心。

本地需求与生产隔离方案详见仓库 docs/M3B4_MINIMAL_PLUGIN.md。插件 ZIP、真实目标和私有去重库不进入 Git；默认关闭的源码可以提交。
