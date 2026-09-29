# 005 家庭试用部署验收记录

日期：2026-09-29；Spec 基线：v0.1。部署代码已完成并实际运行；实物照片完整识别和大陆手机移动网络体验尚未验收，规格保持 `in-progress`。

## 自动化检查

| 检查 | 结果 |
| --- | --- |
| `.venv/bin/ruff check backend deployment/family` | 通过 |
| `.venv/bin/ruff format --check backend deployment/family` | 77 个文件通过 |
| `bash -n deployment/family/start.sh deployment/family/stop.sh` | 通过 |
| 加载根目录 .env 后，从 backend 运行 `../.venv/bin/python manage.py test` | 260 个测试通过；包括新账户不可覆盖、权限、HTTPS 登录和 CSRF |
| `.venv/bin/python -m unittest discover -s deployment/family -p 'test_*.py'` | 3 个进程管理测试通过：仅关闭自有进程、保留无关进程、独占锁/控制 socket、组件退出检测 |
| 前端 `npm run lint`、`npm run format:check`、`npm run typecheck`、`npm run build` | 通过；构建在实际启动日志中 |
| 家庭设置 `manage.py check --deploy --fail-level WARNING` | 通过；明确排除临时域名不适用的 HSTS 子域策略/预加载两项 W005/W021，其他警告仍阻止启动 |
| Caddy 配置验证 | 通过；使用仓库要求的空格缩进，Caddy 自身的 Tab 格式建议不作为启动门禁 |
| 构建产物检查 | MIMO_API_KEY、试用 SECRET_KEY、试用密码均未出现在前端文件中 |
| `git diff --check`；`git check-ignore` | 通过；试用 `.env` 和 `.runtime` 均忽略提交 |

## 实际部署及公网验证

- 已安装 cloudflared 2026.9.1、Caddy 2.11.4、Gunicorn 25.3.0。
- 首次迁移前备份：`.runtime/backups/20260929-151923-828190.dump`。最后一次启动备份：`.runtime/backups/20260929-152731-529784.dump`。两者均使用 `pg_restore --list` 读取验证；没有执行破坏性恢复。
- 书目仍为 191 条（包含历史映射），已应用 28 个既有迁移；没有新增业务 schema 迁移。
- 家庭账户 `family-trial`：active/staff=True，superuser=False，后台权限数 0；凭据文件权限 0600。
- 回环监听：Gunicorn `127.0.0.1:8000`、Caddy `127.0.0.1:8080`；PostgreSQL 继续仅映射 `127.0.0.1:5432`。
- 公网 GET 健康检查、首页、后台登录页、后台 CSS 返回 200；未登录 `/baby/study/books` 返回 403；`/.env` 返回 404；非当次 Host 的网关请求返回 404。
- 首轮公网探测受本机环境代理影响发生部分超时；绕过环境代理后使用真实 HTTPS 完成全部业务验证。没有关闭 TLS 校验。
- 使用两个临时 staff 账户验证真实公网链路：Secure Session/CSRF Cookie、合法登录、缺 CSRF/非法 Origin 返回 403、书架写入后另一登录仍可读取。
- 同一临时账户通过公网逐题保存评估、恢复草稿、完成评估；尝试修改完成答案返回 409。
- 私有测试照片由拥有者读取返回 200 且 `Cache-Control: private, no-store`；匿名返回 403，另一 staff 返回 404；另一账户无法读取原账户书架。
- 上述临时账户、会话、书架、评估和私有测试照片均已清理；家庭账户没有测试画像或测试书架记录；没有为权限探测提交 AI 调用。
- 独立 worker 已运行并实际轮询既有供应商 Batch 任务（`processed=0 polled=1`）；供应商完整返回及实物照片新上传识别尚未验收。

## 生命周期验证

- 重复执行 `start.sh` 返回拒绝启动，原服务健康检查仍为 200。
- 实际执行 `stop.sh` 后，监督器和本次 Web、Caddy、worker、tunnel 退出，8000/8080 关闭，控制 socket 和当前地址文件移除。
- PostgreSQL 仍 healthy；无关的 5173 Node 进程 PID 38011 保持运行。
- 再次启动生成不同临时域名；原家庭凭据可在新域名 HTTPS 登录，书架为空，无 CSRF 错误。
- 最后保留服务运行。最新地址以 `deployment/family/.runtime/public-url.txt` 为准，不将会过期的地址作为永久配置提交。
- 最后运行日志：`deployment/family/.runtime/logs/20260929-152731-529784/`。

## 验收对应

| 验收 | 状态 | 证据/剩余事项 |
| --- | --- | --- |
| AC-001 | 通过 | 迁移前备份、pg_restore manifest、构建/迁移/静态日志、原书目计数 |
| AC-002 | 通过 | 回环监听、公网请求、部署门禁、前端密钥检查 |
| AC-003 | 通过 | 真实 HTTPS Cookie、合法写入、非法 CSRF/Origin 拒绝；未登录/其他用户私有资源拒绝 |
| AC-004 | 通过 | 账户权限检查及不可覆盖/重复启动测试 |
| AC-005 | 通过 | 实际重复启动、停止、重启；数据库和无关项目保持运行 |
| AC-006 | 部分通过 | 书架、评估、不变性、私有照片通过；worker 实际轮询运行；实物照片上传至真实识别完成仍待验证 |
| AC-007 | 未执行 | 已向用户请求大陆手机关闭 Wi-Fi 后测试访问，尚未收到反馈 |
| AC-008 | 通过 | 部署 README、主 README、脚本与实际启动过程一致 |

## 剩余与延期

- 家人实际使用移动网络访问、登录和操作，记录体验结果。
- 上传少量实物书籍照片，等待真实识别完成再人工确认；真实 Batch 结果同时沿用 004 验收记录。
- 固定域名、云服务器迁移、长期守护和大批上传分片均按本期范围延期。
