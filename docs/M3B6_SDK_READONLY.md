# M3-B.6 生产 SDK 只读核验

2026-10-15，Asia/Shanghai。**结论：已检查的 v0.1.1 SDK 接口兼容；生产安装门槛为 blocked。** 不能把接口匹配等同于加载隔离：现场单插件加载仍有全局命令同步，管理界面操作还会同步活跃沙箱；实际激活钩子集合和数据根目录覆盖未得到安全确认。本轮在只读检查后停止。

没有安装、导入或加载现场 AstrBot/插件模块，没有执行钩子、管理接口、OneBot 或 QQ 发送，没有读生产配置、环境变量、聊天数据库或日志。没有修改容器、应用文件、数据卷、权限或网络，也没有构建/启动 RSS、模型调用、推送或合并 Git。

## 现场身份、版本和文件证据

只连接此前获准的同一主机和现有 AstrBot；每次读取先检查主机/账号身份及确切容器名、running 状态。服务器只运行标准库的文件读取、AST、哈希及元数据查询，容器 Python 使用 `-B`，不执行应用模块。镜像标签查询最初因 Labels=null 的模板错误退出，修正只读模板后完成；没有权限异常或更换目标。

| 项目 | 本轮现场结果 |
| --- | --- |
| AstrBot 版本字面量 | `/AstrBot/astrbot/core/config/default.py` 为 4.25.5 |
| 镜像 RepoDigest | `soulter/astrbot@sha256:d7a377d458d6b61890c06f387cb1e8689a4c2936600c34b201e401103f25d5d6` |
| 镜像 ID | `sha256:613dcbce0de4581d2c67f8134a752104bfa715194f9e93772fd039474b9de19c` |
| Git/OCI revision | 没有取得可验证 checkout commit；OCI revision/version 标签为空，不能宣称整个镜像等于某上游提交 |
| 容器 Python / httpx | 3.12.13 / 0.28.1；httpx 只查安装元数据，未导入应用依赖 |
| 工作目录 | 容器 WorkingDir、探针 cwd、PID 1 cwd 为 `/AstrBot` |
| SDK 固定文件 | [21 文件清单](sdk-4255-source-manifest.json) 全部逐字节哈希匹配上游固定提交 `af70151ff82fbd035f8a93d912699400e15ea5e4` |
| 持久挂载 | `/AstrBot/data` 为可写 bind；六个源码覆盖为只读 bind，不保留宿主源路径 |
| 检查前后状态 | AstrBot running/healthy，镜像 ID 一致；没有据此宣称全部业务健康或 NapCat 进行了本轮复验 |

SDK 清单覆盖 api/star/event 导出、Star/base、注册器、PluginManager、Context、AstrBotConfig、StarTools、路径函数、MessageChain/Session/Type、OneBot adapter/event 等实际源码路径；不是用 `find_spec` 或导入应用定位。其他相关文件另保留哈希与脱敏 AST，未扩大“21 文件匹配”为全代码匹配。

六个覆盖目标均在 `/AstrBot/` 下，哈希与 M3-B.3 记录一致：

| 相对路径 | SHA-256 |
| --- | --- |
| `astrbot/core/provider/sources/openai_source.py` | `06cbe032ea9037e57196bdc6f245135ed85a7bc89c0fb63dfd0afaf368be84b4` |
| `astrbot/core/pipeline/scheduler.py` | `53a0e682866bd601f56b349f0a98d3db9735c08a83f06e8e4dd51cf7fa20b8c5` |
| `astrbot/core/knowledge_base/retrieval/rank_fusion.py` | `4b129f2efd053370b236db1aa3d2ec7b6c537afb2c6dcd81b65de8c1447ab1ef` |
| `astrbot/core/knowledge_base/retrieval/sparse_retriever.py` | `f2abf79568229ca5db8da4da2070e38fd50f4079e6545c988d70c3c85a8c6b1c` |
| `astrbot/core/knowledge_base/kb_helper.py` | `075fa392d4b710eadae155184b54576180d1688522975cc0957a51b371703851` |
| `astrbot/core/agent/context/compressor.py` | `e8909878f8fc306051e0ca6fccfbf1d16896371c2ec213e77d11b3c8489a1592` |

这些目标没有直接覆盖本插件使用的加载、卸载、配置或发送 SDK 文件。限定 AST 检查没有发现这六个模块顶层改写上述 SDK 属性，也没有命中所检查的插件生命周期/发送接口引用；compressor 有顶层 `logging.getLogger`。这只排除了已检查的直接覆盖/显式引用，不能排除进程运行时补丁、间接依赖或其他插件干预。

## SDK 接口契约

| 项目 | 现场源码确认 | 静态结论与运行缺口 |
| --- | --- | --- |
| 发现与初始化 | PluginManager 查插件目录的 main.py 或同名入口；支持指定目录/模块。Star 自动注册与 register 共用元数据；构造支持 context/config，随后 await initialize | v0.1.1 签名匹配；没有实际加载 |
| 配置 | `_conf_schema.json` 经 AstrBotConfig 构造，配置注入插件；缺失配置时 SDK 会生成默认文件 | 默认 enabled=false 不启动本插件消费器，但安装/加载本身仍会写配置、注册元数据和触发 SDK 公共路径 |
| 私有目录 | StarTools.get_data_dir 创建 data/plugin_data/插件名；路径函数支持 ASTRBOT_ROOT 和 desktop runtime 覆盖 | 没有调用会建目录的 API；虽然 cwd 和 data bind 已确认，未读环境，实际根目录覆盖仍需管理员确认。未证明专属目录权限可用 |
| 定时任务 | 插件自己管理 asyncio worker，不注册 SDK Cron；initialize/terminate 均为异步入口 | 签名兼容；实际事件循环、停止延迟和平台取消行为未运行验证 |
| 发送 | Context 解析完整 MessageSession，以平台 meta.id 精确选择，缺平台返回 False，找到后 await send_by_session 再返回 True | True 表示平台调用返回，不能作为 QQ 送达回执；未调用平台接口 |
| OneBot 目标 | 群会话底层会处理 session_id 下划线后缀；本插件限定纯数字会话，避免该转换改变目标 | 没有默认跨平台回退；真实 UMO 尚未绑定 |
| 取消/停用 | `_terminate_plugin` 优先类自己的 __del__，否则 await 类自己的 terminate；本插件只有后者；turn_off 先终止再写停用偏好 | 自有任务可独立请求停止；外部钩子/SDK 任务不属于本插件，不能保证全局无副作用 |
| 卸载 | uninstall 默认保留配置/数据；terminate 异常被捕获，仍可能继续解绑并删除源码 | 必须先确认精确停用成功，不能把源码删除当作任务停止 |

插件 v0.1.1 未发现必须修复的 API 签名或依赖版本不兼容。不修改服务器六个覆盖文件，也不为了安装而调整生产 SDK。

## 全局事件与公共路径副作用

下表是**现场源文件确认后的静态调用分析**，不是本轮调用或观察到的运行副作用。

| 入口 | 调用链和影响 |
| --- | --- |
| `PluginManager.load`，包括指定一个目录 | initialize 后取 OnPluginLoadedEvent 的全局 handlers 并逐个 await，异常记录后继续。注册表默认只返回 enabled 且所属插件 activated 的 handler；没有只通知新插件的限制，生命周期事件还被排除在普通 plugins_name 过滤之外 |
| `_terminate_plugin` / 精确停用 / 卸载 | 自有终止后通知所有符合条件的 OnPluginUnloadedEvent handlers。若自有 terminate 抛异常，后续通知不一定执行；不应假设钩子必然全部完成 |
| 任意 `load` 的末尾 | 遍历并移除 `logging.root.handlers` 的现有 handler；随后调用全局 `sync_command_configs()`。这不是只限新插件的清理 |
| `sync_command_configs` | 从全部 handler 收集命令，调用 `db_helper.get_command_configs()`；绑定已有配置时会改运行时 enabled、命令名和别名；存在 stale_configs 时调用 `db_helper.delete_command_configs(stale_configs)`。core 中 db_helper 绑定 SQLiteDatabase。本轮不打开数据库，也不执行这些方法，因此实际受影响条数未知 |
| Web 管理的安装、上传安装、启用、停用、重载、卸载 | 成功后 `_sync_skills_after_plugin_change()` 调用 `sync_skills_to_active_sandboxes()`，遍历全部 session_booter；不是只处理新插件 |
| 活跃沙箱 skills 同步 | booter.available 后 `_sync_skills_to_sandbox` 可创建/删除临时文件、复制目录/打 ZIP、执行 booter.shell.exec、upload_file、应用/扫描 skills 并更新缓存。是否有活跃沙箱、具体 booter 的网络/运行状态未读取，不能声称本机安装不会走这条路径 |
| `reload(未知名称)` / 无名称 reload | 名称未解析成指定模块时转为全量终止/解绑/清空注册表后 load 全部插件；可能影响 AIHOT 及所有其他插件。禁止用于探测新插件是否安装成功 |
| 主动发送的 SDK 基类路径 | OneBot adapter 发送后调用 Platform.send_by_session，后者 create_task(Metric.upload)。Metric 可累计/上传指标、创建 SDK 后台 flush task，条件下写安装标识；是否禁用未读取。这不是 LLM 调用，也不是本插件自有任务；实际发送测试不能宣称进程完全不产生其他 SDK 任务/网络 |

OnAstrBotLoadedEvent、OnPlatformLoadedEvent 与 OnPluginLoadedEvent 在现场注册器中为不同事件；不能把“单插件 load”说成必然触发整个 AstrBot 启动事件。全量 reload 则会重新执行各插件导入/initialize；没有本轮执行证据。

相关现场源码路径：`astrbot/core/star/star_manager.py`（load 898、事件 1231 起、公共清理 1260 后、reload 842、终止 1747）、`astrbot/core/star/star_handler.py`、`astrbot/core/star/register/star_handler.py`（369、386）、`astrbot/core/star/command_management.py`（48、434、451）、`astrbot/dashboard/routes/plugin.py`、`astrbot/core/computer/computer_client.py`（483、660）、`astrbot/core/platform/platform.py`、`astrbot/core/utils/metrics.py`。哈希和去掉全部字符串常量的 AST 在本地私有证据中。

### 已有插件的钩子盘点边界

仅从已确认目录的 SDK 可发现入口及相对源码导入链做 AST 检查：23 个用户插件候选、2 个 builtin 候选，526 个源文件哈希；未读取配置、聊天数据、日志或导出完整插件源码。无关插件使用别名，AIHOT 只辨认源码身份，未改动它。候选目录不等于运行时已激活插件集合。

在这个有限范围内，**没有发现显式的 on_plugin_loaded/on_plugin_unloaded 装饰器或对应 EventType 引用**；也检查了直接 from-import 别名。不能报告“现场已注册钩子为零”：五个候选存在动态代码/导入标记，其中一个触及每插件 180 源文件的上限；外部包、间接别名和运行时注册不能靠该检查完整证明。动态代码标记本身不证明其注册了钩子。

没有为了补齐结论而扩大扫描、执行动态代码、注入运行进程或读取偏好所在的聊天数据库。实际 enabled/activated 集合、动态注册回调及其副作用仍未验证。应由管理员提供现有只读管理视图或经单独审批的、明确不执行钩子的观察方案。此缺口与全局命令/沙箱路径共同构成安装阻断，不能用“没有搜到”消除。

## 安装条件、修复建议与回滚

**现在不具备已证明安全的生产安装条件。** 默认禁用只控制本插件，不会绕过 SDK 导入/配置、全局事件、命令同步及管理界面的沙箱同步。支持单目录 load，因此接口上无需重启机器人；但当前现成路径没有证明能跳过上述公共处理，不能推荐直接调用隐藏 Python 方法或注入在线进程来避开它们。

本地建议为：补充 SDK 契约审查中的全局命令、日志 handler、skills 同步及 SDK telemetry 影响说明/Mock 覆盖；先明确实际激活钩子和数据根目录。若要求安装绝不触及其他命令/沙箱，需要在隔离的固定 SDK 环境验证受支持的局部管理路径，或向上游提出范围限定能力。**仅改 CDU 插件不能阻止它 initialize 前后的 SDK 全局动作**。本轮不修改 SDK、插件或生产核心；没有把这些建议冒充已实现修复。

阻断解除后，最小安装方案仍须单独审批：

1. 固定 v0.1.1 ZIP 哈希及唯一新插件目录；确认已有默认 disabled、空 UMO，不升级依赖、不安装 RSSHub，不用未知名称 reload。
2. 经审查的确切单插件安装路径；必须明确接受/消除全局命令同步及可能的沙箱同步，保留其他业务配置。若路径需要全量重载或重启，停止。
3. 安装前只备份新插件将使用的专属配置、plugin_data 和必要的已批准回滚元数据；首次不存在则记录不存在，不复制整份聊天数据库。由于已发现全局路径，仅备份新插件目录不足以覆盖全局副作用；未解决前不安装。
4. 加载后保持 disabled，验证本插件零 HTTP worker、零发送、零 LLM/命令/Web 入口；不能用一条 QQ 测试替代验收。
5. 回滚先精确停用新插件并确认自有 worker/客户端/锁退出；不删除 ledger、不重置发送预算。停用异常则停止卸载并交由管理员处理。确认停止后才考虑保留配置/数据的卸载；不能用生产重启、还原整库或清空其他插件状态兜底。回滚入口本身也需审查全局通知/skills 同步。

未安装期间的回滚只有结束只读会话；本轮未留下服务器脚本或新应用文件。

## 验证结果与仍需运行验证的内容

本轮本地重新运行：插件专项 **54 passed in 7.78s**；固定 SDK 真实方法体、Mock 外部边界的 **12 项契约通过**。这次现场 21 文件哈希匹配进一步确认了所测源码身份，仍没有启动生产/完整 SDK。历史 **547 项全量通过**属于 M3-B.5，本轮没有改业务代码，未重复全量测试，不能写成本轮 547 项实跑。

候选 ZIP 六个白名单条目逐字节匹配当前源码，SHA-256 仍为 `7ec99f95512725e1f05e6fe4a5d0a31790d83e9cfb7281908319bf5a961812a3`。没有新包或版本升级。

仍未实测：完整框架加载、现场配置生效/私有目录权限、SDK 动态补丁、真实事件注册表、平台取消/错误返回、QQ 送达、启停延迟、全局命令同步影响条数、活跃沙箱同步和 SDK telemetry 状态。RSS 容器与网络连通性也没有进入本轮操作。

## 下一阶段的精确审批边界

当前只建议先补齐阻断证据，**不申请立即部署或发送**：

1. 管理员确认有效数据根目录、插件列表的激活状态；只提供脱敏管理视图，不含配置内容/聊天数据/凭据。若需要代理执行额外现场 GET/观察，先明确接口、字段、身份和不触发加载的证据，再单独批准。
2. 明确允许的安装入口，以及是否接受全局命令配置同步、root 日志 handler 清理和活跃沙箱 skills 同步；不能用“批准安装一个插件”隐含接受所有这些影响。无法限缩或确认时继续 blocked。
3. 上述门槛解除后，分别审批 RSS 构建/运行参数、默认 disabled 的精确 ZIP 安装及回滚、唯一 UMO 的启用、累计最多两次合成发送尝试。各阶段互不授权，RSS 构建、插件安装和发送现在均不执行。

## 证据与 Git

脱敏 JSON 仅在被忽略的 `artifacts/m3b6-sdk-readonly-v1/`：metadata.json（时间、镜像、21 文件/六覆盖哈希与挂载目标）、hooks-source-ast.json（限定源码图与注册表/公共路径 AST）、additional-callchains.json、final-callchains.json、sdk-contract-local.json、candidate-verification.json。没有凭据、头部、聊天数据、原始配置或完整生产插件源码；这些私有调查记录不自动提交。

分支 `codex/m3b4-minimal-rss-plugin`，起止 HEAD `2c1cbaf`。本轮只新增本报告，未改业务源码、安装包、正式数据库、人工标注、模型缓存或 RSS GUID；此前 M3-B.1 四个未跟踪草稿原样保留。没有本轮提交、推送、PR 修改或合并。
