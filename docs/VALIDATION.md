# M0 本机验证记录

验证日期：2026-10-13，Asia/Shanghai。环境：Windows PowerShell、Python 3.12.10、项目独立 `.venv`。

M0 的采集 → SQLite → RSS 最小闭环通过。已完整验证计算机学院及商学院两个真实官方栏目；**优先指定的创新创业学院未完成本机采集验证**。商学院作为第二种模板补充来源，不等同于对创新创业学院的验收。

## 自动化与环境

- `python -m pytest -q`：**49 passed in 1.96s**（提交前复验）。全部使用裁剪真实 HTML 或 MockTransport；自动夹具禁止 socket 联网。初次 4 个失败是 Mock 响应将 HTML 标成默认 text/plain，修正响应的真实 Content-Type 后通过，未降低生产验证标准。之后补充了畸形端口/URL 的错误隔离检查。
- `python -m pip check`：No broken requirements found。`pip install -e ".[dev]"` 成功，CLI 可运行。
- Wheel 构建通过；在 Python `-I` 隔离模式下从 wheel 导入包、读取 30 个打包来源条目和运行 sources CLI 通过。该检查复用当前虚拟环境依赖，不等同于 Linux 安装验收。
- 解析测试检查真实标题、完整 URL、发布日期和正文短段落；覆盖结构变化、空日期、错误日期、正文截止日期不混用、图片正文拒绝、中文 inline span 连接和未知站点。
- 请求测试检查 3 次有限超时尝试、服务端恢复重试、401/403/429 不重试、robots 禁止/失败缓存、间隔和 Crawl-delay、重定向域名及新站 robots 检查。
- 存储/CLI/RSS 测试覆盖来源失败隔离、幂等、内容哈希更新、首次发现时间保留、多来源关联、失败后保留旧记录、XML/中文/特殊字符、Feed 数量限制、未知日期和数据库防覆盖。

## 真实集成

首次：2026-10-13 19:35:21—19:35:39。计算机学院 inserted=3，商学院 inserted=3，均 failed=0。
重复：2026-10-13 19:41:32—19:42:06。两个成功来源均 inserted=0、updated=0、unchanged=3；创新创业学院 failed=1。包含失败来源的命令退出码为 1，正确保留其他来源成功结果。

```powershell
.\.venv\Scripts\python.exe -m cdu_radar collect --source computer --source business --limit 3 --no-env-proxy
.\.venv\Scripts\python.exe -m cdu_radar collect --source innovation --source computer --source business --limit 3 --no-env-proxy
.\.venv\Scripts\python.exe -m cdu_radar export-rss --limit 30
```

SQLite 共 6 篇唯一通知、6 条来源关联、5 次来源运行、23 条 HTTP 请求事件。`PRAGMA integrity_check` 为 `ok`。第二次采集的 `discovered_at`/`updated_at` 均保持首次值，仅 `last_seen_at` 改变；已逐条对照保存的真实正文页标题和哈希。

| 来源 | 原文 | 网页发布日期 | 可读正文字符数 |
|---|---|---|---|
| 商学院 | [【求职创业补贴公示】商学院2027届毕业生求职创业补贴拟推荐名单公示](https://bs.cdu.edu.cn/info/1041/13252.htm) | 2026-10-12 | 240 |
| 商学院 | [【评优评奖】商学院本科生国家奖学金候选人拟推荐名单公示(2025-2026学年)](https://bs.cdu.edu.cn/info/1041/13253.htm) | 2026-10-12 | 638 |
| 商学院 | [【评优评奖】成都大学商学院关于2025-2026学年研究生国家奖学金拟推荐人选的公示](https://bs.cdu.edu.cn/info/1041/13254.htm) | 2026-10-13 | 216 |
| 计算机学院 | [【通知公告】第十八届全国大学生数学竞赛成都大学报名](https://computer.cdu.edu.cn/info/1036/12337.htm) | 2026-09-14 | 736 |
| 计算机学院 | [我院学子在2026年（第19届）中国大学生计算机设计大赛斩获全国二等奖](https://computer.cdu.edu.cn/info/1036/12338.htm) | 2026-09-14 | 895 |
| 计算机学院 | [我院学子在全国大学生嵌入式芯片与系统设计竞赛中斩获全国三等奖](https://computer.cdu.edu.cn/info/1036/12341.htm) | 2026-09-14 | 722 |

数学竞赛通知发布日期为 2026-09-14，正文报名截止为 2026-09-17，二者明确分开；正文“我校在校全日制本科生”可读，截止字段仍为 NULL。商学院第一条通知发布日期为 2026-10-13，可读正文首段正确。未替官方修正正文内其他日期，也未把已过期通知视为当前可报名机会。

`data/feed.xml`：ElementTree 解析通过；feedparser 6.0.14 识别为 `rss20`，`bozo=False`，6 条条目，GUID 和链接唯一且与数据库完全一致。未知发布日期使用首次发现日期的路径由离线测试覆盖，本轮真实 6 篇均有网页日期。原文真实变更未人为触发；更新行为由离线存储测试验证。

本地数据：`data/radar.sqlite3`、`data/feed.xml`、`data/validation.json`（含标题、URL、哈希、各次运行结果）；均在 .gitignore 内，不进入版本控制。短暂保存的原始 HTML 已用于核对并制作裁剪样本，收尾后删除。

## 限制与未执行

- 创新创业学院本机网络连接失败，尚无经过真实 DOM 验证的解析器；远程网页工具能够阅读并不等于本机可采集。
- 全校其余目录条目仅登记官网链接，未完成栏目发现和适配；分页、增量游标、附件/OCR、赛事级去重、筛选及截止时间抽取未实现。
- 标准库 robots 解析器不是完整 RFC 9309 实现；复杂匹配规则需在扩展来源前验证。
- 请求超时为 HTTPX 各阶段 timeout，不是整个采集任务的总时限；当前 3 MiB 检查在响应读入后执行，未来可改流式大小限制。
- 未执行 Linux 服务器部署、AstrBot 或 QQ 群订阅。Feed 只生成本地文件。尚未选定对外开源许可证。

## Git

原目录为空、没有仓库；M0 验证结束时已初始化本地 main 仓库，项目文件尚未提交，没有远程配置，也没有推送。这是验证时的状态快照，后续提交状态以 Git 为准。数据、Feed、虚拟环境、构建和临时材料均被忽略。
