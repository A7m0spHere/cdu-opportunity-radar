# M3-B：真实测试群联调前检查（2026-10-15）

本轮完成分支审查、离线预检和现场操作准备。**未登录服务器、未安装插件、未重启实际机器人、未发送 QQ 消息、未修改正式数据库、未改变网络。真实闭环尚未验收，不具备进入 M3-C 的现场证据。** 下列服务器命令均未执行，需在明确授权后由管理员执行；本地资源目录可读不等于授权登录生产。

## 分支审查与合并风险

比较基线 main `954e914d4ff98a02901c13fc26ff4dcf3a260200`，M3-A 实现提交 `0e37c07` / `e2ec75d`。Git fetch 两次网络失败，改用 GitHub API 核实远程 main 和分支 SHA 与本地一致；未关闭 TLS 校验。`git merge-tree --write-tree origin/main HEAD` 无冲突，不修改 main。交付 PR 保持 Draft，不启用自动合并。

- 产品改动为两个新模块和 CLI 子命令；未改采集、模型协议、审批契约或数据库迁移。保留 main 的原文明示截止防护与损坏参考缓存隔离。
- 审查 reader / writer / read_gate / refresh、持久 GUID/pubDate、单写者锁及合成库标识。HTTP 只读、后台只导出有效批准；变更先阻断旧 200/304，后台成功重建才恢复；磁盘 XML 不作为 HTTP 回退。
- 保留 493 项全量测试，2026-10-15 重跑 **493 passed in 62.20s**，`pip check` 通过。系统 `python` 无 pytest，实际使用 `.venv/Scripts/python.exe`（3.12.10，SQLite 3.49.1）。未增加产品功能或重构。
- 修正交接文档残留的“静态目录发布”步骤和 README 对自动刷新权限的旧描述。禁止代理、静态服务绕过 503。
- 当前未发现代码合并阻断项；这只表示适合代码评审，不能当作 Linux、安装插件或测试群验收。仓库没有现成 CI 工作流，不能把本地结果写成远程 CI 通过。

运行风险：只支持本地 SQLite 回滚日志 delete/truncate/persist、v5；WAL 被拒绝。HTTP 验证与发送期间持有读锁，大量或缓慢请求会影响写入且可能保守 503；本轮无负载 SLA。Linux flock/systemd、实际 Python/SQLite、磁盘权限和时间同步尚待现场验证。已在传输中或进入插件发送队列的消息无法因撤销追回。

### 本轮实际本地预检

使用全新 `artifacts/m3b-preflight-762efe54/lab` 合成库和随机环回端口，保存 19 份 HTTP 状态/安全响应头/响应正文及 GUID/pubDate 清单，记录在忽略的 `artifacts/m3b-preflight-762efe54/result.json`。原有 93 个 data 文件 SHA-256 全部保持；没有向 Git 加入数据库、日志或私有环境资料。

实际验证：非空初始化；第二条新增；三次不变 ETag 返回 304；HTTP 服务与持久 Mock 历史重启，GUID/pubDate 不变；同秒两条新增；原文更新失效及重批同身份；撤销及 FakeClock 到期；注入导出 OSError 得 503（包括旧 ETag）；失败期间新条目不推进 Mock 历史、恢复后处理一次。Mock 共 4 个投递事件，仅为本地数组，**不是实际插件或 QQ 消息**。实际 QQ 发送尝试 0，群观察结果未知，Linux 未验证。预检结束服务已关闭。

## 资源调查：档案与现场分开

用户指定的本地服务器档案记录 Docker 26 / Compose v2、AstrBot `soulter/astrbot@v4.25.5`、宿主 `/opt/astrbot/data` 对应容器 `/AstrBot/data`，NapCat 反向 WS 指向 `astrbot:6199`。档案还记录定制核心补丁、生产机器人和容器可写层依赖，不能为了测试擅自重建、升级或重启它们。**以上均为历史线索，当前版本、网络模式、在线状态未经远程核验。** 没有取得已安装 RSSHub 的现场版本信息；不能因档案没列出就断言未安装。

只读复核的上游固定版本为 RSSHub `a047f46e47cb2a0f7a795f30b9f82ae027005846`：[metadata](https://github.com/AstrBot-Elementary-School/astrbot_plugin_rsshub/blob/a047f46e47cb2a0f7a795f30b9f82ae027005846/metadata.yaml) 声明 v2.1.4、AstrBot >=4.24.0。这不是目标机器已安装版本，也不能仅凭版本范围认定兼容定制环境。安装源码版本不同或有修改时重新审查；本轮不下载/安装运行插件。

## 授权后第一步：只读环境调查

以下在已获准的 Linux 终端执行，不含 SSH 凭据，不访问模型或 OneBot 发送接口。先记录系统；不读取完整环境变量、compose 配置或敏感请求头：

```bash
date -Is
uname -srm
python3 -c 'import sys,sqlite3; print(sys.version.split()[0]); print(sqlite3.sqlite_version)'
timedatectl show -p Timezone -p NTPSynchronized
docker version --format '{{.Server.Version}}'
docker compose version
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
ss -lnt
```

实际选择测试容器名，不能把下面占位自动替换成生产 `astrbot`：

```bash
BOT='test-astrbot' # 先确认它确为独立测试实例
docker inspect --format '{{.Name}} network={{.HostConfig.NetworkMode}} image={{.Config.Image}} image_id={{.Image}} status={{.State.Status}}' "$BOT"
docker inspect --format '{{range $n,$v := .NetworkSettings.Networks}}{{$n}} ip={{$v.IPAddress}} gateway={{$v.Gateway}} {{end}}' "$BOT"
docker inspect --format '{{range .Mounts}}{{.Type}} destination={{.Destination}} rw={{.RW}} {{end}}' "$BOT"
docker port "$BOT"
docker exec "$BOT" python -c 'import sys,sqlite3; print(sys.version.split()[0]); print(sqlite3.sqlite_version)'
```

非容器部署改查 `systemctl show <已确认的测试单元> -p ActiveState -p MainPID`，不要输出可能含凭据的 ExecStart。对已经确认的 NapCat 容器同样只检查状态/网络。WS TCP 建连、AstrBot 平台“已连接”、NapCat 在线及会话对应关系应分别确认；它们都不等于消息发送成功。不调用 `send_group_msg`、`/sub_test` 或登录/重启操作来做连通性探针。

核实插件目录、安装版本、已修改源码及持久数据挂载。以下只输出 RSS 插件 metadata 的白名单行与关键源码哈希，不加载插件、不读取配置密钥或聊天数据库：

```bash
docker exec -i "$BOT" python - <<'PY'
from pathlib import Path
import hashlib, os, shutil, subprocess
root = Path('/AstrBot/data/plugins') # 必须按现场挂载确认，不存在时停止
if not root.is_dir(): raise SystemExit('Confirm the actual plugin directory first')
for p in sorted(root.iterdir()):
    if not p.is_dir() or 'rss' not in p.name.lower(): continue
    print('plugin', p.name)
    meta = p / 'metadata.yaml'
    if meta.is_file():
        for line in meta.read_text('utf-8-sig').splitlines():
            if line.startswith(('name:', 'version:', 'astrbot_version:')): print(line)
    if (p / '.git').exists() and shutil.which('git'):
        env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
        sha = subprocess.run(['git','-C',str(p),'rev-parse','HEAD'],env=env,capture_output=True,text=True)
        if sha.returncode == 0: print('commit', sha.stdout.strip())
        diff = subprocess.run(['git','-C',str(p),'status','--porcelain'],env=env,capture_output=True,text=True)
        print('working_tree_clean', diff.returncode == 0 and not diff.stdout.strip())
    else: print('commit unknown; archive install is not a verified git revision')
    for rel in ['main.py','src/application/services/feed_polling_service.py',
                'src/infrastructure/fetcher/rss/xml_parser.py']:
        f = p / rel
        if f.is_file(): print(rel, hashlib.sha256(f.read_bytes()).hexdigest())
PY
```

另由管理员在测试实例界面核实插件确实启用、bootstrap_skip_history、HTTP proxy、轮询间隔、去重库持久目录与失败重试配置；只分享脱敏值。测试订阅及继承的 handlers 必须为空，不启用 ai_filter / ai_transform / Routes 知识库初始化，不配置测试实例 LLM Key。群消息还可能触发其他聊天/学习插件，所以优先全新测试实例及测试账号；不能直接复用生产群并声称“只改 RSS、不调用模型”。

对获准检查的具体 Radar 库，用只读连接核实版本和 journal_mode，不切换模式或运行迁移（先用新合成库，正式库另行授权）：

```bash
RADAR_DB='/absolute/path/to/new-lab/synthetic.sqlite3'
python3 - "$RADAR_DB" <<'PY'
from pathlib import Path
import sqlite3, sys
p = Path(sys.argv[1]).resolve(strict=True)
if not p.is_file(): raise SystemExit('Not a database file')
c = sqlite3.connect(p.as_uri()+'?mode=ro', uri=True, timeout=1)
try:
    c.execute('PRAGMA query_only=ON')
    mode = c.execute('PRAGMA journal_mode').fetchone()[0]
    version = c.execute('PRAGMA user_version').fetchone()[0]
    print({'journal_mode':mode,'user_version':version,
           'supported':mode in {'delete','truncate','persist'} and version >= 5})
finally: c.close()
PY
```

## 网络方案：先确认拓扑，再批准变更

[Docker 网络文档](https://docs.docker.com/engine/network/) 明确环回地址属于相应网络命名空间。宿主可读 `127.0.0.1:8765` 不证明 AstrBot 容器可读；bridge gateway / host.docker.internal 也不会让仅绑定宿主环回的后端自动可达。

| 现场拓扑 | 待批准的最小方案 |
| --- | --- |
| 测试机器人与服务同一宿主网络命名空间 | 后端保持环回；从测试机器人实际运行环境检查 URL。先证实模式，不能假设 host 网络。 |
| 测试 AstrBot 在 bridge 容器 | 可选独立 RSS 侧车共享**测试容器**的网络命名空间，只挂合成库/私有状态，不挂整个 data、不发布端口；镜像、挂载及容器创建另行批准。重建测试机器人后需重新核查共享关系。 |
| 宿主 RSS 与 bridge 测试机器人分开 | 提交具体 bridge 地址、测试容器源地址、仅 `/approved.xml` 的代理与 ACL 方案；批准后实施。不能仅把 URL 改为网关地址。 |
| 跨主机 | 提交具体内网/VPN/受限隧道、端点、访问身份及代理配置；后端仍环回，批准后执行。不默认开放公网或改变防火墙。 |

所有代理关闭缓存/旧内容回退，503 原样透传，只映射精确 Feed 路由。测试端如有网络安全策略拒绝内网地址，应审查受控方案，不能全局关闭安全限制。先给用户审查实际网络改动，再操作；当前没有应用任何方案。

获准且已确定测试 URL 后，从机器人所在命名空间 GET，禁用环境代理的客户端探针只能说明直连，插件自身 proxy 配置还要另测：

```bash
# TEST_RSS_URL 由管理员填已批准的测试端点，不含 URL 凭据
docker exec -i "$BOT" python - "$TEST_RSS_URL" <<'PY'
import sys, urllib.request, xml.etree.ElementTree as ET
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(sys.argv[1], timeout=5) as r:
    body = r.read(1024*1024+1)
    if len(body)>1024*1024: raise SystemExit('Unexpected oversized test feed')
    xml = ET.fromstring(body)
    print('status',r.status,'type',r.headers.get('Content-Type'),'etag',r.headers.get('ETag'))
    print('items',len(xml.findall('./channel/item')))
    for item in xml.findall('./channel/item'):
        print('guid',item.findtext('guid'),'pubDate',item.findtext('pubDate'))
PY
```

## 隔离实验与真实验收顺序（未执行）

使用已批准的测试机器代码目录/venv。下面初始化/启动仅能在该测试目标授权后执行，LAB 目录必须不存在，不复制正式数据库。测试审批由带双重标识的现有合成命令完成。

```bash
PYTHON='/absolute/path/to/cdu-radar/.venv/bin/python'
LAB='/absolute/path/to/new-m3b-lab'
"$PYTHON" -m cdu_radar rss-synthetic init --directory "$LAB" --ttl 86400
"$PYTHON" -m cdu_radar serve-approved-rss --db "$LAB/synthetic.sqlite3" \
  --state-dir "$LAB/http-state" --bind 127.0.0.1 --port 8765 --interval 10 --max-age 30
```

先确定独立测试账号、测试群及消息上限。此完整方案预期 4 个新条目投递（第二条 1、同批 2、故障恢复 1）；批准上限建议 **最多 6 条 RSS 业务消息**，命令确认回复另记，禁止扩大目标或用 /sub_test 补凑成功。出现错误群、重复发送、模型调用或身份/去重异常立即停止测试订阅，保留证据；不清空历史重跑掩盖失败。首次业务消息为 0，但订阅命令可能回复确认，不能承诺群里完全无消息。

RSSHub 固定版本 [轮询实现](https://github.com/AstrBot-Elementary-School/astrbot_plugin_rsshub/blob/a047f46e47cb2a0f7a795f30b9f82ae027005846/src/application/services/feed_polling_service.py) 具有首轮跳过、稳定身份历史及发送确认逻辑；[解析器](https://github.com/AstrBot-Elementary-School/astrbot_plugin_rsshub/blob/a047f46e47cb2a0f7a795f30b9f82ae027005846/src/infrastructure/fetcher/rss/xml_parser.py) 拒绝空 entries。因此保留初始化条目覆盖整个实验，最后单独观察空 Feed；不依赖空 Feed 建历史。

| 顺序 | 操作与验收标准 | 实际 QQ / 插件结果 |
| --- | --- | --- |
| A | RSS 只有初始化 1 条；授权测试会话 `/sub <测试URL>`。确认 bootstrap 日志/持久历史非空，业务消息 0；未确认前不加第二条。 | 未执行 |
| B | 另终端 `rss-synthetic add --directory "$LAB" --ttl 86400` 添加 ID 2；保存 HTTP GUID、插件成功回执和群观察，新增恰好 1。 | 未执行 |
| C | 不变 Feed 连续至少 3 个实际轮询周期，200/304 均可；新增 0。 | 未执行 |
| D | 授权后重启**独立测试机器人**并保留数据卷；历史相同、再轮询至少 3 次新增 0。不重启现有生产机器人。 | 未执行 |
| E | 在确认的两次轮询之间连续添加 ID 3（ttl 86400）和 ID 4（ttl 600）；先保存同时含两者的 Feed，再轮询，两个身份各 1 次，不能只靠总数量判定。若周期跨越操作，重测需另行授权预算。 | 未执行 |
| F | `rss-synthetic update 2 --directory "$LAB"`；旧批准不能覆盖新正文，短暂 503 后移除 ID 2。再用合成 approve 恢复，GUID/pubDate 不变、新增 0。 | 未执行 |
| G | `rss-synthetic revoke 3 --directory "$LAB"`；等待 ID 4 有效期到达，记录各自移除及旧 ETag 不得续供失效版本。群中旧消息仍在，不是 QQ 撤回。 | 未执行 |
| H | 仅实验状态目录制造可恢复导出故障，确认实际 503。故障中添加 ID 5；至少观察一次插件失败且历史不前进，恢复后 ID 5 恰好 1 次，再轮询新增 0。 | 未执行 |

合成 approve：`"$PYTHON" -m cdu_radar rss-synthetic approve 2 --directory "$LAB" --ttl 86400`。E 的短 TTL 必须大于现场订阅周期与观察时间；如果 600 秒不足，开始前调整计划，不能在测试中偷偷延长已批准内容。两次 add 不保证同秒，需另保存 pubDate 核实；本地 FakeClock 同秒测试已经覆盖，现场未实际同秒就标注未验证。

H 可供审查的注入方式：只针对已确认带 synthetic-owner.json 的新实验目录，把私有 `http-state/approved.xml` 移到实验取证目录，然后在原路径建立**空目录**，等待后台原子替换报错并观察 503；不修改源库或权限、不更改程序、不提供旧 XML。若并发刷新导致 mkdir 失败就停止该注入，不能强制删除文件。恢复仅 `rmdir` 这个空目录，等待后台重建。执行前批准具体实验路径；禁止在正式状态目录演练。停服务导致拒绝连接不能当作 503 验收。

## 取证、停止与验收结论

私有证据目录先设 `umask 077`，使用新目录，放在仓库外或 ignored artifacts 下。每步保存时间、版本、HTTP 状态/ETag/响应 XML（仅合成 Feed）、每条 GUID/pubDate、插件状态/去重与失败队列摘要、实际群观察和投递消息 ID 的本地映射。群号、账号、消息正文及完整日志仅本地必要范围保留，Git 只放脱敏结论。不要输出完整 docker inspect/env/配置，不采集 Authorization/OneBot token。

授权后可用 `curl --noproxy '*' --max-time 5 -sS -D <新headers文件> -o <新xml文件> -w '%{http_code}\n' "$TEST_RSS_URL"` 留存，不加 `--fail`，以保留 503 证据。在机器人容器内检查的证据必须注明命名空间；宿主探针不能替代它。插件日志只截取获准的测试时间窗（例如 `docker logs --since <开始时间> --until <结束时间> "$BOT"`，输出到私有目录），分享前去除其他会话与凭据。投递日志计数不等于群可见数，要按 GUID 对照群观察；`dispatched` 也不一定等于底层消息包数量。

停止时由管理员停用该测试订阅、保存去重数据库/数据卷及合成审批历史，再停实验 RSS 服务。记录失败队列尚未发送的条目，停用后不擅自重放；不清空生产数据、不删除备份。全部保存和清理仅限本次新实验路径。

目前结论：服务器实际环境、已安装版本、容器 RSS 可达性、首次订阅、QQ 投递、实际重启及故障恢复均**未执行**。本地 493 项测试和合成 HTTP 证据可支持开始经授权的测试群实验，不能支持 M3-C 上线结论。

下一步需明确授权的具体项：目标测试主机与只读登录范围；独立机器人/测试账号/测试群；核实后的 RSSHub 安装或版本处理；精确网络端点与变更；测试实例重启、故障注入路径及最多 6 条业务消息预算。当前不执行这些操作。
