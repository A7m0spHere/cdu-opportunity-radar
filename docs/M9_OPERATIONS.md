# M9 部署、停止与回滚操作方案

本方案用于审查和明确获准的隔离演练；不自动连接服务器或操作当前生产。手机/LAN 现场体验已由用户暂缓，当前不再作为部署准备门槛。公开上线仍需 [来源与接入核查](M9_SOURCE_REVIEW.md) 列出的实际输入和明确授权。

## 版本与私有数据

代码以 wheel SHA-256 固定，不单靠 `0.1.0` 区分版本。本次 A 为 `d262b6...5998`，B 为 `1467c1...6216`，完整值及验收见 [完成记录](M9_COMPLETION_RESULTS.md)。各自安装到独立 venv，保留上一份 wheel、对应配置和依赖清单。私有 raw/jobs/http-state 使用最终固定路径；切换代码不移动这些目录，不通过数据符号链接实现切换。

反向代理使用独立私有 `-p` 前缀：`logs/`、`temp/` 和证书目录为运行者所有，私钥 0600。nginx 草稿复制后仅修改该私有副本，先 `nginx -t -p <隔离前缀>/ -c <隔离配置绝对路径>`，不能用不带 `-p/-c` 的命令 reload 机器现有 Nginx。持久服务草稿无 Install 段，本阶段未安装/enable；瞬时验收会随结束收集。

## 隔离部署和停止顺序

1. 检查非生产 owner 标记、目录属主/0700、解释器与 wheel 摘要、空闲环回端口、磁盘/内存以及当前选定生产服务快照。确定应用读写路径仅在隔离目录内；采集间隔 0、模型关闭、付费预算 0。
2. 从明确的合成备份恢复到全新最终路径；未核对前不使用真实业务库。安装指定 wheel 到新 venv，执行安装包 CLI/网页资源 smoke。启动独立环回应用，核对 `/health`、Feed 的内容类型/公开契约及页面声明。
3. 对私有 Nginx 副本做语法检查，再启动其独立 master。默认仅 HTTP 环回；HTTPS 演练需要单独指定测试 SAN/信任证书。核对 Host/路径/方法、证书可信和不可信两种结果、no-store、条件请求、正常空与 503/429，禁止旧快照兜底。
4. 从本机或只绑定两端环回的 SSH 转发读取，检查 GUID/pubDate 增量。不借此建立公网反向隧道。记录版本、合成数据边界、响应与资源结果；不得把短时成功当作容量或长期运行结论。
5. 停止时先停止私有测试 Nginx（只对已确认私有前缀的 PID 发 SIGQUIT），再以 SIGINT 停应用，等待退出和 publisher lease 释放。异常时只处置本轮已知 PID/瞬时单元，不使用 killall/pkill/nginx -s stop 影响同机其他实例。
6. 验证所有测试端口及 SSH 本地转发均关闭、瞬时单元退出，复核生产 Nginx PID/配置摘要、容器启动时间/重启计数。保留证据和备份；不自动删除任何旧生产目录、日志或备份。

## 升级和回滚

升级前停止采集、模型 worker、应用发布器等全部写者；确认停止后调用 `scripts/backup_state.py backup ... --quiesced`。核对 raw/jobs/subscriptions 清单、quick_check 与摘要，保留最新任务/未知用量，私钥和模型 Key 不入包。

同 schema 的代码切换：A 停止 → B 新 venv 使用相同固定数据目录启动 → 检查 Feed/首次时间/任务账本与页面 → B 停止 → 如需回滚以 A venv 指向相同最新数据目录 → 再核验。**不以旧备份覆盖新用量或新通知**。本轮已实测 A → B → A → B；两个 wheel 的业务 schema/核心相同，差异包括新增首屏非官方说明，不扩推至任意未来 schema 降级。

若旧版不兼容当前 schema，应停服并保存现场，用新独立固定目录恢复匹配备份，完成提供商费用/备份后新增通知对账，再经明确决定切换；不能改 schema 版本号、删除新表或自动覆盖现有目录。备份一致性的前置条件和身份重建见 [部署准备](DEPLOYMENT_PREPARATION.md)。没有跨版本兼容证据就拒绝切换，不生成旧 XML 维持假健康。

复现本次同 schema 的实机切换，需要原 M9 测试根、两套私有 venv、`native-test-v2/bundle` 和尚不存在的 `release-test/`：

```bash
venv/bin/python -I project/scripts/m9_release_acceptance.py --root /home/ubuntu/cdu-radar-nonprod-NEW --baseline-python /home/ubuntu/cdu-radar-nonprod-NEW/venv/bin/python --candidate-python /home/ubuntu/cdu-radar-nonprod-NEW/release-venv/bin/python
```

在独立受限瞬时单元中执行，MemoryMax=768M、CPUQuota=75%、TasksMax=64、RuntimeMaxSec=300、UMask=0077、PrivateTmp、ProtectSystem=strict、NoNewPrivileges，只给测试根写权限。脚本只监听 127.0.0.1:8767，拒绝占用端口和已有结果目录。

## 运行检查与密钥

检查来源失败/最后成功时间和 72 小时停发、worker 队列/未知费用、固定 Feed 的 503/429、磁盘/内存与证书到期。阈值与动作沿用 [部署准备](DEPLOYMENT_PREPARATION.md) 的表；`/health` 200 不等于有当前机会或来源已更新。严重错误日志私有，导出前脱敏，无客户端访问日志/追踪统计，不新增后台通知任务。

模型始终关闭，当前持续 LLM 花费上限为 0。未来开启时先明确提供商单价/币种和其服务端硬限额；本地 token 预留不是账单上限。轮换时保留用量账本、替换受限凭据并完成另行授权的最小验证后再撤旧凭据；不在 HTTP、日志或备份展示明文。

真实域名证书的到期告警、续期与失败停用方案在准备文档中已有设计，本轮只验证自签信任链正常/失败行为；没有申请证书或修改系统信任。公共内容许可、摘要核对、CDU 域名/主办者/接入条件、证书续期和长期容量验证属于真实公开版本的后续放行条件。
