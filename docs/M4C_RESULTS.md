# M4-C 学生通知页验收

日期：2026-10-15，Asia/Shanghai。轻量网页与同源只读 JSON 已实现，无需 RSS 阅读器。当前只支持本机环回访问；不代表公网或学生跨设备试运行已验收。[运行及接口契约](STUDENT_PAGE.md)。

## Git 开工核对

实际工作树起点是 `codex/m4b-auto-publication` / `105e4a3`。M4-B 已独立提交，共 18 个源代码/文档/测试文件，没有待提交的 M4-B 修改，因此不重复提交或改写历史。核对三个 Feed 和已退役的 approved.xml；Git 未跟踪 data、artifacts、SQLite、日志或 .env。私有真实回放库继续被忽略。

仅有四个开工前未跟踪的旧草稿：deploy/m3b1/compose.admin.draft.yaml、deploy/m3b1/compose.draft.yaml、docs/M3B1_ISOLATION_DESIGN.md、scripts/validate_m3b1_design.py，均保留。M4-C 在新本地分支 `codex/m4c-student-notices` 开发，17 个交付文件留作本地未提交改动，没有移动 M4-A 或 M3-B 的分支/worktree，没有推送、创建或合并 PR。交付文件敏感内容扫描无命中，数据库、截图、日志和回放证据继续留在忽略目录。

## 改动清单

| 文件 | 变更 |
| --- | --- |
| cdu_radar/web/index.html、app.css、app.js、favicon.svg | 响应式通知页，最新/当前/历史、类别、状态、原文入口、正常空状态与故障清空 |
| cdu_radar/student_page.py | 静态资源白名单和 RSS 合格快照的公开字段投影 |
| cdu_radar/auto_service.py | 同源 JSON、剩余核验时限、离线模式标记；复用原响应门禁 |
| cdu_radar/rss_service.py | 固定静态路由与响应类型，安全响应头，处理 Windows 客户端取消请求 |
| cdu_radar/auto_publish.py | 行政分类边界、源检查时间、减少计划内重复目录读取与冗余计划计算 |
| pyproject.toml | wheel 包含四份网页资源，无新增运行依赖 |
| tests/test_student_page.py | 只读/路由/质量/一致性/行政误分类/剩余时限/动态白名单/Windows 取消连接回归 |
| scripts/preview_student_page.py | 明确标识的离线真实样本预览与隔离服务 |
| README、PLAN、ARCHITECTURE、AUTO_PUBLICATION、本文及 STUDENT_PAGE | 当前入口、数据契约、运行与验收说明 |

没有新增数据库结构、前端框架、Agent、账号、个人数据收集、管理写接口或第三方网页依赖。

## 分类与真实数据

实际复现“教师科研项目申报通知 + 未来报名截止”在 M4-B 被归为科研/current；后端现将明确行政通知归其他通知，不进入当前机会。补充竞赛经费报销、AI 岗位聘任、获奖推荐名单公示边界，避免前端文案掩盖分类错误。历史中的未知报名状态保持未知，不改为截止。

源数据来自 M4-A 的 real-notices.sqlite3，仍以 mode=ro + SQLite backup 回放原 parser 输出与采集时点。新隔离目录为 artifacts/m4c-page-final，参考时间 2026-10-15T20:00:00+08:00。输入 18 篇，自动聚合及网页 16，历史 11，当前 0，报名 unknown=15 / closed=1；教务处 pending 原文单位的 ID 17、18 仍不发布。原始通知和历史记录不改写，未生成任何真实通知补量。

页面在默认“最新通知”确实展示 16 篇，原文日期从 2023 至 2026，系统采集时点另标。当前机会 0 有正常提示；历史视图 11 篇中 10 篇仍为报名状态未确认，仅有明确截止的数学竞赛为报名已截止。

## 验证结果

| 验证 | 结果 |
| --- | --- |
| 新增学生页专项 | 42 项；与自动发布专项合跑 97 passed / 22.61s |
| 全量 pytest | 通过：644 passed / 92.15s |
| JS 语法 | 通过：node --check cdu_radar/web/app.js |
| Windows 原生 Python + 同源 HTTP | 通过，真实回放 16 条可浏览 |
| Linux 原生 | 未执行：本机 WSL 无发行版，没有为本轮安装环境 |
| 桌面与手机布局 | 通过：1528、1366、390、320 像素宽度检查，无横向溢出；手机为浏览器视口模拟，非真机 |
| 中文/长链接 | 通过：独立合成库 112 字标题、1091 字符 URL，未知发布日期和报名状态显示明确，摘要 script 文本不形成脚本节点 |
| 空分类/当前 0/合格数据为空 | 通过，均为正常空状态；不显示为服务错误 |
| RSS 503 → 页面 | 通过：仅隔离副本改为 WAL，feed.xml 与 api/notices 都为 503；网页旧卡片数量 0，时间清为 —；恢复回滚日志与原健康记录后重新展示 |
| 只读和路由 | 通过，JSON 与 RSS 同一 GUID/分类/状态集合；私有路径/查询变体 404，写方法 405，approved.xml 410 |
| 包资源 | 通过，离线构建 wheel 含 4 个 web 资源，不含数据库/日志/回放目录 |
| 依赖与差异检查 | pip check / node --check / git diff --check 通过 |
| 外部系统 | 未连接生产、AstrBot、NapCat、QQ 或付费模型，未新建生产服务 |

截图和私有验证证据在 artifacts/m4c-page-final/screenshots、result.json、page-acceptance.json。隔离健康记录在空状态测试后逐行还原；源库 SHA-256 仍为 4b6b1564cc87850b86d8a5f53be457a82504df142f1b8647c1f2574652327356，原始/历史表与回放副本逐行一致。合成边界库与真实样本分开，其服务结束后关闭；仅保留本机 8766 端口的真实回放预览供查看。浏览器未报告本页脚本错误；故障时的网络不可用有明确页面状态。

持续读取检查发现发布器重复加载目录与冗余计算会争用响应锁：修复前 80 次读取有 47 次短暂 503；减少重复工作后，同样 80 次读取为 0 次 503。记录分别为 http-timing.json / http-timing-final.json；这只是本机小样本，不代表并发 SLA。没有延长守门锁超时、弱化时效或使用旧静态 XML；新增“白名单变为 pending 后下一次读取即失效”回归保持通过。

构建时项目虚拟环境没有 setuptools.build_meta，首次 no-build-isolation wheel 检查失败；改用本机既有 setuptools 83.0.0 的 Python 离线构建成功，没有安装新依赖。该构建只验证资源打包，不当作 Linux 原生运行或生产部署验收。

## 小规模试运行判断

具备本机、小范围演示与阅读体验条件：普通同学打开页面即可浏览最新通知、辨认报名未知和历史、进入官方原文；当前机会为空不阻碍使用。尚不具备直接面向跨设备学生开放的验收结论：服务仍是环回地址，Linux 原生、托管方式、持续真实采集及当前原文连通性未在本轮复验。若继续试运行，下一步应先做受控非生产托管和真实采集稳定性验证，而不是增加账号或管理后台。
