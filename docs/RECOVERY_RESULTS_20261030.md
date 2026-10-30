# 正常复查误触发 503：rc4 修复与上线（2026-10-30）

`0.2.0rc4` 已于北京时间 **2026-10-30 15:50:22** 上线，服务 active/running，MainPID=4043937，自动失败重启数 0。用户授权提交、推送并更新服务器；代码提交为 `70e917a8b419eba3d5cbb21d8e0aa31809606036`，已推送到 origin/main。只切换本项目主服务和观察任务的运行环境；MaiBot、NapCat、new-api、workbuddy2api 的容器 ID、启动时间和重启数全部保持原值。

访问：[校园信息网页](https://122.51.203.116/cdu/)；[本科生 RSS](https://122.51.203.116/cdu/rss/v2/all.xml?audience=undergraduate)。

## 原因与修复

正常成功复查即使正文 unchanged，也会更新 `source_checked_at`。旧发布指纹包含该字段，HTTP 请求因此判定整份快照已失效，清空共享 snapshot，触发 source_changed_or_expired；后续请求因 snapshot=None 被统一记录成 validation_expired，直到后台刷新恢复。

rc3 期间取得的 6 条公网 503 与同秒的 source_changed_or_expired 均能对应。本机隔离复现中仅检查时间变化，快照年龄为 0 秒，也会依次让 RSS、health 和 API 返回 503。

- 发布指纹仅排除 `source_checked_at`；来源资格、健康状态/年龄、正文版本及排除项仍逐次校验。
- HTTP 从本次持锁原文读取事务生成最新检查时间的投影，使用请求自己的 Snapshot 副本，不延长 validated_at、不修改共享缓存或写库。
- 正常续期不改变精选集合 token、GUID、首次聚合及首次精选时间；正文变更、撤回、未来检查时间、72 小时超期和快照 30 秒有效期继续拦截旧响应及旧 304。
- 日志将 snapshot_missing 与真正的 validation_expired 分开。

## 验证及边界

| 检查 | 结果 |
| --- | --- |
| Windows 专项 | 120 passed，含 9 项新增回归。 |
| Windows 全量 | 1127 passed、2 skipped；跳过为当前会话不能创建符号链接。 |
| 服务器 Linux 全量 | 1128 passed、1 skipped，92.48 秒；跳过为 Windows DPAPI 专属测试。 |
| 远程 CI | 代码提交的 [Offline CI](https://github.com/A7m0spHere/cdu-opportunity-radar/actions/runs/41020352383) 和 [Dependency audit](https://github.com/A7m0spHere/cdu-opportunity-radar/actions/runs/41020352335) 均 success。 |
| 已安装发行包 | 67 个 Python 文件逐项与提交源码核对一致；pip check、安装包 smoke、依赖锁及 TLS/CA 检查通过。 |
| 隔离数据库副本并发验证 | 跟踪异常后的同范围验证为 40/40 HTTP 200、2 并发；21 次模拟健康续期和 84 次分析库写事务，写入错误 0、刷新失败 0、门控失败 0；P95 1.756 秒。随后模拟撤回，对旧 ETag 请求返回 503。没有官网、模型调用或群发送。 |
| 首次隔离混合写压测 | 40 次中有 2 次精选 503，记录为 response_validation_or_storage；未保留具体异常类别。增加异常跟踪后的复测未重现，不把首次失败改记成功，不宣称此历史泛化异常已完全归因。 |
| 上线环回检查 | health、全部/本科生 JSON、全部/精选本科生 RSS 5 条路线均 200。 |
| 公网检查 | 首页、health、本科生 JSON、全部/精选本科生 RSS 均取得正常 TLS 校验下的 200；其中多次首次 TLS 握手 EOF，重试成功。TLS 连接失败未记作 HTTP 503，也未被隐藏；没有修改 Nginx 或 TLS 配置。 |
| 首轮自然采集 | 新服务的 12 个来源自然轮次于 15:51:27 完成，没有新增/更新原文；学工原文有 1 条既有解析失败保留。15:52:46 发布器 ready、刷新 16 次、failures=0、gate_failures={}。 |
| 机器人 | 发送开关恢复 true，official_today_only 规则及既有水位保持；15:52:16 自然轮询 ready，100 条截断 Feed、queued=0。没有为验收发送 QQ 消息。 |

上述隔离及上线初期结果只证明对应窗口，不等于 rc4 已通过新的 48 小时观察或所有类型的 503、TLS 问题均消失。模型 HTTP 400、原文解析缺口和人工效果评测均未在本次修复中重置或冒充通过。

## 切换、数据与运行配置

先暂停校园 RSS 的 send_enabled，停止本项目观察定时器/任务及主服务，再保存一致 SQLite 备份。新服务就绪并完成接口和身份检查后恢复发送；没有直接改写消费者水位或群回执。

| 持久记录 | 切换前后 |
| --- | --- |
| 原文 GUID 与首次发布时间 | 203 条，既有行全部相同 |
| 模型调用账本 | 212 条，既有行全部相同 |
| 首次精选时间 | 66 条，既有行全部相同 |
| 机器人发送回执 | 5 条，既有行全部相同 |

新目录为 `/opt/cdu-radar/venv-0.2.0rc4`、`/opt/cdu-radar/release-0.2.0rc4`。只更新两个本项目 systemd 单元中的版本路径；独立用户、127.0.0.1:8765、每轮结束等待 7200 秒、每轮 48 次官网预算及既有资源限制保持。观察定时器继续 active。

实际模型管线仍为 `review-v3:0a5316a1bba9951b111d8d5593da8611`，旧分析结果无需因本次发布重算；没有运行补采或强制模型重试。上线后的正常采集及 worker 按既有服务配置自行运行，15:52 worker idle、handled=0、failures=0。

## 发行身份与回退

- wheel：`cdu_opportunity_radar-0.2.0rc4-py3-none-any.whl`。
- wheel SHA256：`10081b9224b1dd36a4feb0ac2aaa1f12dbc17062f706c14744ac07ca1f687f17`。
- 提交源码归档 SHA256：`fd6e963ae1568a004dbfec1b13e59c3f5856803984bce61c0c9b893f71173959`。
- 本批服务器私有备份：`/opt/cdu-radar/repair-backups/20261030-rc4/`，包含 owner/manifest、一致数据快照、原单元、原配置及身份与激活记录。
- 本机材料：`artifacts/deploy-rc4-20261030/`，保留安装包、测试/隔离失败与成功记录、激活状态、公网结果及脚本；不上传私有数据库、配置或原始日志到公共 Issue。

如需回退，先暂停校园发送并停止本项目写者，保存最新状态；仅恢复本批 `cdu-radar.service.before` 和 `cdu-radar-observe.service.before` 的 rc3 路径，再启动并核验后恢复发送。**不使用旧数据库覆盖本次上线后的原文、调用账本、首次时间、水位和回执。** 回退 rc3 会恢复正常复查触发 503 的旧逻辑。
