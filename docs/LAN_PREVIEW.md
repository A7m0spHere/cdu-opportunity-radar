# M4-D 独立局域网预览方案

当前 `127.0.0.1` 只供服务所在电脑自身访问。M3-A/M4-B 的 `serve-rss` 继续只监听环回地址，不接受 `0.0.0.0` 或 LAN 地址。本轮新增的是独立只读转发命令 `serve-lan-preview`，没有修改核心监听边界，也没有实际启用 LAN 监听。

手机/其他电脑将访问独立预览端口；它只连接固定的本机 `127.0.0.1:<upstream-port>`，原 RSS 服务仍负责来源、版本、失效、撤回和时间检查。预览器没有数据库参数，不读取本地文件、不采集、不调用模型，也不缓存上次成功内容。

## 显式启用条件

- 必须指定电脑实际使用的 RFC1918 IPv4 地址，拒绝通配、环回、公网、链路本地和 IPv6 地址；不能用域名代替。
- 必须逐个指定 1–8 个允许访问的设备 IPv4，不接受网段或 `*`。建议首次仅允许本机私网 IP 和一部手机。
- 无参数不会启动；缺少 `--enable-lan` 会拒绝监听。`--check-only` 只校验参数和环回上游，不打开监听端口。
- CLI 只接受上游端口，目标地址固定为 `127.0.0.1`，不能指定任意 URL；不跟随重定向、不转发浏览器 Cookie/Authorization。
- `--check-only` 不证明该私网 IP 已配置在网卡上，也不证明手机、防火墙或 Wi-Fi AP 能互通。实际绑定和跨设备测试须由用户手动完成。

这是可信局域网中、公开通知的短时只读预览，没有登录或 TLS 身份认证，不是正式托管服务。本轮不改防火墙、路由器、NAT、公网端口或隧道，也不做生产部署。

## 只读路由与安全审查

| 检查点 | 实现与本机验证 |
| --- | --- |
| 暴露范围 | 固定 `/`、`/app.css`、`/app.js`、`/favicon.svg`、`/api/notices`、三个 Feed、`/health`；无文件目录映射 |
| 旧批准语义 | `/approved.xml` 410；不把自动通知标为人工审核 |
| 私有文件/管理路径 | SQLite、.env、配置、日志、owner.json、管理路径、查询变体、编码穿越和绝对 URL 均拒绝 |
| 写操作 | POST/PUT/PATCH/DELETE/OPTIONS 405；GET/HEAD 不转成写请求 |
| 设备边界 | 真实连接来源 IP 必须在精确白名单内；未列设备 403 |
| Host/跨站 | 只接受配置的字面 IP:端口；跨站数据读取被拒绝，无 CORS 开放头；仅允许外部链接顶层导航到无内嵌通知的公开首页 |
| 上游误配 | 每次响应须有 `X-CDU-Public-Contract: student-notices-v1`，且 MIME/JSON 公开字段契约匹配；端口换成其他本地服务时返回 503，不转发内容。此标记用于服务识别，不是认证密钥 |
| 失效保护 | 每次转发重新请求环回服务；上游失效/503/断开/超限均 503，旧 ETag 不能绕过；不读磁盘 XML、无静态兜底 |
| 缓存与页面 | no-store；JSON 不返回 304；有效期与网页错误清空规则沿用 M4-C，传输用时由前端保守扣除 |
| 资源界限 | 每响应最多 2 MiB，上游超时 3 秒，客户端超时 5 秒，最多 4 个同时转发；不记录访问 IP、请求头或正文 |

预览器保留 CSP、no-referrer、nosniff、same-origin 资源策略。官方原文链接由浏览器直接打开原站，不经过本地转发器。不暴露审核、标签、源配置和原始全文接口。

两跳传输不是跨进程原子事务；内容可能在一份已核验响应传输途中变化，已经发出的数据不能远程撤回。后续请求仍受上游门禁约束，网页在下一次轮询或有效期届满时隐藏旧内容，不承诺撤回瞬间所有客户端同步消失。

## 用户手动启动

以下命令未在真实 LAN 地址执行。示例 `192.168.1.10`、`192.168.1.50` 是占位值，请分别换成当前 Wi-Fi 下电脑、手机的实际 IPv4。Windows 可在本机网络设置或 `Get-NetIPAddress -AddressFamily IPv4` 中查看电脑地址；手机在该 Wi-Fi 的详情页查看。两台设备需在同一可信局域网。

命令在仓库根目录、当前 M4-D 工作树代码下执行；首次安装和虚拟环境沿用 README。

终端 A：先用当前代码启动核心环回服务。此处使用本轮新隔离采集库，不导入旧审批或回放数据。

```powershell
.\.venv\Scripts\python.exe -m cdu_radar serve-rss --db artifacts/m4d-live/notices.sqlite3 --state-dir artifacts/m4d-live/http --bind 127.0.0.1 --port 8765 --collect-interval 0
```

先打开 `http://127.0.0.1:8765/`，确认页面和 `/api/notices` 正常。若需重新做受控采集，先选择不存在的新目录运行下列命令，再将上面的 db/state-dir 改到对应新目录，不覆盖验收库：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/validate_trial_live.py --directory artifacts/trial-new
```

终端 B：先只检查配置，不监听 LAN。

```powershell
$trialPcIp = '192.168.1.10'
$trialPhoneIp = '192.168.1.50'
.\.venv\Scripts\python.exe -m cdu_radar serve-lan-preview --bind $trialPcIp --allow-client $trialPcIp --allow-client $trialPhoneIp --upstream-port 8765 --port 8780 --check-only
```

期望输出 `configuration_valid=true`、`upstream_ready=true`、`listener_opened=false`。上游不可用时先修复环回服务；不要改用静态文件服务器。

只有用户决定开始跨设备体验时，才手动执行：

```powershell
.\.venv\Scripts\python.exe -m cdu_radar serve-lan-preview --bind $trialPcIp --allow-client $trialPcIp --allow-client $trialPhoneIp --upstream-port 8765 --port 8780 --enable-lan
```

两个精确地址分别允许电脑自测和手机访问；其他设备即使同网也不在许可范围。此命令不更改操作系统防火墙。

## 用户手动验证与停止

1. 在电脑第三个终端执行 `Get-NetTCPConnection -LocalPort 8780 -State Listen`，应只显示指定的电脑私网地址，不能是 `0.0.0.0`。
2. 允许的手机和电脑分别打开 `http://<电脑私网IP>:8780/`。核对最新列表、当前机会 0、分类、未知报名状态、原文日期和系统采集时间。点击官方原文确认新窗口到达对应学院域名。
3. 在允许的电脑访问 `/api/notices`、`/feed.xml`，核对通知集合；访问 `/notices.sqlite3` 应 404，写请求应 405。未列设备应 403；不要为测试扩大为整个网段。
4. 可短暂停止终端 A，预览器应返回 503，网页旧通知消失；重启 A 后恢复。阅读器已自行缓存的旧 RSS 不受网页控制。
5. 完成后先在终端 B 按 Ctrl+C，再停止 A。再次执行 `Get-NetTCPConnection -LocalPort 8780 -State Listen -ErrorAction SilentlyContinue`，应无监听记录。不要删除数据库或状态锁文件来停止服务。

若防火墙/AP 客户端隔离导致无法连通，记录为未通过；本轮没有放行规则或路由修改，不改用通配绑定或公网隧道。

Linux 使用 `.venv/bin/python` 运行同样的子命令，监听可用 `ss -ltnp` 检查，停止仍为 Ctrl+C。本轮没有可用 Linux 运行环境，这些命令尚未获得 Linux 原生实测结论。

## 当前验收边界

本轮只在 `127.0.0.1` 绑定核心服务、转发器和空库测试服务，并用真实新采集数据完成浏览器检查。没有执行 `--enable-lan`，没有私网/公网监听，没有真手机跨设备连通记录。精确设备 ACL、路由、上游识别和故障传递已本机验证；它们不能替代用户之后的现场 Wi-Fi、防火墙和手机验证。[实际结果](M4D_RESULTS.md)。
