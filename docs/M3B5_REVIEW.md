# M3-B.5 交付审查与生产兼容性预检申请

日期：2026-10-15。只做本地审查、缺陷修复和离线验证。本轮没有 SSH、生产容器操作、真实 SDK 安装、QQ 消息或模型请求；未推送或创建/修改 PR。

**结论：v0.1.1 可作为下一次“生产只读 SDK 预检”的审查候选，不能据此直接安装、启用或发送。** 本插件权限入口审查通过；已有生产 SDK 的加载/卸载钩子、全部相关 API 文件及依赖尚未完成当前现场核验。以下五份申请不能合并成一次笼统部署批准。

## A. 两个已有提交与安装包

起点为 69d953a（插件/测试）和 8b9b028（部署草稿/文档）。保留原提交，不 squash、rebase 或重写；修复追加提交。M3-B.1 的四份旧三容器草稿保持未提交，不纳入本次功能。

审查起点的 0.1.0 ZIP 与当时六个源码文件逐字节一致，无多余/重复条目、路径穿越或符号链接；只含 __init__.py、main.py、engine.py、metadata.yaml、_conf_schema.json、README.md。两个提交不含 data、artifacts、数据库、历史响应、密钥或真实目标。旧包保留作为历史，不再用于新安装。

| 审查点 | 结果 |
| --- | --- |
| 默认禁用 | 不创建消费器、HTTP 客户端、去重库或发送任务；真实 SDK Schema 默认值也已离线执行验证 |
| 隐藏入口 | 没有命令/event handler、Web API、导入/重试接口、LLM 工具、模型 provider 或 AIHOT 调用；SDK 只注册插件元数据 |
| 精确目标 | 每次提交前核对完整 UMO 和配置快照；不允许通配、缺省平台、多个目标、正文改路由。数字 QQ ID 也避免 OneBot 对含下划线会话的规范化改变目标 |
| 预算 | 私有连接显式 synchronous=FULL，BEGIN IMMEDIATE 的提交在调用平台之前完成；另一只读连接可见 attempts=1、status=inflight；模拟提交失败没有外部调用 |
| 并发/重启 | 单消费者 poll_lock、跨进程 OS 锁、持久累计预算；12 个并发轮询仍只预留/提交 2 次，不因重启归零 |
| 中断 | v0.1.1 将取消被 SDK 吞掉、超过截止时间或授权失效后的返回均视为 uncertain，不自动重试 |
| 503/抓取/解析失败 | 不观察新的 GUID、不更新去重水位或预算，不使用缓存正文补发 |
| 自有清理 | 取消并等待本插件 worker、关闭客户端和 SQLite/OS 锁；没有脱离管理的重试任务。SDK 全局事件另见 B，不承诺外部钩子无影响 |

### 实际复现并修复的安全缺陷

底层发送协程若捕获取消后返回 True，0.1.0 会记录 submitted，worker 还会进入下一次休眠，延迟停用；回归用例实测旧版 **31.62 秒后失败**。v0.1.1 在停止/取消/超时后保守记录 uncertain，保持已预留预算；退出前跳过休眠。

回归还暴露 Windows 定时器粒度下仅比较单调时钟计时差不足以识别被吞掉的超时。现在用 asyncio.timeout 的 expired 状态判断，保留计时差兜底，并直接等待发送协程，不创建额外的提交 Task。停止用例验证及时返回；不响应任何取消的底层 SDK 仍会阻塞停止，不能强制撤回远端消息或承诺 exactly-once。

私有 ledger Schema 仍为 v1，字段/来源目标绑定/旧预算不变，不迁移或重置旧库。没有改原通知、模型缓存、人工标签、发布历史或 RSS GUID。

同时明确私有预算连接的刷盘级别，避免依赖 SQLite 运行时构建默认值；离线模拟初始 synchronous=OFF，验证会在写入前提升为 FULL。没有证据表明现场原本是 OFF，这是一项持久化契约加固，不改正式库或聊天库。

## B. SDK 验证层级

固定公开源码版本 v4.25.5，提交 **af70151ff82fbd035f8a93d912699400e15ea5e4**。[哈希清单](sdk-4255-source-manifest.json) 固定 21 个文件；源码只保存在忽略的 artifacts/m3b5-audit-v1/sdk-upstream，不随插件 ZIP 发布。

[validate_cdu_sdk_source.py](../scripts/validate_cdu_sdk_source.py) 不联网、不安装 SDK，先核对文件哈希，再编译/执行选定的真实 SDK 方法体。外部平台、组件序列化边界、全局事件注册表、偏好存储和删除动作使用 Mock，配置/状态只写临时目录；未启动完整框架。这比只按版本号或 Fake SDK 签名判断更具体，仍不能替代生产验收。

| API | 离线验证 | 现场证据/缺口 |
| --- | --- | --- |
| Star / register | 实际 Star 自动注册和旧 register 共用一个元数据项；构造器接受 context/config；两个未使用 mixin 无构造/注册钩子 | 现场 API 文件当前哈希未重新获取 |
| load / initialize | 默认配置由实际 AstrBotConfig Schema 生成并读回，禁用时不轮询；加载方法静态检查支持按指定目录加载 | M3-B.3 快照的 star_manager 全文件哈希与本次固定源码一致，reload/install 两种方法 AST 也一致；不是本轮现场再次核实 |
| 调度 | 使用 asyncio 自有 worker，不注册 SDK Cron 或全局调度器任务 | SDK 真实事件循环及定制环境的运行效果未测 |
| MessageChain / MessageSession / Context | 真实文本链方法、会话解析、Context.send_message 调用 Mock 平台；只有精确目标被请求，平台缺失返回 False，没有默认回退 | 未调用 OneBot/NapCat；True 仅为平台调用返回，不能当作 QQ 送达 |
| StarTools.get_data_dir | 真实方法在临时 data/plugin_data/astrbot_plugin_cdu_radar 创建专属目录 | 现场路径覆盖、持久挂载和权限尚待只读确认；预检不得实际调用会建目录的方法 |
| Config | 真实 Schema/配置文件加载、保存及 dict 行为验证 | 管理界面保存是否正确停止/重载此确切插件仍未现场操作；不把改磁盘等同于改运行时对象 |
| 停用/卸载 | 真实 _terminate_plugin、turn_off_plugin、uninstall_plugin 方法执行；自己的 worker 清理、无关 tool/preference 项保留，卸载默认 delete_config=false / delete_data=false | SDK 会触发全局卸载事件，现场已激活的钩子未知 |

源码级依据：[Context](https://github.com/AstrBotDevs/AstrBot/blob/af70151ff82fbd035f8a93d912699400e15ea5e4/astrbot/core/star/context.py)、[StarTools](https://github.com/AstrBotDevs/AstrBot/blob/af70151ff82fbd035f8a93d912699400e15ea5e4/astrbot/core/star/star_tools.py)、[PluginManager](https://github.com/AstrBotDevs/AstrBot/blob/af70151ff82fbd035f8a93d912699400e15ea5e4/astrbot/core/star/star_manager.py)。

### 必须保留的生产门槛

SDK load 在 initialize 后通知所有 OnPluginLoadedEvent，_terminate_plugin 通知 OnPluginUnloadedEvent。离线用合成既有钩子证实会收到通知，因此“只操作一个插件”不等于“其他插件不被通知”。本轮没有读取或执行生产钩子，不能证明它们不会调用模型或产生其他副作用。

此外 uninstall_plugin 会捕获 terminate 错误并继续解绑/移除源码。回滚必须先对已注册的确切插件执行停用并核实清理成功，再考虑卸载；清理异常时不能把卸载完成当成停止成功。未知名字的 reload 可能重载全部插件，禁止用它探测安装是否成功。

## C. 单 RSS 容器审查

[既有草稿](../deploy/m3b4/compose.rss.draft.yaml) 静态检查通过：256m、0.25 CPU、非 root 10001:10001、只读 rootfs、cap_drop ALL、无 Docker Socket/生产数据挂载、仅新 synthetic/state 卷、无 ports、无新 bridge、restart=no。一次性合成初始化无网络，拒绝覆盖 dataset。Dockerfile 和 .dockerignore 只放行必要 Radar 包，不发送私有数据或本插件到 RSS 镜像。

RSS 命令仍为 M3-A serve-approved-rss，绑定 127.0.0.1:8765；有效性检查失败返回 503，草稿没有代理或静态 XML 回退。源码/授权/到期保护未修改。未执行 Docker config/build/up，未做实际 Linux 镜像或 256 MiB 限额验证。

共享现有 AstrBot 的网络命名空间意味着同一环回可达；不共享进程或文件卷，也不是独立安全网络。新进程可以访问该命名空间其他环回服务，必须限定审查过的镜像、不挂凭据、非特权运行。变量要求明确的现有容器 ID；上线前必须验证其为获准运行中的 AstrBot，不用名字自动跟随其他实例。

8765 占用时应停止，不能自动换端口、终止占用者或改为公网绑定。RSS 停止后插件拒绝连接/503 不更新历史；恢复只用原卷重建有效 Feed，不能重新 init 或给旧内容换 GUID。Astro 被其他运维重建后，旧 RSS 可能留在旧命名空间；需重新只读核对并另批仅 RSS 的重建，不能为它重启生产 Astro。

运行时 memory/cpu 限额不约束镜像构建峰值。优先在另行批准的本地/构建机产出固定 linux/amd64 镜像，再批准服务器导入/拉取；不要将“容器只用 256 MiB”误当作生产 pip/build 也有相同上限。当前本机无 Docker CLI、服务器容量是先前快照，尚不具备真实可执行部署参数。

## D. Git 交付计划（需确认后才执行远程操作）

GitHub 只读核实：Draft PR #1 仍 OPEN/DRAFT，base main 954e914，head codex/m3a-safe-rss-http 的 2217942。本分支 codex/m3b4-minimal-rss-plugin 保留 69d953a、8b9b028，追加本轮修复及审查提交，不改变 main、不整合旧三容器草稿。

建议用户确认后才：推送本分支，创建**独立 Draft 后续 PR**，base=codex/m3a-safe-rss-http，head=codex/m3b4-minimal-rss-plugin。该增量只包含最小插件/测试/构建草稿和审查文档，依赖 #1；不把插件直接合入 main，也不改 #1 的 head。#1 后续合并后再经确认调整后续 PR base。具体 diff 统计及拟用正文保存在本轮本地 PR 准备文件；本轮不发起确认后的操作。

## E. 五份独立操作申请

### 申请 1：生产只读 SDK 兼容性检查（当前唯一建议的下一步）

**范围**：只在此前确认主机、现有 AstrBot 容器查询 Python/httpx 的版本元数据、容器 ID/镜像 ID/WorkingDir、必要的持久挂载、当前内存与 Docker 可用空间、8765 是否监听。按哈希清单的 21 条固定 SDK 源码路径和已知六条覆盖文件读取 SHA-256；必要时仅提取注册/加载/终止方法签名及 AST，不导入 AstrBot/插件或调用 StarTools（会建目录）。不读源码以外的真实配置、聊天 DB、日志、Token 或完整 Env。

**额外只读门槛**：只解析现有插件 main.py 中 OnPluginLoaded/OnPluginUnloaded 的装饰器声明和相应回调的调用 AST，输出插件/方法别名、风险类别及哈希，不返回字符串常量、不导入或执行。实际激活钩子集合由管理员提供只读管理视图的脱敏确认；静态扫描不能替代激活状态。如果无法安全核实就保留阻断，不用读聊天 DB/注入运行代码来取注册表。数据根目录若受环境覆盖，先由管理员确认与持久挂载一致，不擅自读凭据/环境推断。

**停止条件**：目标/权限异常、文件哈希或 API 与预期不一致、httpx 缺失/不兼容、未确认钩子副作用、根目录不明确，立即停止相关项，不修生产。哈希不一致不直接判错，先另行审查差异。

**验收**：脱敏 SDK 哈希/签名对照、依赖版本、持久目录证明及加载/卸载钩子安全结论；不产生目录或配置。**回滚**：无应用变更，仅结束会话。

### 申请 2：RSS 镜像构建/导入与独立容器启动

**前置/范围**：申请 1 通过；审批精确源码提交、基础镜像与成品 digest、构建位置、容量、唯一 Astro 容器 ID、256 MiB/0.25 CPU、一个常驻 RSS 与一次性初始化容器、两个全新卷。镜像缺失或参数不明停止，不自动 build/pull。先新合成库、只读核验 Radar v5/回滚 journal，再从正确命名空间读合成 Feed；不安装插件或发送。

**停止/验收**：端口冲突、资源不足、UID/时区/SQLite/架构不兼容、绑定错误或 503 被回退覆盖即止；验收隔离卷、无新增公网端口、200/503/恢复、GUID 稳定。**回滚**：仅停止新增 RSS/初始化容器，保留卷，绝不静态发布旧 XML或动 Astro/NapCat。

### 申请 3：插件默认禁用安装

**前置/范围**：申请 1 的真实 SDK/钩子门槛和申请 2 均通过；审批 v0.1.1 ZIP SHA-256、确切新增插件目录及专属默认配置（enabled=false、目标为空）。通过指定目录加载，不忽略版本、不 pip 升级依赖、不触发全量 reload，不修改六个覆盖文件、AIHOT 或其他业务配置。

**停止/验收**：加载/钩子/依赖异常、出现工具/命令/发送任务或需要重启即止；验收注册元数据、零 LLM/命令/Web 入口、零轮询/发送，记录配置来源。默认禁用不代表 SDK 不发加载事件，钩子未知不安装。

**回滚**：先精确停用新插件并确认任务/HTTP/锁已关闭，再经授权卸载源码，delete_config=false、delete_data=false。停用失败则停止卸载，申请人工处理，不恢复整份生产 DB 或重启。

### 申请 4：启用插件并绑定唯一测试会话

**前置/范围**：申请 3 通过；用户明确唯一 UMO（管理员私聊或独立测试群），仅改本插件配置：固定环回 URL、该完整 UMO、enabled=true、max_send_attempts=2、retry_limit=0。只含一条种子的新合成 Feed，使用已验证的确切单插件配置加载路径；不修改既有会话/订阅。

**停止/验收**：UMO、配置对象生效、私有 state 路径或来源绑定不明即止；验收非空初始化、累计 attempts=0、业务发送 0、无新增权限入口。**回滚**：精确停用该新插件，保留 ledger，不能只改磁盘文件便认定内存已停。

启用前还须确认私有 ledger 不存在（首次初始化），或经过只读审计仅包含该种子的 baseline、attempts=0 且没有 pending/failed/inflight；不满足就停止，不能删库达标。此阶段 Feed 固定为已核验的唯一种子，禁止新增通知，任何未知 GUID 出现即停；申请 5 未批前不能以“已启用”为由添加测试消息。

### 申请 5：最多两次合成发送尝试

**前置/范围**：申请 4 已证明种子登记、零发送。批准第 2 条正常轮询 1 次；如同时批准 503 恢复试验，仅故障注入新 RSS 私有输出路径，失败期间加第 3 条，恢复最多再 1 次。累计预算含失败和不确定，重启不清零，不用 /sub_test、不自动提额/补测或重试 QQ 断线。

**停止/验收**：任何错误目标、重复、异常回执、不确定结果、模型调用或清理异常立即停止；验收按 GUID 对照 Mock 以外的真实测试会话观察、每次尝试的 ledger 状态、预算≤2，重复轮询无新增请求。True 不是送达证据，不承诺 exactly-once。

**回滚**：先停此插件并确认自有任务已结束，再停新 RSS；保留状态和已消费预算/uncertain 证据，不清历史或重新 init，已发 QQ 不可由 RSS 撤回。生产重启、其他群、其他账号和任何新增尝试均需另批。

## 本轮结果与可复现入口

回归测试包括新增并发、跨连接持久化、提交失败拒发、取消吞掉、超时吞掉；最终全量输出与新 ZIP 哈希在本轮 artifacts/m3b5-audit-v1 中。运行 `python scripts/validate_cdu_sdk_source.py --source-root <固定哈希源码目录> --output <新结果文件>` 可重做 12 项 SDK 源码契约检查，无自动下载/安装/服务器连接。源码目录须来自清单固定 commit，缺失或哈希不符即拒绝执行。

最终全量 **547 passed in 68.90s**；原有 541 项保留，新增 6 项，插件专项 54 项。12 项 SDK 源码契约和单 RSS 草稿静态检查通过，pip check / diff 检查通过。93 个既有 data 文件在本轮前后哈希一致。

新候选：artifacts/m3b5-audit-v1/astrbot_plugin_cdu_radar-0.1.1-final.zip，SHA-256 `7ec99f95512725e1f05e6fe4a5d0a31790d83e9cfb7281908319bf5a961812a3`，六个白名单条目逐字节对应当前源码，无状态库或真实配置。0.1.0 原候选及其原哈希保留，不能将新旧包混作同一验收结果。

SDK 方法体验证不启动完整 AstrBot，不运行真实 OneBot 或生产全局钩子；先前的现场 star_manager 快照与上游一致仅缩小了未知范围，不等于当前 21 个 SDK 文件都匹配。最终仍须由用户决定是否批准申请 1。
