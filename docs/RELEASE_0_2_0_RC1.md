# 0.2.0rc1 自动化部署候选

本候选包含本机闭环、简单真人标注、LLM 自动分类/精选，以及 #27 证书信任修复。用于后续服务器隔离验收；不是公开 Beta 或生产上线。

## 依赖与信任修复

应用最低要求 `certifi>=2026.10.19`，当前精确锁定 `2026.10.19`，对应 [GHSA-6q48-6c7w-9gw2](https://github.com/advisories/GHSA-6q48-6c7w-9gw2)。采集、DNS 覆盖、模型请求及网络诊断显式使用修复后的 CA 包，证书与主机名验证保持开启。

本机 `SSL_CERT_FILE` 指向旧 CA 包。全局变量没有修改，应用不继承这个覆盖；预检分别记录 ambient HTTPX 和应用实际 TLS。部署单元只在自身范围清除 CA 环境覆盖，不将应用修复称为全系统信任库修复。

## 产物与提交

产物在 `artifacts/release-0.2.0rc1/`：候选源码 zip、wheel、运行时 wheelhouse、摘要、候选补丁和验证记录。`delivery/source-manifest.json` 记录基线 commit、候选 Git tree 和范围；`delivery/delivery-manifest.json` 记录最终产物校验和。部署 zip 解压后的根目录包含下述命令需要的脚本、依赖锁和部署模板，先进入该目录再操作。

```powershell
.\.venv\Scripts\python.exe scripts/prepare_release.py --directory artifacts/new-release-candidate
```

打包使用独立 Git index，真实 index 不变。范围由 `deploy/m10/release-files.json` 指定；私有数据、标签、配置、凭证、IDE 项和无关未跟踪 M3-B 草稿不进入新增范围。实际提交只使用清单中的路径；现有暂存区含用户 IDE 项，不能整体提交。建议提交说明：“feat: 准备自动化部署候选并修复 TLS 信任依赖”。本轮没有自动 commit/push、tag 或发布包。

## 干净安装与预检

使用独立 Python 3.11+，建议与本机验收一致的 3.12。保留旧 venv、wheel、配置及数据，创建新环境：

```bash
python3.12 -m venv /opt/cdu-radar/venv-0.2.0rc1
/opt/cdu-radar/venv-0.2.0rc1/bin/python -m pip install --no-index --find-links bootstrap --require-hashes -r bootstrap-lock.txt
/opt/cdu-radar/venv-0.2.0rc1/bin/python -m pip install --no-index --find-links wheelhouse --require-hashes -r runtime-lock.txt
/opt/cdu-radar/venv-0.2.0rc1/bin/python scripts/verify_security_environment.py --require-lock --output /var/tmp/cdu-security-new.json
/opt/cdu-radar/venv-0.2.0rc1/bin/python -I scripts/ci_smoke.py
```

wheelhouse 仅含通用 Python 运行依赖，应用不需要开发工具。不要用 `--no-deps` 跳过实际部署的依赖修复。构建工具、应用/测试锁、审计工具分别由 requirements-build、requirements-lock、requirements-audit 维护。Linux、容器实际使用前应在目标环境重建，不复用未经核验的旧 venv/镜像。

## 数据备份与恢复

先停止采集、模型、发布器及消费者等全部写者。备份 raw/jobs/subscriptions，再恢复到最终的新目录：

```bash
python scripts/backup_state.py backup --raw-db RAW_DB --state-dir HTTP_STATE --analysis-db JOBS_DB --output NEW_BACKUP --revision CANDIDATE_TREE --quiesced
python scripts/backup_state.py restore --bundle NEW_BACKUP --output /var/lib/cdu-radar
```

工具仅在副本中重绑路径/inode 和发布器身份，保留用量、原文健康与首次时间，旧精选集合等待重验。不能用旧数据覆盖已经产生新用量/通知的目录。

现有备份工具不包含消费者 consumer.sqlite3、人工标签或凭证；需要这些状态时，在停写后另存一致副本与摘要。消费者身份包含 URL/端口，不自动改写。部署包不附带任何私有状态，也不启用真实机器人。

代码/传输版本影响缓存身份。模型、输入、Prompt 与 Schema 不变时可显式用 `analysis-queue revalidate --from-pipeline OLD --notice-id ID ...` 零请求重验，保留原调用/费用账本。模型或 Prompt 改变不能复用旧提案。

## 启停与回滚

将 analysis.example.json 和 environment.example 复制到受限 `/etc/cdu-radar/`，确认固定路径。密钥在服务器阶段私下设置环境变量，不继承 Windows DPAPI 路径。长期预算也在服务器阶段确认，不能机械继承本轮试验额度。

```bash
/opt/cdu-radar/venv-0.2.0rc1/bin/python deploy/m10/run_service.py --check-only
```

默认模型 worker 和真实采集都关闭；检查模式不监听、不请求。确认服务器、资源、预算及启停时间后才按计划启用。服务固定绑定 127.0.0.1；systemd 草稿无 Install 段，草稿存在不等于已安装/启用。

停止本单元使用 SIGINT，等待自己的 worker/端口退出并保留状态。回滚使用独立旧 venv、匹配配置及**已修复的依赖**，先停后启；不得回退到受影响的 certifi，或用旧备份覆盖最新用量。旧版若不支持 v3 配置/缓存，采用匹配配置并允许精选暂不可用。任意旧版、跨 schema 降级没有保证；必要时保存现场，在新目录恢复和对账后再切换。

本轮只验证本机修复、干净安装、审计与候选准备。Ubuntu CI、服务器环境、容器重建（如实际使用）、真实 48h、公网和机器人投递均按后续阶段验收。详见 [#27 记录](SECURITY_27_RESULTS.md)。
