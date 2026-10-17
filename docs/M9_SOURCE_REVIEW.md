# M9 来源与公开入口核查（2026-10-17）

当前决策：**部署准备可以验收；真实通知暂不放行公网再分发。** 已有服务器按本机档案标注为腾讯云上海，已完成该机 Linux 隔离验收；CDU 服务尚未选定公开域名、主办主体与备案服务范围。既有其他服务的域名、证书或备案不能自动当作 CDU 入口已获准。

## 官方来源访问与转载核查

对现有 11 个 verified 栏目仅检查主页和 `/robots.txt`，共 22 次 GET，单次 timeout=8 秒、无自动重试、同一主机请求间隔 2.5 秒，不读取通知正文/附件、不更新业务库。随后对创新创业学院主页额外作 1 次代理连通诊断，仍 ConnectError；共 23 次脚本请求。网页搜索/官方文档查询另计，不混作原文采集成功。私有证据 `artifacts/m9-completion-20261017/source-policy.json`，时间 2026-10-17T13:31:12.910771+00:00。

| 目录 ID | 官方主机 | robots 实测 | 主页实测 | 公开再分发结论 |
| --- | --- | --- | --- | --- |
| `jztm` | [jztm.cdu.edu.cn](https://jztm.cdu.edu.cn/) | 200 HTML，非 robots 规则 | 200；有版权标记 | 未取得许可，不放行 |
| `ele` | [ele.cdu.edu.cn](https://ele.cdu.edu.cn/) | 200 HTML，非 robots 规则 | 200；未检出版权标记 | 未取得许可，不放行 |
| `computer` | [computer.cdu.edu.cn](https://computer.cdu.edu.cn/) | 200 HTML，非 robots 规则 | 200；未检出版权标记 | 未取得许可，不放行 |
| `pharm` | [pharm.cdu.edu.cn](https://pharm.cdu.edu.cn/) | 200 HTML，非 robots 规则 | 200；有版权标记 | 未取得许可，不放行 |
| `business` | [bs.cdu.edu.cn](https://bs.cdu.edu.cn/) | 200 HTML，非 robots 规则 | 200；有版权标记 | 未取得许可，不放行 |
| `sfxy` | [sfxy.cdu.edu.cn](https://sfxy.cdu.edu.cn/) | ConnectError | ConnectError | 未取得许可，不放行 |
| `msys` | [msys.cdu.edu.cn](https://msys.cdu.edu.cn/) | 200 HTML，非 robots 规则 | 200；有版权标记 | 未取得许可，不放行 |
| `ftva` | [ftva.cdu.edu.cn](https://ftva.cdu.edu.cn/) | 200 HTML，非 robots 规则 | 200；有版权标记 | 未取得许可，不放行 |
| `innovation` | [cxcyxy.cdu.edu.cn](https://cxcyxy.cdu.edu.cn/) | ConnectError | ConnectError | 未取得许可，不放行 |
| `department-xsc-column-148` | [xsc.cdu.edu.cn](https://xsc.cdu.edu.cn/) | ConnectError | ConnectError | 未取得许可，不放行 |
| `department-tuanwei-column-149` | [tuanwei.cdu.edu.cn](https://tuanwei.cdu.edu.cn/) | 200 HTML，非 robots 规则 | 200；有版权标记 | 未取得许可，不放行 |

8 个站点的 robots 路径返回 HTML，不能将 HTTP 200 当成有效 robots 或访问许可；另 3 个站点连接失败，没有绕过 TLS 校验。8 个主页可读取，其中 6 个检出版权标记、2 个未检出；简单文本检测和没有版权标记都不证明允许转载。没有得到任何来源的明确再分发许可。

搜索引擎对[创新创业学院官方主页](https://cxcyxy.cdu.edu.cn/index.htm)的索引出现“本站内容，图片资料未经许可请勿转载与使用”的提示；本轮直连/代理连接均失败，网页读取工具另有较旧页面，**未确认该提示的现行版本**。该来源按需取得许可处理，不能将搜索缓存或旧采集成功当作当前授权。未联系站方或代用户申请授权。

## 公开内容的处理决定

现有摘要是原文节选，公开投影不会自动屏蔽所有学生名单、学号、联系方式或敏感事项；模型输入脱敏不等于网页/RSS 输出已脱敏。本阶段不把未知授权改为允许，也不改动历史原文、可信来源目录或现有本地阅读语义。公开版本须逐来源确认摘录/标题/链接的可用范围，排除或另行取得限制来源许可；对拟发布样本进行人工内容核对后，才能写入具体公开版本的放行记录。当前 `public_listener_enabled=false`、采集/模型关闭、预算 0。

每条页面卡片和 RSS 保留原始官方链接与发布单位；本轮在页面首屏加入“非成都大学官方服务；内容来自各通知所示官方来源，完整要求以原文为准。”不使用校方身份/账号或宣称校方背书。标明非官方不能代替来源许可。

## 地区、域名和接入条件

服务器地域来自用户指定的本机服务器档案（上海），OS/资源来自 SSH 实测；本轮没有登录云控制台或用 IP 地理库推断备案地域。具体 CDU 域名与主办者尚未指定，域名归属、实名、接入与业务范围均未作已通过判断。准备阶段明确采用中国内地接入要求，不另购境外服务器，也不拿裸 IP 绕过公开入口核对。

核对依据为工信部《非经营性互联网信息服务备案管理办法》（2024 年修订）的第 5、7、13 条，以及腾讯云[备案云资源](https://cloud.tencent.cn/document/product/243/18908)、[备案限制说明](https://cloud.tencent.com/document/product/243/18911)。官方办法可由[工信部公开文件](https://www.miit.gov.cn/api-gateway/jpaas-web-server/front/document/file-download?fileName=f1b8d119f55240b4813dd39360d2e0b0.pdf&fileUrl=%2Fcms_files%2Ffilemanager%2F1226211233%2Fattach%2F20241%2Ff1b8d119f55240b4813dd39360d2e0b0.pdf)复核。此处只确定当前公开方案的核查前提，不判定具体主办主体必然适用某个专项许可。

真正上线阶段的输入为：CDU 使用的域名/主办主体、接入商确认的备案及业务范围、来源/公共摘要放行记录、证书与续期配置、明确上线授权。域名尚未指定是上线待办，**不是反复阻止本轮部署准备收尾的理由**。没有实施 DNS、备案提交、购买、申请证书或开放公网。
