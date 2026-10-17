# M9 现有服务器隔离验收（2026-10-17）

用户明确授权使用已有服务器资源测试，并提供服务器档案位置。本阶段在该机器的独立私有目录、虚拟环境和瞬时 systemd 单元中完成 Linux、Nginx、HTTPS、RSS 增量和数据恢复验证：**Linux 全量 979 项通过，原生链路 49 项通过，Windows 跨机订阅检查通过。** 现有生产服务前后检查一致，测试进程、端口及 SSH 转发均已关闭。

这是与现有服务共享物理资源的隔离实例，并非独立物理服务器或公网部署。#6 的 Linux 原生部分已补齐；手机、真实同 Wi-Fi LAN 和同学反馈仍待完成。#14 的隔离演练部分通过，公开入口、真实域名证书及上线条件未完成，issue 保持开放。本阶段未提交、推送或修改远端 issue。

## 环境与隔离

| 项目 | 实测与边界 |
| --- | --- |
| 主机 | Ubuntu 22.04，kernel 5.15.0-106-generic，4 CPU，约 3.3 GiB 内存；开始可用内存约 1.6 GiB、磁盘约 19.1 GB。不公开地址与 SSH 凭据位置 |
| 私有目录 | `/home/ubuntu/cdu-radar-nonprod-20261017-m9`，ubuntu 所有、0700，`owner.json` 标记为隔离测试；代码、合成数据库、证书、日志、运行时均在此目录 |
| 解释器 | 目录内便携 CPython 3.12.15 和全新 venv，Python SSL 使用 OpenSSL 3.5.9；系统 Python 3.10.12 保持原样。运行时使用 Astral python-build-standalone，来源见[官方安装说明](https://docs.astral.sh/uv/guides/install-python/) |
| Nginx | 使用现有 1.18.0 二进制、独立 `-p`/`-c` 前缀与非特权 master。日志、PID、五类临时目录均在私有前缀；没有 reload 现有 Nginx |
| 资源限制 | 两个顺序运行的瞬时单元：pytest MemoryMax=768M、TasksMax=128；原生验收 MemoryMax=512M、TasksMax=64；均 CPUQuota=75%、RuntimeMaxSec=900、UMask=0077、PrivateTmp、ProtectSystem=strict、NoNewPrivileges，只放行私有目录写入。单元退出后收集，不安装/enable 常驻服务 |
| 监听与数据 | 应用 127.0.0.1:8765、CLI 烟雾 8766、HTTP 8870、HTTPS 9443；均仅环回且结束后关闭。三条合成原文、Mock 分析，再追加一条合成通知；官方采集和真实模型调用均为 0 |
| HTTPS | 新建仅供测试的 1 天自签证书，SAN 包含 localhost/127.0.0.1，私钥 0600；客户端只信任指定测试证书，不关闭校验、不修改系统信任。没有申请真实域名证书 |

安装的 wheel SHA-256 为 `d262b6b42dd7fe4a965043a1621e570d3e076daead0710db115230f6dc175998`，与 [Windows 准备记录](M9_PREPARATION_RESULTS.md) 相同。原生 runner 使用 `-I` 并检查模块来自隔离 venv 的 site-packages；测试源码及部署草稿的最终摘要见私有 `source-manifest-final.json`。包/解释器下载是测试依赖联网，不是业务采集或模型请求。

## 实际结果

| 检查 | 状态 | 证据与范围 |
| --- | --- | --- |
| Linux 全量与安装包 | 通过 | `venv/bin/python -m pytest -q`：979 passed、0 skipped、84.36 秒；瞬时单元 exit 0。安装 wheel 的 `python -I project/scripts/ci_smoke.py` 检查 CLI 和网页资源通过 |
| CLI 原生启动/停止 | 通过 | 实际启动安装后的 `serve-rss`，得到 3 条 RSS 通知，SIGINT 退出码 0 |
| Nginx 配置与 TLS | 通过（有诊断） | `nginx -t` exit 0；实际协商 TLS 1.3、信任测试证书可解析 RSS 2.0、未信任证书会拒绝。启动诊断见下文 |
| 公开/私有边界 | 通过 | 页面/静态资源/JSON v1-v3/旧新 Feed/health 13 路由 200 且 no-store；旧 approved=410、私有路径=404、非法和错误编码参数=400、POST=405、非法 Host=403 |
| 条件请求 | 通过 | 有效快照重复请求 304，显式默认筛选与规范订阅一致；来源撤回、分析变更后不会返回旧 304/旧精选，部分覆盖明确为 degraded |
| 超期、故障与限流 | 通过 | 合成时钟前进 73 小时后无过期条目；7 秒上游延迟约 6009 ms 后返回 503，无旧正文/ETag；停止上游为 503。50 次突发得到 21 次 200、29 次 429，429 的 Retry-After=1 |
| 慢请求头 | 通过 | 未结束的请求头约 5005 ms 后被关闭；Nginx 1.18 返回 EOF。此项不代表大规模慢客户端或慢响应读取负载已验收 |
| 三库恢复/路径回切 | 通过 | 停止发布器后备份，源库摘要不变；恢复到新目录重绑身份并重验，精选 XML 与原始一致；回切原隔离数据路径再次一致。此次验证同版本数据路径回切，未执行旧代码版本降级或真实灾难恢复 |
| Windows → SSH → HTTPS | 通过 | 仅本地环回 SSH 转发，feedparser 解析；初始精选 2 条，服务器追加合成通知后 3 条，新增 GUID 恰为 1，旧 GUID/pubDate 不变、ETag 更新，重复请求 304；两次转发均关闭 |
| 停止后检查 | 通过 | 49 项原生检查全部通过、瞬时单元 exit 0；8765/8766/8870/9443 全部关闭，Windows 转发端口关闭 |
| 生产状态对照 | 通过（选定快照） | 生产 Nginx MainPID/active 状态、四个容器 ID/StartedAt/RestartCount、三份生产 Nginx 配置 SHA-256、原监听端点均与基线一致；结束可用内存约 1.63 GiB。未改变防火墙、DNS、生产数据或服务配置 |
| 手机/LAN/用户反馈 | 未执行 | 跨机 SSH 验证不是手机或同 Wi-Fi LAN 测试，人工参与者仍为 0；也未做第三方 GUI 阅读器长期无人值守订阅 |
| 公网发布 | 未执行 | 没有新公网监听、域名映射、证书续期、生产升级、模型调用或业务通知发送 |

收尾本地复核：`tests/test_backup_state.py` 26 passed（41.29 秒）、草稿静态策略 21 项通过、runner AST/证据摘要/文档链接及 `git diff --check` 通过。

上表来自一次短时、小数据演练，不推导吞吐或生产可用性承诺。自签测试证书 SHA-256：`0a813a66288f515d9919d6c1d9aa9725f4b9bf790385d2b53979732a5fef64f3`。

## 过程中的失败与修正

服务器直接下载便携 Python 超时，改为本机从官方 GitHub 获取、校验 SHA-256 后通过 SSH 上传。解释器归档 SHA-256 为 `178d3d83b6e8e99285a65d3fce9c16b87952cc5efdb4d1a0e3ff3be18ff0fc9c`。Ubuntu 的 tar 安全过滤器拒绝可选 terminfo 硬链接，保留过滤器，在新目录解压时排除 2871 个 terminfo 条目；ssl/sqlite/venv 和后续测试通过。旧解压诊断保留在私有目录，未替换系统 Python 或安装系统包。

首次原生尝试将慢请求头超时限定为 HTTP 408，但 Nginx 1.18 在约 5 秒后直接关闭连接。仅修正验收脚本，接受在 4.5–7.5 秒内收到 EOF 或 408；产品超时仍为 5 秒。第一次失败记录保留，第二次 49 项通过。

该 Nginx 二进制会先尝试打开编译默认的 `/var/log/nginx/error.log`，非特权用户因此出现一条 Permission denied alert，随后使用私有配置日志，`nginx -t` 与实际运行成功。未修改生产日志权限或为消除提示改用 root。不能将本次记录表述为“无诊断的配置检查”。

本轮草稿补充 `client_body/proxy/fastcgi/uwsgi/scgi` 五种相对临时路径，避免依赖发行版的系统临时目录。原生 runner 为 [scripts/m9_native_server_acceptance.py](../scripts/m9_native_server_acceptance.py)；程序启动仅作用于明确标记的新测试目录，不会自动登录服务器或修改系统服务。

## 证据与复现

私有证据根目录为 `artifacts/m9-server-20261017/`，不提交完整主机信息或运行日志。`test-evidence.tar.gz` 含 21 份白名单报告/摘要，已下载并验证 SHA-256：`fdcb77580e974c55bb8b1bff7d76d61c3481b65955040858b36cac6e39e8b994`；不含私钥、凭据或 SQLite 数据库。安全解压后的 `server-evidence/` 包括：

- `linux-pytest.log`、`installed-wheel-smoke.log`、两个瞬时单元状态及依赖清单。
- `native-test/result.json`（首次失败）与 `native-test-v2/result.json`（49 项通过）、Nginx 诊断、恢复清单与最终源文件摘要。
- `baseline.json` 与 `postflight.json`（所选生产状态前后对照）。

Windows 读取证据另存 `baseline-reader.json`、`updated-reader.json` 和公开测试证书；SSH 私钥只由本机客户端使用。服务器私有测试目录保留以便复核，所有测试服务已停止。

复现需要 Linux、已有 Nginx/OpenSSL、指定 wheel 和独立 venv，并由操作员先创建专属私有目录和 `owner.json`（`kind=cdu-m9-isolated-test`）。目录由运行者所有，名称须以 `cdu-radar-nonprod-` 开头；源码放在 `project/`、安装包在 `venv/`。从该根目录、在同等资源限制的瞬时单元中执行；`--run-name` 对应目录必须尚不存在：

```bash
venv/bin/python -I project/scripts/ci_smoke.py
venv/bin/python -I project/scripts/m9_native_server_acceptance.py --root /home/ubuntu/cdu-radar-nonprod-NEW --run-name native-test
```

runner 固定使用四个环回端口，开始时检查未占用；不应与另一轮同时运行。跨机检查使用 SSH LocalForward 的本地环回绑定，并为 HTTPS 客户端显式指定测试 CA。报告不包含可直接连接生产的命令或凭据。

## 后续状态

#6 剩余真实手机/同 Wi-Fi LAN、3–5 位使用者反馈；Linux 原生和本次受控跨机链路已验证。#14 剩余实际公开目标的地区/域名条件、来源再分发和摘要核对、公开页面声明、真实证书/续期、长期运行与公开切换。服务器档案提供了既有云资源的地域线索，尚未替代具体 CDU 公开目标确认。本阶段的测试授权不代表已授权公网发布；部署设计当前状态见 [DEPLOYMENT_PREPARATION.md](DEPLOYMENT_PREPARATION.md)。
