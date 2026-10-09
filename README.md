# Baby Study

儿童英语阅读辅助产品。当前已实现版本级书目、联网采集与核对、多个套装共享分册、批量加入、前台管理员新增/归档、照片识别草稿与可选联网补全、家庭书架、初始评估与可追溯的状态画像；选书推荐、计划与 Agent 尚未实现。产品与技术约定见 [产品讨论/product.md](产品讨论/product.md) 和 [产品讨论/技术方案.md](产品讨论/技术方案.md)。业务 API 固定使用 `/baby/study` 前缀。

## 环境要求

- Python 3.11（本次验证版本；其他版本需符合 Django 5.2 的支持范围）
- Node.js 22 和 npm
- Docker 与 Docker Compose，用于本地 PostgreSQL

## 本地启动

在项目根目录复制 `.env.example` 为 `.env`，然后在运行后端的终端加载环境变量：

```sh
cp .env.example .env
set -a
source .env
set +a
```

启动数据库并安装后端依赖：

```sh
docker compose up -d db
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt
.venv/bin/python backend/manage.py migrate
.venv/bin/python backend/manage.py createsuperuser
```

在第一个终端运行后端：

```sh
.venv/bin/python backend/manage.py runserver 127.0.0.1:8000
```

在第二个终端运行前端：

```sh
cd frontend
npm ci
npm run dev
```

打开 `http://127.0.0.1:5173/`，点击“前往登录”，用刚创建的管理员账户登录。“系统书目”支持搜索单本或系列：可以逐本加入，也可以批量加入系列已收录分册，已有书籍的阅读状态会保留。家庭书架按系列折叠，展开后逐本管理。也可在“初始评估”回答问卷；离开后能继续草稿。提交后可查看各项画像的回答依据、确认画像，或修改答案生成新版本。空书架不妨碍完成评估。管理员点击“新增图书”可手工新增、上传图片（支持多选）或查看录入任务，在图书详情中归档。管理员可从单本详情、录入表单或系列卡片进入“维护系列”，新建/编辑系列、关联已有书、移出成员与调整顺序；系列下的“添加图书”复用手工和照片录入。照片任务保存来源系列，核对时可逐本改选，确认后统一关联。未知总册数留空，数量冲突会提示，完整性需要明确核实。登录、书架、评估和后台共用同域会话；本地请始终使用 `127.0.0.1`，避免 `localhost` 与 `127.0.0.1` 的 Cookie 不一致。

迁移初始化的 19 个原始 ID、来源和书架状态保留。2026-09-28 已对原表全部 66 行联网核对，39 行导入可靠资料，27 行保留候选与原因。当前有 190 条活动单本、1 条历史映射、27 个系列；177 条单本与25个系列保存封面 URL。已确认误建的 My First Book of Words 套装保留为历史映射，不传播拥有/阅读状态。`super hammy 1/2` 具体套装尚未确认，暂保留旧记录。逐行覆盖和缺失见 [全表核对报告](imports/catalog-review.md)。

同一版本的书可属于多个套装，复用同一 ID 和状态。书架在各所属系列展示共享分册，总计按唯一单本计算。加入系列只处理当时已录入的分册，语言筛选不会缩减范围；新增分册需家长主动补齐。不完整系列显示“加入已收录 X 本”，无已核实分册时禁止加入。封面缺失或加载失败显示书名占位，详情弹窗可查资料和来源。需求见 [图书资料 Spec](specs/003-catalog-enrichment/spec.md)、[计划](specs/003-catalog-enrichment/plan.md)。

## 前台照片录入与 MiMo

加载服务端 `.env`、应用迁移后，在第三个终端运行独立 worker；Web 请求只上传和保存任务，页面无需持续打开：

```sh
set -a
source .env
set +a
.venv/bin/python backend/manage.py process_book_intakes --interval 5 --batch-interval 60
```

也可用 `--once` 单轮处理。每批最多 30 张、单张 8 MiB，JPEG/PNG/WebP。新任务默认实时，可明确选择省钱 Batch；已有 Batch 不自动转实时。照片识别、分组及联网检索只生成草稿，必须核对后统一确认。确认可选择加入个人书架；归档不会改变既有阅读状态，已有拥有者仍可查看及管理其书架记录。任务和照片按创建管理员隔离。

`MIMO_API_KEY` 仅放未跟踪 `.env`，文字/图片/音频分别用 `MIMO_TEXT_MODEL`、`MIMO_IMAGE_MODEL`、`MIMO_AUDIO_MODEL`，默认 `mimo-v2.6-flash`。实时与中国区 Batch 地址分别配置，三类实时能力已真实验证；Batch 已真实提交/查询，供应商结果下载仍待返回。页面分别显示供应商返回 tokens 和覆盖项的人民币费用估算；暂无返回不代表零费用，金额以 MiMo 账单为准。

`MIMO_COST_PRICING_REGION=cn` 使用 2026-09-29 核验的国内 Flash/Pro 价目，Batch 单价为实时的一半，输入统一按未命中缓存估算；未覆盖的失败、重试或尚未返回调用不计入该估算。设置为空或其他地域时不计算人民币金额；价格变化需更新 `catalog/intake/costs.py` 中价目和日期。页面提供官方价格来源，未知模型/缺失输入或输出用量不以零计费表示。

可选联网目前使用 Open Library 的具体版本与作者记录，不把作品级结果直接写入，不覆盖草稿字段。公开来源不可达时可配置服务器的 `INTAKE_ENRICHMENT_PROXY_URL`；不会把 MiMo 凭据发给公开书目服务，也不使用 `.netrc`。中文/点读版覆盖可能不足，仍可补拍或手工填写。

私有照片默认保留 30 天；需要定期运行以下清理命令，识别文本/来源及书架保持不变：

```sh
.venv/bin/python backend/manage.py cleanup_intake_media
```

配置上限及保留时间见 `INTAKE_MAX_PHOTOS`、`INTAKE_MAX_PHOTO_BYTES`、`INTAKE_MEDIA_RETENTION_DAYS`、`INTAKE_MEDIA_ROOT`。若本地 5173 被其他项目占用，前端可运行 `npm run dev -- --port 5174 --strictPort`，服务端 `CSRF_TRUSTED_ORIGINS` 增加 `http://127.0.0.1:5174` 后重启；主机仍统一使用 `127.0.0.1`。本次联调实际使用 5174。

规格/公共契约及待验收项见 [004 Spec](specs/004-catalog-management-ai/spec.md)、[实施计划](specs/004-catalog-management-ai/plan.md)、[验收记录](specs/004-catalog-management-ai/validation.md)。

## 可复用资料采集与导入

加载 `.env` 并执行迁移后，在根目录运行：

```sh
.venv/bin/python backend/manage.py collect_catalog 图书列表.xlsx
.venv/bin/python backend/manage.py import_catalog --dry-run
.venv/bin/python backend/manage.py import_catalog --apply
.venv/bin/python backend/manage.py report_catalog
```

`collect_catalog` 默认读取 `imports/catalog-sources.json`，写入 `catalog-enriched.json`，缓存放在忽略的 `imports/.cache/`。相同原始记录及映射自动续跑复用；用 `--refresh` 重新请求，`--mapping`、`--output`、`--cache` 可换路径。每域默认两秒最多一次请求，有限重试并遵守 Retry-After；单源失败继续其他行。只采集网页/公开书目接口，不下载音频或批量下载封面。Open Library 搜索仅作为作品候选，具体 edition 未核实不导入版本资料，封面按官方推荐 URL 展示。

人工在来源映射中核对 `confirmed`、`edition_verified`、身份标识、字段和分册/附件范围，随后续跑采集。标题相似不合并，单本 ISBN 不复制自套装；来源 ID 与原始行号映射必须明确同版本。`import_catalog` 默认不写库；`--apply` 每行事务导入，并生成导入结果。重复运行复用 ID 和证据。后台修改的字段自动加入 `manual_fields`，后续导入跳过；确认需要恢复自动更新时可在后台移除锁定字段。

`report_catalog` 生成 Markdown 与 JSON 逐行覆盖、来源、成员、封面、缺失和失败原因。`catalog-enriched.json` 含原表购买资料，仅供本地核对，不应公开发布；业务 API 不返回这些内容。完整接口说明见 [图书接口文档](产品讨论/catalog-api.md)。

## 图书与书架接口

均以 `/baby/study` 为前缀，要求登录，写请求需要 CSRF。

| 方法与路径 | 输入与行为 |
| --- | --- |
| `GET /books` | 可选 `q`（书名或系列名）、`language`；保留 `books` 单本结果并新增 `series` 系列结果。单本增加 `series_ids`、系列摘要、封面与基本资料；`series_id`、`series_name`、历史 `series_description` 等保持兼容 |
| `GET /books/{id}`、`GET /series/{id}` | 返回完整书目、阅读属性、成员及字段证据；来源含 URL、采集时间与核验状态 |
| `GET /shelf` | 保持 `items` 单本结构，返回当前用户 `owned` 的记录；单本含系列 ID 供界面分组 |
| `PUT /shelf/{book_id}` | 幂等设置单本 `ownership_status`、`reading_status`；拥有时需要阅读状态，未拥有/未知时必须为 `null` |
| `POST /shelf/series/{series_id}` | 无业务请求字段（可发送 `{}`）；由服务端确定已收录成员，原子加入缺少的分册，保留已拥有状态。返回 `added_count`、`already_owned_count`、`total_count` |

`series` 的 `total_count` 表示实际已收录成员数，`expected_count` 是已核实读物总数（未知为 `null`），`completeness` 为 unknown/partial/complete。`owned_count` 按当前用户统计。成员完整清单不受语言筛选截断；空系列可显示已核实套装资料但不提供加入入口。加入空系列返回 400 `invalid_input`；不存在返回 404 `not_found`。重复加入不改变已有阅读状态或更新时间；曾移出/未知分册重新加入为 `owned/unread`。所有书架写入通过用户行锁串行处理。旧 `series_id` 优先原主系列（仍为成员时），否则取所属系列最小 ID。

健康接口为 `http://127.0.0.1:8000/baby/study/health`，返回 `{"status":"ok"}`。Vite 开发服务器会把 `/baby/study`、`/admin` 和 `/static` 请求转发至后端。

如果页面无法加载书目，先确认后端和 PostgreSQL 正在运行，且已经执行迁移。健康接口本身不查询数据库。

## 初始评估接口

问卷版本为 `2026-09-v1`，画像规则版本为 `2026-09-v1`。所有评估接口要求登录；写入请求需要 CSRF。

| 方法与路径（均以 `/baby/study` 为前缀） | 用途 |
| --- | --- |
| `GET /learner` | 读取单孩子档案及出生年月 |
| `GET /assessment/questions?session_id=…` | 读取当前条件下可见的题目与选项 |
| `GET /assessments/draft`、`GET /assessments` | 恢复草稿、查看评估历史 |
| `POST /assessments` | 创建草稿；提交 `questionnaire_version`，可选 `copy_from_id` 复制已完成答案；现有草稿会复用 |
| `GET/DELETE /assessments/{id}` | 读取评估；仅未完成草稿可删除 |
| `PUT /assessments/{id}/responses/{question_id}` | 以 `{"answer_value": …}` 逐题保存答案 |
| `POST /assessments/{id}/complete` | 检查当前可见题目并生成不可覆盖的画像版本；重复提交返回同一版本 |
| `GET /state/current`、`GET /states/{id}` | 读取当前或历史画像，包括各项结论所引用的题目和答案 |
| `POST /states/{id}/confirm` | 家长确认当前画像 |

Q11 只在满 3 岁或报告使用分级读物时出现；A/B/C 分支由 Q06、Q07 的具体行为决定。未显示题目的旧草稿答案不参与画像。缺少证据不会转成负面能力结论；分级读物级别只保留家长陈述的原生值。

## 检查、测试与构建

在项目根目录运行后端检查与测试：

```sh
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
.venv/bin/python backend/manage.py check
```

从 `backend/` 目录运行 Django 测试，以便发现所有应用的测试。测试会创建临时 PostgreSQL 测试库：

```sh
cd backend
../.venv/bin/python manage.py test
```

在 `frontend/` 目录运行前端检查与构建：

```sh
npm run lint
npm run format:check
npm run test:requests
npm run typecheck
npm run build
```

前端构建产物在 `frontend/dist/`。Django 无需编译；需要收集后台静态资源时运行 `.venv/bin/python backend/manage.py collectstatic --noinput`，产物在 `backend/staticfiles/`。

## Cloudflare 家庭试用

本机服务可通过 Cloudflare Quick Tunnel 的临时 HTTPS 链接分享给家人，无需购买域名或服务器。安装 Caddy、cloudflared 和后端依赖后，在根目录执行：

```sh
deployment/family/start.sh
```

脚本先备份数据库，再构建前端、收集静态资源并启动 Gunicorn、Caddy 和照片识别 worker；自动配置当次域名和 HTTPS 安全设置。终端显示试用链接，家庭共用账户的随机密码保存在忽略提交的 `deployment/family/.env`。电脑需要保持开机联网；停止使用 `deployment/family/stop.sh` 或 Ctrl+C，数据库和其他项目继续运行。

安装、账号、备份恢复及上传限制见 [家庭试用说明](deployment/family/README.md)。本配置用于短期家庭试用，长期云服务器部署另行安排。

## 环境变量

`.env.example` 包含本地开发示例值；`.env`、前端依赖和构建产物不会提交到版本库。公开部署前必须更换数据库密码与 `DJANGO_SECRET_KEY`，并设置正确的域名、HTTPS 与数据库备份。前端不保存服务端密钥。

照片上传重试、性能与日志说明见 [007 Spec](specs/007-photo-intake-diagnostics/spec.md)、[Plan](specs/007-photo-intake-diagnostics/plan.md) 和 [家庭部署排查说明](deployment/family/README.md)。
