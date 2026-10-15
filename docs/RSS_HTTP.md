# M3-A：只读 RSS HTTP 与安全刷新

> 历史接口说明。当前使用 [M4-B 自动 RSS](AUTO_PUBLICATION.md) 的 `serve-rss`；新服务 `/approved.xml` 返回 410，原 `serve-approved-rss` 启动入口停用。M3-A 的读事务、锁和 503 原则继续复用，但不再要求人工审批或 v5 审批表。

服务基于 main `954e914` 的人工发布契约。它不采集、不分析、不修改评测标签或人工审批，也不连接 AstrBot。HTTP 只允许精确的 GET/HEAD `/approved.xml` 和 `/health`，其他路径 404、写方法 405；没有目录列表、文件下载或管理接口。始终只绑定显式环回 IP，本轮不提供 0.0.0.0/LAN/公网绑定开关。

## 刷新与拒绝旧内容

后台复用现有批准导出：启动时重新核验并生成，之后按 interval 定时刷新。HTTP 请求使用只读 SQLite 连接检查当前批准、源版本和有效期；发现变化或过期先返回 503 并唤醒后台，安全刷新完成后才恢复。新批准、撤销、原文更新、有效期到达均由这一机制覆盖，不改审核系统。到期检查保留 main 中“未知日期不能延长原文明示截止”的防护；损坏的模型参考缓存不参与发布决策。

后台持久化现有发布台账的首次时间和系统导出事件，**不创建人工批准**。首次服务即是已批准内容的自动导出，不需要再给 HTTP 调用审批权限。Source DB 必须已经通过原有管理员流程初始化到 v5；服务不创建替代数据库或自动迁移。

任何数据库、文件写入或结果核验失败都清除可服务的内存快照，返回 503，禁止读取上次成功 XML 作为回退，也不对旧 ETag 返回 304。GET/HEAD 自身不写数据库或文件，只将内存状态置不可用并请求后台刷新。后台失败会尽力删除私有缓存文件；无法删除也不影响 HTTP 拒绝旧内容。缓存在私有状态目录，禁止用另一个静态文件服务器绕过守门检查。

服务返回 UTF-8 RSS 2.0、Content-Type `application/rss+xml; charset=utf-8`、no-store 缓存指令和当前内容 ETag。只有重新验证有效的当前快照才可返回 304。GUID/pubDate 由原有持久发布台账提供，定时刷新及正常/异常重启都不伪造新消息；重启不信任磁盘旧 XML。忙碌或卡住的刷新不会无限阻塞读请求，守门锁等待 0.1 秒后返回 503；成功读响应保持 SQLite 读快照到写出完成，客户端超时上限 5 秒。

状态目录通过 owner.json 绑定源库路径，并持有 Windows msvcrt / Linux flock 的单写者 OS 锁。进程退出自动释放锁，publisher.lock 文件可以保留，不需要靠 PID 判断或删除旧锁。不同源库必须使用不同状态目录；拒绝非空且无标识目录、符号链接缓存、包含源数据库的状态目录。

## 部署前置条件

- 本地磁盘上的 SQLite v5，保持已有源表与发布表；已有批准仍须有效。后台账号要有数据库及其目录的读写权限，以记录首次导出与 SQLite 日志。HTTP 不提供写接口。
- **仅支持 delete / truncate / persist 回滚日志模式。WAL / memory / off 被拒绝**。WAL 允许写者在旧读快照存在时提交，当前响应顺序保证不适用；服务不会擅自切换模式。需要 WAL 的部署应等后续共享同步协议，不能绕过检查。
- SQLite 与缓存使用可靠本地文件系统，不用 NFS/网络共享；配置正确的系统时间/NTP，时间事实按 Asia/Shanghai 核验，存活验证使用单调时钟。
- interval 范围 0.1 秒至 max-age，max-age 为 0.1–3600 秒。建议 interval=10、max-age=30。后台未及时完成验证时，HTTP 保守拒绝，不能用旧缓存提升可用率。
- 状态目录只属于该服务，权限限制到服务账号。无需 LLM Key，不加载 .env，不配置消息发送工具。

## Windows 开发运行

在可信本地管理员完成原有批准流程后，才对工作库运行：

```powershell
.\.venv\Scripts\python.exe -m cdu_radar serve-approved-rss --db data/m2c.sqlite3 --state-dir data/m3a-rss-state --bind 127.0.0.1 --port 8765 --interval 10 --max-age 30
# 另一终端读取；此命令不审批或发送消息：
Invoke-WebRequest http://127.0.0.1:8765/approved.xml
Invoke-RestMethod http://127.0.0.1:8765/health
```

Ctrl+C 停止。主库尚无批准时可返回合法空 Feed；它不适合直接作为 RSSHub 初始化消息。不要对外开放整个 data 目录，不要启动普通 `http.server` 来服务状态目录。独立合成实验与测试群步骤见 [M3A_TEST_GROUP.md](M3A_TEST_GROUP.md)。本轮实际运行只用隔离合成库，未启动以上真实库服务。

## Linux 服务器部署说明（未执行）

需要用户另行授权生产连接/网络发布后才实施。Python 3.11+、SQLite、本项目常规依赖即可，不需要 AstrBot、Web 框架或模型 SDK 新依赖。在 `/opt/cdu-radar` 建 venv/install，在 `/srv/cdu/private` 放经核验的工作库，在 `/srv/cdu/rss-state` 放私有状态；两者均不挂为静态目录。

```bash
python3 -m venv /opt/cdu-radar/.venv
/opt/cdu-radar/.venv/bin/python -m pip install -e /opt/cdu-radar
/opt/cdu-radar/.venv/bin/python -m cdu_radar serve-approved-rss \
  --db /srv/cdu/private/radar.sqlite3 --state-dir /srv/cdu/rss-state \
  --bind 127.0.0.1 --port 8765 --interval 10 --max-age 30
```

可供人工审查的 systemd 单元示例，未在 Linux 上执行：

```ini
[Unit]
Description=CDU approved RSS gate
After=network.target
[Service]
User=cdu-radar
Group=cdu-radar
WorkingDirectory=/opt/cdu-radar
ExecStart=/opt/cdu-radar/.venv/bin/python -m cdu_radar serve-approved-rss --db /srv/cdu/private/radar.sqlite3 --state-dir /srv/cdu/rss-state --bind 127.0.0.1 --port 8765 --interval 10 --max-age 30
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/srv/cdu/private /srv/cdu/rss-state
[Install]
WantedBy=multi-user.target
```

使用独立非特权账号及数据库备份。不要设置独立 PrivateNetwork 后又假定宿主 Nginx 能访问该进程环回端口。服务文件权限、发行版 Python/SQLite 版本和 OS 锁行为需在实际服务器验收；本轮 Windows 本地测试不能替代 Linux 部署验收。

如需另一台测试机访问，必须另行授权网络目标，再用受控反向代理；后端保持环回。下面仅为**环回代理**配置示例，不开放公网：

```nginx
server {
    listen 127.0.0.1:8080;
    location = /approved.xml {
        proxy_pass http://127.0.0.1:8765;
        proxy_cache off;
        proxy_buffering off;
        proxy_intercept_errors off;
        proxy_read_timeout 10s;
        add_header Cache-Control "no-store" always;
    }
    location / { return 404; }
}
```

不配置 try_files、alias、root、旧 XML error_page 回退或 proxy_cache_use_stale。正式 TLS、访问网段、端口和反向代理变更需用户授权并现场验证。失败应透传 503，而不是由代理补旧 Feed。

## 已知边界

SQLite 与文件不能共同事务提交，保留 M2-C 的持久首次时间和稳定重试策略。新请求可拒绝失效内容，但已写入网络缓冲区或 QQ 的消息不能追回。503 可能在刷新、写锁争用或不支持的数据配置期间暂时出现。合法空 Feed 仍可能被插件条目解析器拒绝，不会伪造初始化条目。

测试使用真实环回 HTTP 和源码语义对齐的 Mock；没有安装实际插件、验证 Linux 服务单元、TLS/代理或真实测试群推送。客户端去重数据丢失、历史保留上限或错误缓存策略可能导致重发/漏发；不能用服务 GUID 保证任意客户端“永不重复”。
