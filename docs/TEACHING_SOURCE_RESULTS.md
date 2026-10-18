# 教务处教务公告接入（2026-10-18）

教务处首页“通知公告”的“更多”指向 [教务公告](https://jw.cdu.edu.cn/jwgg.htm)。本次新增稳定来源 ID `department-jw-notices`，发布单位沿用 `department-jw`。学籍、选课、考试、毕业论文、教师教学等普通通知按栏目收录，不以 AI、竞赛关键词、个人兴趣或模型评分删去原文。

## 实现与范围

- `teaching` 列表只读取已观察的 `div.text-list > ul`，逐行解析标题、官方原文 URL 和 `span.span` 来源日期；原有正文模板继续复用。
- 翻页限定在该栏目 `/jwgg/<页码>.htm` 下，结构异常、无日期节点、非法日期和非文章路径显式失败。
- 只验证这个通知栏目，不把教务处的管理文件、办事流程、教学工作月报等候选自动放行，也不登录教务系统。
- 原始全文、发布日期、转载发现来源与 URL 去重继续由现有采集层管理；网页和 RSS 保留质量、撤回和核验时效检查。

## 本次有界实采

以本次会话先前的 15 篇试采库为基线，SQLite 只读备份到新隔离目录后运行；第一页最多尝试 6 篇，12 秒超时、无重试、请求间隔 2.5 秒。没有模型调用、没有下载附件。

| 项目 | 结果 |
| --- | --- |
| 首轮文章尝试 | 6 篇 |
| 新增独立原文 | 5 篇 |
| 无可提取正文 | 1 篇：置顶“教务系统登录指南”为嵌入式 PDF |
| 有效原文重复解析 | `unchanged`，未新增重复记录 |
| 既有教务转载复查 | 2 篇，重新联网解析后通过展示检查 |
| 扩展后隔离库 | 20 篇原文、20 篇合格展示，其中教务处 7 篇 |

新增公告包含新生学籍核对、毕业设计工作安排、教师教学活动等。PDF-only 指南记为解析失败，不伪造摘要，不把 HTTP 200 当作正文解析成功。采集可保留同轮其他有效公告；退出状态和失败记录仍提示该轮并非全部成功。

此前 13 篇展示增加至 20 篇，包含 5 篇本次新采原文和 2 篇原已存储、此次复查后通过的教务转载；不能把这 7 篇全部称作新增采集。小样本不代表教务处全历史覆盖或全校信息总量。

本地证据保留在被 Git 忽略的 `artifacts/teaching-trial-20261018/`：`result.json`、`before.json` / `before.md`、`after.json` / `after.md`、请求日志与私有响应。列表及正文裁剪样本、原始哈希在 `tests/fixtures/teaching/manifest.json`，不提交完整网页或名单。

## 验证结果

- 全量离线回归：`python -m pytest -q`，**986 passed, 2 skipped**；两项跳过均因 Windows 当前权限不允许创建符号链接。
- 本地 HTTP：`/health`、JSON v3、全部 RSS、历史 RSS 均返回 200；全量 JSON 与 RSS 均为 20 篇，`/api/notices/v3?source=department-jw` 为 7 篇。
- 内置浏览器确认发布单位可选“教务处（招生办公室）”，筛选显示 7 条，首条为新生学籍核对通知。
- `git diff --check` 通过；裁剪 HTML 的 SHA-256 与清单一致。此次验证不包含生产部署。

## 复现

在新的本地数据库执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m cdu_radar collect --db data/teaching.sqlite3 --source department-jw-notices --limit 6 --max-pages 1
.\.venv\Scripts\python.exe -X utf8 -m cdu_radar serve-rss --db data/teaching.sqlite3 --state-dir data/teaching-http --port 8767
```

如置顶 PDF-only 指南仍存在，采集会报告该条解析失败，同时保存其他有效正文；检查终端与运行记录，不能把整轮称作全成功。默认自动服务省略 `--source` 时，非零采集间隔将包含此已验证栏目；显式指定来源时可重复追加 `--source department-jw-notices`。

离线回归覆盖真实 DOM 的列表和正文、置顶来源日期、未知结构、日期错误、分页边界、非 AI 通知入库、去重、跨来源发现及撤销来源后的发布阻断。实际长期运行、全历史补采与 PDF 正文读取未执行。
