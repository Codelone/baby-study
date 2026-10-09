# Cloudflare 家庭试用

本机运行 PostgreSQL、Gunicorn、照片识别 worker 和 Caddy，通过 Quick Tunnel 给家人一个临时 HTTPS 链接。家庭共用专用账户，书架和评估保存在现有本地数据库中；原管理员的个人书架和评估不会自动复制到新账户。

## 首次准备

在项目根目录操作。需要 macOS/Linux、Python 3.11、Node.js 22、Docker Compose，以及已经配置好的根目录 `.env`。当前脚本仅支持 README 中位于 `127.0.0.1:5432` 的 Compose PostgreSQL。

macOS 安装：

```sh
brew install cloudflared caddy
.venv/bin/python -m pip install -r backend/requirements.txt
```

Linux 从 [cloudflared 官方下载页](https://developers.cloudflare.com/tunnel/downloads/) 和 [Caddy 官方安装页](https://caddyserver.com/docs/install) 安装这两个程序，再安装 Python 依赖。

无需运行 `brew services start`；启动脚本会单独管理本项目的进程。请保证 Docker 已启动，8000 和 8080 可用，5173 可继续供其他项目使用。

## 启动与登录

```sh
deployment/family/start.sh
```

启动过程：检查工具和端口 → 启动/等待数据库 → **备份** → 迁移 → 创建/核对试用账户 → 安装和构建前端 → 收集后台静态资源 → 获取临时网址 → 检查部署安全设置 → 启动 Web、网关和独立识别 worker。

- 终端打印的 `https://….trycloudflare.com` 是分享地址；也保存在 `deployment/family/.runtime/public-url.txt`。
- 默认账号 `family-trial`；随机密码位于 `deployment/family/.env` 的 `FAMILY_TRIAL_PASSWORD`。将账号和密码单独告诉家人，**不要分享整个配置文件**。
- 页面点击“前往登录”。账户的 `is_staff=True`，能管理前台共享书目和使用照片识别；不是超级用户，没有 Django 后台模型权限。
- 家人共用该账户的一份书架、孩子档案和评估。另一个浏览器登录同一账户也能读取已保存内容。
- 根目录 `.env` 提供数据库和 MiMo 等服务配置；试用独立随机密钥、账户凭据保存在 `deployment/family/.env`，权限为 0600，已忽略提交。重复启动保留密码；同名账户密码或权限不一致时拒绝覆盖。
- 本次域名自动精确配置进 `ALLOWED_HOSTS` 和 `CSRF_TRUSTED_ORIGINS`。试用设置不会修改本地开发的 DEBUG、Cookie 或域名配置。
- 临时域名只使用 1 小时 HSTS，不声明子域策略或预加载。试用设置只排除对应的 `security.W005/W021` 提醒，其余部署安全警告会阻止启动。

保持启动终端运行、电脑开机联网。如需在后台持续运行（启动输出只含准备状态和路径；实际运行日志按组件轮转）：

```sh
mkdir -p deployment/family/.runtime
chmod 700 deployment/family/.runtime
nohup deployment/family/start.sh > deployment/family/.runtime/launcher.log 2>&1 < /dev/null &
```

使用 `status` 检查启动结果、控制入口、组件存活及本地/隧道状态，不要凭历史 PID 判断运行。可在另一个终端查看状态：

```sh
.venv/bin/python deployment/family/family.py status
```

## 停止与重新启动

```sh
deployment/family/stop.sh
```

也可在启动终端按 Ctrl+C。脚本通过权限为 0600 的本机 Unix socket 通知持有独占锁的监督器，再关闭该监督器启动的子进程组。不会按历史 PID 杀进程，不停止数据库或其他项目。重复启动会被拒绝；子进程意外退出会关闭本次其余组件。

再次执行 `start.sh`，以终端打印的新网址为准。旧链接停止后失效，换域名后需要重新登录；数据库和照片仍保留。不要强制杀死监督器：如果控制 socket 已失效，停止脚本会拒绝根据历史 PID 杀进程，应先人工核对占用的 8000/8080 进程和日志再处理。

## 试用检查

1. 手机关闭 Wi-Fi，使用大陆移动网络打开链接，检查首页、登录、书目和页面响应。
2. 添加书架、修改阅读状态，刷新或用另一个浏览器确认内容持久化。
3. 评估保存后离开再进入；提交后生成画像，已完成答案不可覆盖。
4. 上传少量实际书籍照片，确认任务保存、worker 处理，人工核对后确认入库。
5. 未登录不能访问书架、评估或私有照片；非任务所有者不能查看照片。
6. 停止后入口关闭，数据库和其他项目仍运行；重启后新链接可登录和写入。

真实模型任务与大陆网络体验必须实际验证。Batch 会等待供应商返回结果；MiMo 按实际调用计费，启动 worker 会处理现有数据库的待办识别任务。

## 上传和保留

- 试用建议每批最多 **10 张**，每张仍不超过 **8 MiB**，以留出 multipart 请求空间。大批照片分成多个任务上传。
- 服务现有的 30 张上限不代表 Cloudflare 允许 30 张最大尺寸照片一次上传；超出网关限制可能返回 413。[Cloudflare 上传限制](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/4xx-client-error/error-413/)
- 照片继续存放在本机私有目录，经已登录、任务所有者的 API 读取，没有开放静态照片目录。
- 默认照片保留 30 天，定期按项目 README 运行 `cleanup_intake_media`，删除到期照片、保留识别文本和资料来源。

## 日志、备份与恢复

运行采用 IPv4 和 HTTP/2 隧道，避免已观察到的 IPv6/UDP 路由故障；这不保证大陆网络速度。持续 `Tunnel not found` 30 秒会关闭本次组件并保留 failed 原因，重新启动后使用新地址。worker 每5秒接收新任务，每60秒查询Batch，新任务优先。

每次启动的日志位于 `deployment/family/.runtime/logs/<时间>/`，包括备份、迁移、账户、构建、隧道、网关、Web 和 worker。组件日志由监督器单写者按10 MiB轮转，最多保留5份历史文件；启动时清理30天前已结束运行的日志目录，数据库备份不参与日志清理。启动失败时终端会指出对应日志，自动清理本次进程。

查看当前或最近一次运行的日志（停止后仍可查）：

```sh
.venv/bin/python deployment/family/family.py logs --component tunnel --lines 100
.venv/bin/python deployment/family/family.py logs --component web --follow
.venv/bin/python deployment/family/family.py logs --component worker --follow
.venv/bin/python deployment/family/family.py logs --component gateway --lines 100
```

- `tunnel`：查IPv6/连接中断/`Tunnel not found`；`status` 的 tunnel 为connected/disconnected/not_found，组件存活不代表隧道可达。
- `gateway`：JSON访问日志，含status、duration与request_id；请求头、响应头不写入日志。
- `web`：`http_request` 含时间、方法、路径、状态、elapsed_ms与request_id；`photos_normalized` 记录图片处理耗时，`intake_created/intake_replayed` 把请求关联到任务。
- `worker`：按任务URL里的intake_id搜索；`intake_claimed` 的queue_ms是排队时间，`model_attempt_started/model_attempt` 是逐图调用及尝试次数，`photo_result_saved/intake_finished` 是保存和分组。`batch_polled` 显示供应商状态和错误码，worker_cycle每5秒记录心跳。

按任务查日志（将任务编号替换为实际UUID）：

```sh
rg '实际任务UUID' deployment/family/.runtime/logs/当前运行目录/worker.log*
```

上传超时或断网可能发生在服务器已保存之后。页面保留照片，同次提交重试沿用幂等键，先查看录入任务，同一次上传重试不会重复创建任务或再次触发识别；模型内部失败重试的用量仍以账单为准；改图片、模式或来源系列才生成新键。默认实时，Batch需主动选择，供应商等待不会自动转实时。日志不含密钥、Cookie或照片内容；包含真实任务编号和访问路径，仍请只在可信位置保存。

每次迁移前生成 `deployment/family/.runtime/backups/<时间>.dump`，目录权限为 0700；含真实数据，不要上传或提交。核对备份可读取：

```sh
docker compose exec -T db pg_restore --list < deployment/family/.runtime/backups/<时间>.dump
```

**恢复会替换当前数据库的数据**。仅在确认需要回退、已保留当前数据并停止试用后执行，将 `<时间>` 替换为选定备份：

```sh
deployment/family/stop.sh
docker compose exec -T db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_restore --clean --if-exists --no-owner --exit-on-error --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' < deployment/family/.runtime/backups/<时间>.dump
```

数据库备份不包含私有照片；照片仍在 `INTAKE_MEDIA_ROOT` 或默认 `backend/private-media/`，应在需要整机恢复时另行备份。根目录 `.env` 和试用 `.env` 也只在可信位置保留。

## 可用性与后续

Quick Tunnel 不需要域名和服务器，免费提供随机 HTTPS 地址，不保证长期可用性。电脑休眠、关机或网络中断都会影响访问。[官方说明](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)

大陆访问是否顺畅以家人的移动网络实测为准。长期使用时将同样的 Web、worker、数据库和存储迁到云服务器，配置固定域名、正式 Tunnel、持续备份和进程守护；本期不迁移到 Workers，也不提供大批上传分片。

需求和验收记录见 [005 Spec](../../specs/005-family-cloudflare-trial/spec.md)、[Plan](../../specs/005-family-cloudflare-trial/plan.md)、[Validation](../../specs/005-family-cloudflare-trial/validation.md)。
