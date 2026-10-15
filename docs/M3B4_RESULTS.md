# M3-B.4 离线验收（2026-10-15）

选择专用最小插件，未裁剪/安装 RSSHub。代码位于 astrbot_plugin_cdu_radar，只使用固定管理员 URL 与完整目标，没有命令、Web API、LLM 工具、AIHOT/Provider 调用。默认关闭；默认累计投递尝试预算 2、重试 0，明确失败可以审批有限重试，不确定结果不自动补发。持久库独立于聊天、标签及人工发布记录。

## 实际测试

- 修改前基线：493 passed in 58.67s。
- 新增插件测试：48 项。覆盖权限入口、目标隔离、live config guard、初始化/重复/重启、永久 GUID/累计预算、503、无效/过大/压缩响应、失败重试、崩溃不确定、损坏库拒绝重建、取消清理及恶意正文。
- 最终全量 **541 passed in 66.36s**，包含原 493 项及新增 48 项；记录保存在本轮私有验收目录。
- 单 RSS Compose 草稿静态契约、pip check、diff 空白检查通过；没有 Docker CLI，不声称 Docker config、build 或 Linux 容器验收通过。
- M3-A 集成使用新合成 SQLite 与 FeedController，并以 HTTP 获取协议 Mock 对接消费器。源更新导致旧批准失效，503 不推进消费历史，恢复后新条目只产生一次 Mock 投递。没有使用真实历史通知作为测试消息。
- 既有 93 个 data 文件在最终全量测试前后 SHA-256 保持，未打开正式库写入，原标签/审批/GUID/模型记录保持。

Mock Context 只记录投递请求，不模拟真实 QQ 送达。AST 检查和缺少权限 API 的 Mock SDK 确认无聊天命令、订阅入口、LLM/Provider/Web API 调用；完整目标不匹配和停止后内部发送也被拒绝。禁用配置不创建客户端/去重库，原有插件文件/配置夹具保持不变。

## 本地资源测量

Windows Python 3.12 独立进程，仅载入消费器与 Mock：101 条合成 Feed、100 次轮询，4.790 秒墙钟/2.156 秒进程 CPU，1 次 Mock 投递请求，SQLite 32 KiB。含冷导入的进程峰值工作集 **34.01 MiB**，tracemalloc Python 峰值约 **4.26 MiB**。这是本地测量，不是 Linux 上现有生产 AstrBot 的增量，也不是 RSS 发布服务的内存实测。

RSS 容器预算仍为 30–100 MiB 常驻估算、cap 256 MiB/0.25 CPU；插件无需另起容器。生产运行时版本、httpx 是否现成可用、文件锁/取消、QQ 回执和真实增量尚未验证。没有为此次开发连接服务器。

## 可审查安装候选与剩余限制

最终安装候选为忽略目录 artifacts/m3b4-offline-v1/astrbot_plugin_cdu_radar-0.1.0-final.zip，只含六个白名单源文件，不含模型响应、状态库、目标配置或任何其他插件。SHA-256：`3f421b60c7d7cbf88d9b74ee784e4ec3ef8d4a5d363f7625e278dcfc16cb3d50`。没有上传/安装该 ZIP。

Context 返回 True 的状态名为 submitted，不是 delivered；超时或取消可能已进入网络。跨崩溃、底层平台重试、旧备份恢复仍存在重复/漏发窗口，不承诺 exactly-once。停止等待本插件拥有的任务结束，不遗弃重试/发送协程；已进入 SDK/QQ 的消息无法撤回。

没有后台自动提额、清库、改目标、跟随重定向或缓存正文补发。50,000 GUID 后拒绝继续处理，须人工规划；长消息/过大 Feed 拒绝而不裁掉事实。配置变化后须受控单插件加载，不能改文件后假定内存已停。

## 部署边界

服务器连接 0，生产插件安装/重载/启用 0，服务器容器创建 0，生产卷/网络修改 0，QQ 发送 0，付费 LLM 0，PR 合并 0。只生成本地候选包、草稿、测试与提交；具体授权单见 [M3B4_MINIMAL_PLUGIN.md](M3B4_MINIMAL_PLUGIN.md)。Draft PR #1 不自动更新或合并。
