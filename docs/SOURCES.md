# 信息源清单

清单依据 [成都大学官网院系设置](https://www.cdu.edu.cn/yxsz.htm)，本机于 **2026-10-13** 获取原始 HTML。共 30 个目录条目，包括学院、医学中心、中国-东盟艺术学院下属单位和国际部；不是 30 个均已独立接入的学院。仅核对目录链接，不意味着逐一访问了这些学院网站。

`verified`：真实栏目和正文已完成本机采集验证；`pending`：目录确认但未接入；`failed`：本机近期访问失败；`unsupported`：已确认暂无法自动采集（本轮未给任何条目使用这一状态）。最后验证时间只表示完整采集成功时间，待接入和失败来源留空；目录核对时间另存于 JSON 的 `directory_checked_at`。

以下是 2026-10-13 验证快照；运行后最新状态以 `python -m cdu_radar sources` 的 SQLite 结果为准。可维护配置为 `cdu_radar/sources.json`。

| 院系/单位 | 官网目录链接 | 已发现栏目 | 类型 | 采集状态 | 最后采集验证（北京时间） |
|---|---|---|---|---|---|
| 机械工程学院 | [官网](https://jxgc.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 建筑与土木工程学院 | [官网](https://jztm.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 电子信息与电气工程学院 | [官网](https://ele.cdu.edu.cn) | 待发现 | 待核实 | pending | — |
| 计算机学院 | [官网](https://computer.cdu.edu.cn) | [学科竞赛](https://computer.cdu.edu.cn/cxcy/xkjs.htm) | 学科竞赛（含竞赛新闻） | verified | 2026-10-13 19:41:40 |
| 药学院 | [官网](https://pharm.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 食品与生物工程学院 | [官网](https://bio.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 化学化工学院 | 待核实（目录链接为 #） | 待发现 | 待核实 | pending | — |
| 医学中心 | [官网](https://yxzx.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 基础医学院 | [官网](https://yhxy.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 临床医学院 | [官网](https://med.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 护理学院 | [官网](https://yhxy.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 应急管理学院 | [官网](https://yj.cdu.edu.cn) | 待发现 | 待核实 | pending | — |
| 马克思主义学院 | [官网](https://marx.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 文学与新闻传播学院 | [官网](https://wenxin.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 旅游与文化产业学院 | [官网](https://lvyou.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 商学院 | [官网](https://bs.cdu.edu.cn/) | [学生工作 / 通知公告](https://bs.cdu.edu.cn/xsgz2/tzgg.htm) | 学生工作通知（未筛选） | verified | 2026-10-13 19:41:50 |
| 法学院 | [官网](https://law.cdu.edu.cn) | 待发现 | 待核实 | pending | — |
| 师范学院 | [官网](https://sfxy.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 外国语学院 | [官网](https://dfl.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 体育学院 | [官网](https://tyxy.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 中国-东盟艺术学院 | [官网](https://ccaa.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 美术与设计学院 | [官网](https://msys.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 影视与动画学院 | [官网](https://ftva.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 音乐与舞蹈学院 | [官网](https://md.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 国际部 | [官网](https://idccaa.cdu.edu.cn) | 待发现 | 待核实 | pending | — |
| 张澜学院 | [官网](https://zhanglan.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 创新创业学院（技术转移学院） | [官网](https://cxcyxy.cdu.edu.cn/) | [学科竞赛](https://cxcyxy.cdu.edu.cn/jyjx/xkjs.htm) | 学科竞赛 | failed | — |
| 成都大学斯特灵学院 | [官网](https://stirling.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 海外教育学院 | [官网](https://coe.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |
| 继续教育学院 | [官网](https://jxjy2.cdu.edu.cn/) | 待发现 | 待核实 | pending | — |

## 本轮重点来源

- **计算机学院**：列表 `.div_list` 包含 `.div_title`、`.date` 和 `/info/1036/*.htm` 链接；正文在 VSB 文章表单及 `vsb_content_2` 中，标题不能取页面顶部学院名称。取得 3 篇正文，重复运行未新增。
- **创新创业学院（技术转移学院）**：远程网页阅读工具能读到指定学科竞赛栏目，并可见跨学院直链；但本机 HTTPX 和 curl 的 HTTPS 握手失败，内置浏览器为 `ERR_CONNECTION_CLOSED`。HTTP 普通访问也失败，未关闭证书校验或绕过访问控制。CLI robots 请求额外重试 2 次后仍为 `ConnectError`，停采；状态为 `failed`，适配器和最后成功验证时间均为空。**不能声称这两个优先指定来源均已通过。**
- **商学院**：从已确认的官网首页实际发现“学生工作 / 通知公告”链接，作为不同模板的补充验证来源。`ul.jtfylb > li` 中 `a > p` 为标题、`span` 为日期；正文使用 `vsb_content` 或 `vsb_content_4`。取得 3 篇正文，重复未新增。通知包含奖学金和补贴公示，当前仅用于原始通知技术验证。

两个可访问站点的 robots.txt 均为 HTTP 200 的 HTML 维护页，没有可解析的 User-agent 组；按无匹配规则处理并记录 `robots_no_rules` 警告，仍保持至少 2 秒间隔。后续规则发生变化时，每次运行都会重新检查。

基础医学院和护理学院在官网目录中共享 `https://yhxy.cdu.edu.cn/`，按官网原值记录，未推断为不同网站；化学化工学院没有可用官网链接。其余学院栏目 URL 均未猜测。

## 相关官方接口文档

- [HTTPX 客户端超时](https://www.python-httpx.org/advanced/timeouts/) 与 [MockTransport](https://www.python-httpx.org/advanced/transports/)
- [Beautiful Soup 文档](https://www.crummy.com/software/BeautifulSoup/bs4/doc/)
- [Python sqlite3](https://docs.python.org/3.12/library/sqlite3.html) 与 [RobotFileParser](https://docs.python.org/3.12/library/urllib.robotparser.html)
- [RSS 2.0 规范](https://www.rssboard.org/rss-specification) 与 [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html)
- [pytest tmp_path](https://docs.pytest.org/en/stable/how-to/tmp_path.html)
