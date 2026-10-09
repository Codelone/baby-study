# 005：Cloudflare 家庭试用部署实施计划

| 字段 | 内容 |
| --- | --- |
| 需求 ID | 005-family-cloudflare-trial |
| 状态 | in-progress |
| 关联 Spec | [spec.md](spec.md) |
| Spec 基线版本 | v0.2 |
| 更新日期 | 2026-09-29 |

## 实施方案

- 独立 `config.family_settings` 开启 HTTPS 安全设置，保留开发模式配置。
- `deployment/family/` 使用 Caddy 提供构建文件、静态资源和同域反向代理；Gunicorn 和 worker 复用本地数据。
- Python 启动监督器在持有独占锁时管理子进程；通过权限 0600 的 Unix socket 停止，历史 PID 只用于状态展示。
- 每次先备份再迁移、构建并创建/核对账户；隧道给出随机网址后配置精确 Host/CSRF 并启动 Web。异常退出时关闭自有组件。
- 随机密钥和账户密码保存在忽略提交的 `deployment/family/.env`，权限 0600；日志/备份和状态在私有 `.runtime/`。

## 任务分解

| 编号 | 任务与影响路径 | 对应需求 / 验收 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| TASK-001 | 运行依赖、数据备份、构建与迁移；backend/requirements.txt、deployment/family/ | REQ-001 / AC-001 | 本机 Docker | 完成 |
| TASK-002 | family_settings、Caddy、隧道域名及安全配置 | REQ-002、REQ-003 / AC-002、AC-003 | TASK-001 | 完成 |
| TASK-003 | 专用账户命令及凭据文件，测试不可覆盖既有账户 | REQ-004 / AC-003、AC-004 | TASK-001 | 完成 |
| TASK-004 | 启动/停止监督器、地址及日志、说明 | REQ-005、REQ-007 / AC-005、AC-008 | TASK-002 | 完成 |
| TASK-005 | 后端检查/测试、前端检查、公网及进程生命周期验证 | REQ-006 / AC-002、AC-003、AC-005、AC-006 | TASK-001～TASK-004 | 进行中：自动化/公网验证完成，实物照片识别待验收 |
| TASK-006 | 大陆手机移动网络及家人实测反馈 | REQ-006 / AC-007 | 公开试用地址、用户实际操作 | 待开始 |

## 需求对应与验证

| 需求 | 实施任务 | 验收项 | 验证方式 | 结果与证据 |
| --- | --- | --- | --- | --- |
| REQ-001 | TASK-001 | AC-001 | pg_restore --list、数据计数、构建/迁移日志 | 通过，见 [validation](validation.md) |
| REQ-002 | TASK-002、TASK-005 | AC-002 | 回环监听、健康/页面/静态请求 | 通过，见 [validation](validation.md) |
| REQ-003 | TASK-002、TASK-005 | AC-002、AC-003 | Django --deploy、HTTPS 登录/CSRF 自动化 | 通过，见 [validation](validation.md) |
| REQ-004 | TASK-003、TASK-005 | AC-003、AC-004 | 账户权限/不可覆盖测试、真实登录 | 通过，见 [validation](validation.md) |
| REQ-005 | TASK-004、TASK-005 | AC-005 | 启动/重复启动/停止/重启，检查数据库和 5173 | 通过，见 [validation](validation.md) |
| REQ-006 | TASK-005、TASK-006 | AC-006、AC-007 | 书架/评估/私有照片测试、worker、小批上传及手机实测 | 部分通过：实物照片完成及手机移动网络待验收，见 [validation](validation.md) |
| REQ-007 | TASK-004 | AC-008 | 文档与实际命令核对 | 通过，见 [validation](validation.md) |

## 风险、迁移与剩余事项

- 本期无业务 schema 新迁移；执行既有迁移前备份。恢复需要先停止试用，再按说明使用 pg_restore。
- 家庭共用账户不复用原管理员的个人书架/评估，试用更改写入真实本地数据库。
- 识别 worker 处理真实待办任务；MiMo 调用沿用已有模型、费用与 Batch 配置。
- 30 张大图可能超过 Cloudflare 请求大小；分片支持延期至后续需求。
- 大陆移动网络和真实模型结果必须记录实际证据，不以自动测试替代。

## 计划调整记录

| 日期 | Spec 基线 | 变更及原因 | 影响任务 |
| --- | --- | --- | --- |
| 2026-09-29 | v0.1 | 根据用户确认方案开始实施；新增 Unix socket 控制避免误杀历史 PID | TASK-001～TASK-006 |
| 2026-09-29 | v0.1 | 配置和脚本完成；260 后端测试、3 进程管理测试、真实公网业务及停止重启通过；保留实物照片和移动网络待验收 | TASK-001～TASK-006 |

## 2026-09-29 后续用户确认（v0.2）

007补充5秒新任务调度、IPv4/HTTP2、日志轮转/状态诊断和失效检测；清理后保留唯一后台部署。详见 [007 Spec](../007-photo-intake-diagnostics/spec.md) 和 [Plan](../007-photo-intake-diagnostics/plan.md)。原始需求及此前验收证据保留，新增验收由007管理。

2026-09-29：007实现及研发验收完成。实物两张照片实时识别已通过并保持待确认，独立拍照入口合并为多选上传；日志/状态/停止重启和公网同任务重试已验证。历史Batch及物理手机的新入口验证继续保留，详见[007验证记录](../007-photo-intake-diagnostics/validation.md)。
