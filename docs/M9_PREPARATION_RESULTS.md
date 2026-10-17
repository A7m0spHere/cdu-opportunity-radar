# M9 部署准备验证（2026-10-17）

> 本文保留此前 Windows 阶段的实际结果。后续已获授权并使用现有服务器完成 Linux/Nginx/TLS 与受控跨机 RSS 测试，当前进度见 [M9 服务器验收](M9_SERVER_RESULTS.md)。

按用户“完成下一个 issues”的指令继续 #14，完成本机可执行的设计、草稿、离线备份恢复工具与验证。**部署准备阶段通过，#14 整体验收未完成、未关闭。** #6 的第二设备/手机和非生产 Linux 原生仍缺失；没有登录现有生产腾讯云、启用服务、启动 nginx、申请证书或开放公网/LAN。本轮没有提交、推送或修改远端 issue。

设计、边界、操作顺序和后续条件见 [DEPLOYMENT_PREPARATION.md](DEPLOYMENT_PREPARATION.md)。本轮保留上一阶段 #6 的未提交文件、原有 `.idea` 暂存与其他部署草稿。

## 交付

- `deploy/m9/`：精确公开路由、固定环回上游、无缓存/旧响应兜底、只读/限流/超时/Host 和头边界的 nginx 草稿；未默认启用的 TLS 草稿；默认无采集/模型的 systemd 草稿；目标、地区、域名及审查状态均显式未知的候选参数文件。
- `cdu_radar/backup_state.py` 与 `scripts/backup_state.py`：停止写者后的三库快照、清单/摘要/SQLite/身份验证，以及仅向全新目录恢复；不复制凭据/日志/旧 XML、不修改源库，重建新副本身份并使旧精选集合失效。
- `scripts/validate_m9_design.py`：定点静态策略检查，比较当前代码路由白名单；不宣称实现 nginx 配置解析器或 `nginx -t`。
- `scripts/m9_offline_acceptance.py`：合成数据、Mock 提案、备份/恢复 CLI、重验后的精选一致性检查；没有打开监听或进行部署演练。
- `tests/test_backup_state.py`：恢复数据/首次时间/任务及预算保护、活跃发布器拒绝、错误清单/身份/校验和/路径/已有目标/WAL/额外表拒绝，以及 10 种危险草稿变更的检测。

## 实测

| 检查 | 结果 | 证据与边界 |
| --- | --- | --- |
| 专项测试 | 通过 | 26 passed；包含恢复后重新验证的精选 XML 与原始一致，合成未知付费预留记录不被删除或清零 |
| 最终全量回归 | 通过 | `.venv/Scripts/python.exe -m pytest -q`：977 passed、2 skipped，275.32 秒；两项跳过为当前 Windows 不允许创建符号链接 |
| 草稿策略 | 通过 | 21 项定点静态检查；只允许当前 14 条精确公开路由，未知路径拒绝，默认监听仅 127.0.0.1，TLS 未 include，模型/采集/生产授权均关闭 |
| 合成离线数据工具验证 | 通过 | 3 条合成原文、2 条精选；三库备份与新目录恢复 CLI 实际执行；源数据库哈希保持，原文核验时间、任务/账本和首次入选保持，恢复 collection 先清空，重新验证后 XML 相同 |
| wheel 构建/隔离安装 | 通过 | `pip wheel . --no-deps`，随后 `pip install --target ... --no-deps --no-index`；从该安装目录实际导入新的备份模块并完成恢复。使用现有环境依赖，不冒称独立依赖环境或 Linux 安装验收 |
| 依赖/差异 | 通过 | `pip check` 无损坏依赖，`git diff --check` 无空白错误 |
| 基线远端 CI | 已核对成功 | `8e29999` 的 [Offline CI](https://github.com/A7m0spHere/cdu-opportunity-radar/actions/runs/38935608765)：Windows/Ubuntu × Python 3.11/3.12 四项成功。本轮新文件还没有远端 CI，旧结果不能覆盖新改动 |
| nginx -t、HTTPS、systemd、Linux 原生 | 未执行 | 当前未安装 nginx、没有明确非生产 Linux 目标；草稿不等于运行验收，已安装 OpenSSL 也不代表代理链路通过 |
| 真实阅读器经 HTTPS 长期订阅、原生故障/限流、独立部署演练 | 未执行 | 依赖 #6 与明确隔离目标；本轮仅离线工具测试，不放宽前置条件 |
| 地区/域名/备案/来源条款/公共摘要与非官方声明 | 未完成 | 已查阅官方办事指南及技术文档并形成待核实项；实际目标尚未知，没有执行备案或对外内容审核 |
| 生产操作、模型调用、业务网络请求、部署监听 | 未执行 | 离线验证程序记录均为 0；pytest 自身使用隔离 Mock/环回测试，不是部署演练。文档/CI 查询与构建依赖联网单独区分 |

首轮全量为 976 passed、2 skipped、1 failed：既有 Windows `test_subprocess_timeout_is_bounded_and_mock_needs_no_key` 的总耗时约 6.69 秒，超过 5 秒断言。独立复验 2.45 秒通过，保留原测试/阈值后的完整复跑为上表 977 passed。没有为 M9 修改模型超时、重试或其他产品逻辑；这一时间波动不能当作已完成原生模型运行验收。

本地私有证据为 `artifacts/m9-preparation-20261017/result.json`、`bundle/manifest.json`、`restored/restore.json` 和 `installed-result.json`。内容是明确的合成原文/Mock，人工真值 0，listeners_opened=0、deployment_rehearsal_performed=false。恢复操作不自动启动任何服务。

本轮 wheel：`artifacts/m9-preparation-20261017/wheel/cdu_opportunity_radar-0.1.0-py3-none-any.whl`，SHA-256：`d262b6b42dd7fe4a965043a1621e570d3e076daead0710db115230f6dc175998`。安装副本及合成恢复目录在忽略的 artifacts 内保留作证据，原 #6 数据和真实队列未用于本轮恢复。

## 复现

```powershell
.\.venv\Scripts\python.exe scripts/validate_m9_design.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_backup_state.py
.\.venv\Scripts\python.exe scripts/m9_offline_acceptance.py --directory artifacts/m9-new-isolated-run
```

合成验证目录必须尚不存在。脚本没有部署模式，也不读取模型 Key；外部目标、TLS 证书、系统服务安装与权限配置仍须以后在满足前置条件的独立环境中验证。

下一步是补齐 #6 和明确非生产目标，再执行原生安装、真实 nginx/TLS/阅读器/故障与恢复/回滚的独立验收；真正上线仍需单独授权。本记录不把准备完成改写成已部署或 #14 已关闭。
