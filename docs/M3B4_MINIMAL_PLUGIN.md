# M3-B.4：生产 AstrBot 的最小固定会话消费器

日期：2026-10-15。本轮仅离线开发、单元/Mock 集成测试、构建部署草稿与本地 Git 提交准备。服务器连接、插件安装/启用、容器创建、QQ 发送、付费 LLM、PR 合并均为 0。M3-A 尚未合并 main，部署必须指向后续明确批准的代码快照。

## 方案比较与决定

| 维度 | A：裁剪 RSSHub v2.1.4 | B：专用最小插件（选择） |
| --- | --- | --- |
| 维护范围 | 上次静态审查已涉及 main/bootstrap、聊天命令、Web API、导入、LLM 工具、handlers、队列、持久层；删掉表面命令不能封闭间接入口 | 仅 engine.py 与 main.py 两个运行模块，配 metadata/配置 Schema；审计边界明确 |
| 权限 | 需逐个封闭所有入口、默认设置继承及历史重试 | 没有用户命令、Web API、导入或工具，固定管理员配置，只调用一个完整目标 |
| 定制核心兼容 | 名义最低版本符合，不等于页面/依赖/完整运行栈兼容 | 只使用 4.25.5 的 Star/StarTools、MessageChain、Context.send_message 生命周期 API；Mock 验证，生产兼容仍待确认 |
| LLM 风险 | initialize 会注册工具；handler 支持 AI 处理，需主动裁剪 | 没有注册或调用模型的代码，没有 provider 选择或 AIHOT 调用 |
| 目标隔离 | Feed 全局共享、订阅按会话，需同时限制命令/工具权限 | URL+目标绑定私有 SQLite，消息正文不能改源或改群；无空目标回退 |
| 去重/恢复 | 功能完整，但要约束历史队列补发与默认继承 | 非空历史初始化、永久 GUID 墓碑、累计预算、有限明确失败重试、不确定状态人工核验 |
| 安装/卸载影响 | 多后台组件、依赖与权限面，裁剪后的升级要持续重审 | 无 requirements 自动安装脚本，依赖已有 httpx；单一轮询任务、私有 SQLite，无管理入口；仍需审查加载/卸载生产影响 |

决定 B。不保留未使用的订阅管理、媒体、知识库、多模型或前端。现有 AIHOT 和生产业务配置不修改。已有三容器设计作为历史草稿保留，不执行其 up，也不新建 AstrBot/NapCat。

参考对照为固定提交 a047f46e47cb2a0f7a795f30b9f82ae027005846：[RSSHub 入口](https://github.com/AstrBot-Elementary-School/astrbot_plugin_rsshub/blob/a047f46e47cb2a0f7a795f30b9f82ae027005846/main.py)、[投递/重试](https://github.com/AstrBot-Elementary-School/astrbot_plugin_rsshub/blob/a047f46e47cb2a0f7a795f30b9f82ae027005846/src/application/services/notification_dispatcher.py)。不是基于 README 宣称隔离。

## 实现与权限契约

[独立插件目录](../astrbot_plugin_cdu_radar/README.md) 可打成 ZIP，不作为 cdu_radar Python 包安装，也不放入 RSS 镜像。main.py 只提供初始化/终止及内部发送适配，没有聊天事件、命令、LLM 工具或 Web API 注册。普通聊天/模型没有修改源、目标或预算的入口；可信管理员使用 AstrBot 自身认证的管理界面维护专属配置。

配置默认 enabled=false、target_umo 为空。禁用时不轮询、不创建消费器数据库。启用前必须人工核对一个完整 unified_msg_origin，匹配精确 QQ 会话语法；目标始终来自配置快照，任何 Feed 内容只作为 Plain 文本。仅允许显式 HTTP 环回 /approved.xml URL，不跟重定向，不使用环境代理，不跟随条目链接或图片。

每次提交前核对完整配置快照与当前授权；运行中对象改变使原消费者停发，URL/目标改变后旧数据库拒绝绑定。默认累计尝试预算 2，永久持久、重启不清零；每轮最多提交一次，无自动提高预算或定期清零。

SDK 的 send_message(True) 只证明找到平台并返回，不证明 QQ 送达。False 是明确的平台不存在，默认不重试；管理员若额外批准 retry_limit（最多 2），仍受累计预算和至少 30 秒间隔限制。异常/超时/取消/崩溃中断全部 uncertain，不自动补发。SQLite 先预留尝试再外部调用；损坏/不一致不删库重试，没有历史窗口淘汰。完整状态与崩溃窗口见插件 README。

503/解析错误不会推进水位或发送旧正文。仅首次有效非空 Feed 建历史；缺项/正文变更会阻断此前尚未完成项。事实批准仍由原 M2-C/M3-A 决定，插件不审核、不批准、不修改 Schema/标签或原 RSS GUID。

## 离线测试与限制

开发前基线 **493 passed in 58.67s**。新增 tests/test_cdu_radar_plugin.py 覆盖固定 URL/完整目标校验、默认关闭、无权限入口/工具注册、目标变更即时停发、非空初始化、重复/重启去重、跨重启预算、503 与无效 XML、失败有界重试、不确定状态、崩溃预留、损坏状态拒绝重建、停止任务清理，以及恶意正文不改变源或目标。

Mock SDK 缺少命令/模型注册接口；一旦代码试图访问这些入口，测试失败。对 Context 的观察是“投递请求”，不写成 QQ 消息成功。M3-A 合成 SQLite/FeedController 集成验证新增、源变更、失效 503 和恢复；没有读取正式通知作为夹具。Windows asyncio 的内部 socketpair 在专属夹具中获准建立自管道，实际 HTTP/其他外部 socket 仍由全局测试策略禁止。

本轮没有安装真实 AstrBot SDK 或连接生产；官方 4.25.5 API 源码静态核对与 Mock 不替代定制生产实例验收。全量最终结果、包哈希与本地资源测量见 [M3B4_RESULTS.md](M3B4_RESULTS.md)。

## 单 RSS 容器草稿

[compose.rss.draft.yaml](../deploy/m3b4/compose.rss.draft.yaml) 只包含一个常驻 rss 和一次性 init-synthetic：

- rss 共享后续明确批准的**现有 AstrBot 容器 ID**网络命名空间，不修改 Astro/NapCat Compose、连接或重启；不映射端口、不声明新 bridge。
- 两个全新专属卷分别放合成库与 RSS 状态；不挂 /AstrBot/data、QQ、配置、密钥或 Docker Socket。
- 复用已审查的 Python 3.12 RSS Dockerfile；非 root、只读 rootfs、cap_drop ALL、256 MiB/0.25 CPU、pids=128，restart=no。初始化 network_mode=none，拒绝覆盖既有 dataset。
- M3-A 安全服务只绑定 127.0.0.1:8765；源码/审批/时间失效和导出失败继续返回 503，无静态回退。

共享生产网络命名空间不是安全沙箱，RSS 代码可访问同命名空间环回服务，必须审查镜像/源码且无凭据挂载。现有 Astro 若被外部运维重建，先停 RSS 并重新核对，不自动跟随容器名或为恢复 RSS 重启生产。

不能直接运行草稿：先复核当前资源余量、确切容器 ID、8765 冲突、镜像架构/Python/SQLite/时区与非 root 卷权限，再确认该命名空间 GET /approved.xml。当前主机宿主 Python 3.10 不用于 RSS，不向生产 AstrBot 进程中塞 RSS 服务。

资源预算：RSS 预计 30–100 MiB 常驻、上限 256 MiB/0.25 CPU；构建和镜像暂预算 0.5–1.5 GiB。专用插件仅一个后台任务、一个小 HTTP 客户端与 SQLite；HTTP ≤512 KiB/200 条，消息≤4000字符，历史最多 50,000 GUID 后停发不清理。Linux 实际 RSS/插件增量内存未测；本地测量不能当作服务器配额保证。全量三容器预算不再适用。

## 生产影响、回滚及下一次精确审批

先批准准备以下项目，不能把本轮离线授权当作部署授权：

1. **生产只读部署前复核**：限定此前主机，确认资源余量、当前 Astro ID、8765 未占用、挂载目标、已有 httpx/API 兼容；不读取聊天/凭据、不修改服务。若不兼容或需新增全局依赖，停止另审，不自动 pip 或重启。
2. **RSS 构建/初始化/启动**：批准精确源码提交/镜像 digest、单 RSS 项目名、两个新卷、限额与 container 网络模式。只生成专属合成库和新状态，不读正式 36 条；先做无 QQ 的安全 HTTP/503 及 journal_mode/v5 验证。
3. **专用插件安装但默认禁用**：批准给确切新插件目录安装已审查 ZIP、创建专属配置/数据；使用指定目录加载，不触发 core 的未知名字 reload→全插件重载路径。已有模型、AIHOT、订阅和生产群配置保持。依赖/加载失败停止，不忽略版本或重启绕过。
4. **单会话启用与 2 次总尝试**：用户提供并核对唯一测试群或管理员私聊的完整 UMO；预算固定 2、retry_limit=0、非空种子初始化，再加第二条 1 次；如另批 503 恢复试验，只操作新 RSS 状态，在故障期间加第三条，恢复后最多再 1 次。没有 /sub、/sub_test 或命令确认回复。插件不调用模型，但不能由此保证既有其他业务后台全系统没有模型调用。
5. **停止/卸载授权一并明确**：异常时停止此新插件并等待任务取消，停此 RSS 容器；保留私有 ledger、尝试次数及不确定项供核验，不删/恢复旧库来凑预算。精确卸载本插件可移除其生命周期对象，无需恢复整份生产数据库；若管理器无法只处理该插件，停止另行审批，不能重启全机器人代替。

插件安装本身仍改变新的注册项/配置，并非生产零影响。没有 requirements 自动升级路径减小了风险，但现场依赖、SDK、网络回执和取消行为仍须确认。取消或 RSS 移除不能撤回已在 SDK/QQ 中的消息。正常重启保留去重，恢复旧快照/数据丢失可能导致重放；不承诺跨崩溃 exactly-once。

当前仅准备本地提交与 PR 更新说明，不推送、不更新生产、不自动合并 Draft PR #1。
