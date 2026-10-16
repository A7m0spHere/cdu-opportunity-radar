# Issue #13 本地验收（2026-10-17）

在 main 分支完成参数化 RSS、本地页面订阅入口及离线回归；本记录描述本地验收，远端交付以 Git 提交记录为准，GitHub issue 状态单独维护。接口合同见 [RSS_V1.md](RSS_V1.md)。本轮使用独立合成库，不改用户原始数据、私有分析队列或人工历史。

| 项目 | 结果 | 证据与边界 |
| --- | --- | --- |
| 全量回归 | 通过 | `.venv/Scripts/python.exe -m pytest -q`：951 passed、2 skipped，382.91 秒；两项跳过为既有符号链接测试，Windows 当前权限不允许创建链接 |
| 新订阅回归 | 通过 | `tests/test_rss_v1.py` 50 项：严格参数、规范化、交集筛选、部分覆盖/空集合/首次未就绪、GET/HEAD/304、原文及分析变化、数据库/磁盘/锁/WAL 故障、稳定身份与首次时间、重启、字节/条目/速率/连接上限、环回代理与提交竞态 |
| 旧接口兼容 | 通过 | 全量覆盖旧 Feed、v1/v2/v3、LAN 代理、原文门禁及人工历史；调整旧代理断言以区分新精选未配置时的 503 |
| 标准解析与增量消费 | 通过 | feedparser RSS 2.0 + GUID 轮询消费者：首次 3 条、重复轮询新增 0、完成一条合成分析后新增 1，原有 GUID/pubDate 稳定；`artifacts/rss-v1-browser-check/subscriber-result.json`。这是本地解析/增量消费验证，不是第三方阅读器云服务或长期可用性验收 |
| 页面 | 通过 | Edge 实际浏览器验证精选/发布单位/历史条件对应链接、复制成功提示、切换条件清除旧复制状态、源故障清空链接/禁用复制及恢复；390×844 单列布局，无横向溢出，无页面脚本错误。`artifacts/rss-v1-browser-check/browser-result.json`、`desktop.png`、`mobile.png`；手机尺寸不等于真实手机 |
| wheel 构建与安装运行 | 通过 | `pip wheel . --no-deps` 后安装到本次独立临时 venv，复用当前开发环境的依赖目录，执行 `python -I scripts/ci_smoke.py`；实际从安装包读取模块、目录、页面资源、CLI 与新 RSS 的 200/400/503。wheel 和临时 venv 保留在 `artifacts/rss-v1-wheel-check/`；清理命令被自动审批以 `blocked by policy` 拒绝 |
| 依赖与差异检查 | 通过 | `pip check` 无损坏依赖；`git diff --check` 无空白错误 |
| 真实模型、学校采集、公网/LAN 新监听、生产部署 | 未执行 | 本轮均为 0；只使用自己的 127.0.0.1 临时测试服务，收尾关闭 |
| Linux 原生、真实手机、第三方 GUI 阅读器长期订阅、远端 CI | 未执行 | 原生设备与部署分别保留 #6/#14；远端 CI 须待后续交付触发，不以本地测试替代 |

wheel：`artifacts/rss-v1-wheel-check/wheel/cdu_opportunity_radar-0.1.0-py3-none-any.whl`，SHA-256：`0e8fc4c120d215d9a960e9c6e3dc7eb1595969c0f81562858f1a6ad705d70487`。

新增的 `<state-dir>/subscriptions.sqlite3` 需要和状态目录一起备份，以保留全局首次入选时间；不能把新建空 state-dir 称为保留订阅身份的迁移。未入选与尚未分析继续保留在全部信息中。未就绪/失效不返回旧 XML 或旧 304，部分分析覆盖在 RSS 中明确标注。

满连接测试曾在 Windows 观察到拒绝连接时未读请求导致 TCP reset；现先有界读取普通阅读器的小请求，再返回 503，定向及最终全量回归通过。初次无构建隔离的打包因环境未安装 setuptools 失败，改用项目既定的隔离构建流程后构建、安装检查通过。
