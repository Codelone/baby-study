# 004 开发公共契约

2026-09-29，实施基线 v0.5。主会话管理代码集成和用户澄清；各开发会话独立工作树，提交功能 commit 后由主会话集成。本文为实施方案，不改动用户确认的范围。所有业务 URL 以 `/baby/study` 开头。管理员为 `is_staff` 或 `is_superuser`；所有管理/识别接口还要求登录、CSRF 和任务所有者校验。

## 负责范围

- 主会话：`catalog/models.py`、全部新迁移、`catalog/urls.py`、intake API/序列化与确认事务、任务 worker 编排、配置中媒体段、集成验证和 spec/plan/index。
- 书目会话：`catalog/services.py`、现有 `catalog/views.py`/`serializers.py`、独立管理测试；不改 models/urls/migrations。
- 模型会话：`ai/client.py`、`ai/tests.py`、settings 的 MIMO 配置及 `.env.example` 的模型段；不改 catalog。
- 分组会话：`catalog/intake/recognition.py`、`grouping.py`、对应独立测试；纯提取/分组函数，不改 ORM/API。
- Batch 会话：`ai/batch.py`、批量协议固定样本测试；纯供应商适配，不改 ORM/worker。
- 联网会话：`catalog/intake/enrichment.py`、固定来源测试；返回候选，不直接写库。
- 前端会话：全部 `frontend/src` 必要变更；不改后端及文档。

## 数据模型（主会话提供）

- `Book.catalog_status`: active / legacy / archived；新增 `archived_at`。归档保留关系、书架和历史，活动查询过滤 active。
- `SourceEvidence`: 现有字段保留；新增 `origin_kind` (web/manual/photo)、`photo` (可空引用 IntakePhoto)、`model_name`、`confirmed_by` (可空用户)；source_url 允许空。照片不伪装成网页证据，人工确认不自动成为权威分级依据。
- `BookIntake`: UUID id，user，mode (batch/realtime)，status (queued/processing/awaiting_confirmation/partial_failure/failed/cancelled/confirmed)，revision (从 1 开始)，groups (JSON 列表)，unassigned_photo_ids (JSON 列表)，errors (JSON 列表)，progress (JSON)，confirmed_books (JSON)，generation (从 1 开始)，has_manual_edits，lease_expires_at/lease_token，created_at/updated_at。租约使用独立 token，旧执行者不能回填或释放新租约。
- `IntakePhoto`: UUID id，intake，file (私有 FileField)，original_name，sha256，status (pending/processing/succeeded/failed)，extraction (JSON)，error (JSON)，created_at。同一 intake+sha256 唯一，重复照片复用。
- `IntakeBatch`: UUID id，intake，generation，status，input_file_id，provider_batch_id，output_file_id，error_file_id，requests (custom_id/photo_id 映射 JSON)，errors (JSON)，created_at/updated_at。供应商提交状态不明确时不盲目重提。

原始媒体只通过鉴权接口返回，无公开静态 media URL。默认单批最多 30 张、每张 8 MiB、JPEG/PNG/WebP；服务端验证文件内容并清除 EXIF，保留可读分辨率。配置可覆盖。私有照片默认保留 30 天，清理命令只清除到期媒体，保留识别文本及来源记录；已确认书目不依赖文件永久存在。

## 图书资料字段

`fields` 支持 title、original_title、language、isbn、authors、illustrators、translators、publisher、publication_date、binding、pages、edition、description、themes、cover_url、cover_source_url。未知字符串为空、列表为空、数值为空值；语言为 english/chinese/bilingual/unknown。不接受无来源的 reading_attributes/蓝思值，不自动创建套装分册。

分组结构：

```json
{
  "id": "稳定字符串",
  "photo_ids": ["照片UUID"],
  "fields": {"title": "可见书名", "isbn": "", "language": "unknown"},
  "evidence": [{"field": "title", "value": "可见书名", "kind": "photo", "photo_id": "照片UUID", "source_label": "封面识别", "model_name": "实际模型"}],
  "issues": [],
  "existing_book_id": null,
  "selected": true
}
```

网页 evidence 用 kind=web、source_url/source_label；人工修改用 kind=manual。分组可合并、拆分、移动照片，不强行把无归属照片合并到相似书名。每次用户编辑校验 revision 并加一；迟到模型结果不覆盖编辑。

## 纯服务接口

- `MimoClient.from_settings()`；`build_chat_body(messages, capability='text', json_output=False, max_completion_tokens=...) -> dict`；`chat(messages, capability='text', json_output=False) -> 完整供应商响应 dict`。`ModelAPIError(code,message,retryable,status_code)`。MIMO_API_KEY/MIMO_BASE_URL/MIMO_BATCH_BASE_URL 与文字/图片/音频模型分别配置；默认 flash。
- `validate_extraction(payload: dict) -> dict`：规范化字段、photo_type、isbn_kind、identity_text、issues，不凭猜测补事实。
- `recognition_messages(data_url: str) -> list`：图片提取消息，要求 JSON。`extract_photo(client, data_url: str) -> dict`：调用上述 chat 并解析/验证。
- `group_photos(photos: list[dict]) -> dict`：输入每项 `{id, extraction}`，返回 `{groups, unassigned_photo_ids}`；同名异版/同套装分册分开，稳定分组 id，字段证据可追溯。
- `BatchClient.from_settings()`；`submit(requests: list[dict], name='') -> dict` (含 input_file_id、id/status)；`retrieve(batch_id) -> dict`；`results(file_id) -> list[dict]`；`cancel(batch_id) -> dict`。每个请求为 OpenAI JSONL 原样的 custom_id/method/url/body。不得无限重试不幂等提交。
- `search_candidates(fields: dict) -> dict`：返回 `{candidates: [{id, fields, evidence, issues}], issues: []}`。通过实际公开检索/来源查询获取候选；没有可靠来源就返回不足，不把模型记忆当检索。封面仅使用来源可查 URL，不下载公开远程封面。
- `create_book(user, fields, idempotency_key='', evidence=None, existing_book_id=None) -> (book, created)`：服务内验证、重复版本核对和事务；明确现有 book 时复用，不覆盖人工资料。
- `archive_book(user, book_id) -> Book`：幂等归档。所有管理员服务验证权限。

## API 契约

- `GET /catalog-capabilities` -> `{can_manage_catalog, realtime_configured, batch_configured}`，均 boolean，绝不返回密钥。
- `POST /books` JSON `{fields, idempotency_key?, add_to_shelf?}` -> `{book: BookDetail, created: boolean}`；管理员。`DELETE /books/{id}` -> 204，幂等归档。
- `GET /books/{id}` 对活动书返回详情；归档书仅已有拥有者可读。归档后已有拥有者可以更新/移出书架，其他家庭不能新增该归档书；私有照片仍不在公开详情中返回。
- `POST /book-intakes` multipart，多次 files 字段，mode 默认 batch；响应 201 `{intake: Intake}`。仅写上传/任务，不在请求中长时间等模型；独立 worker 处理 batch/realtime。
- `GET /book-intakes` -> `{intakes: Intake[]}`；`GET /book-intakes/{uuid}` -> `{intake: Intake}`。
- `GET /book-intakes/{uuid}/photos/{photo_uuid}` -> 已验证图片二进制，管理员及所有者校验；过期已清理则 404。
- `PATCH /book-intakes/{uuid}/groups` JSON `{revision, groups, unassigned_photo_ids}` -> `{intake: Intake}`；photo_ids 必须属于任务且不重复，冲突 revision 返回 409 `revision_conflict`。
- `POST /book-intakes/{uuid}/enrichment` JSON `{revision, group_ids}` -> `{intake: Intake, candidates: {group_id: CandidateResult}}`；只返回候选并保存到各组 enrichment_candidates，更新 revision；用户自行选择/编辑后 PATCH，不自动填最终书目。
- `POST /book-intakes/{uuid}/confirm` JSON `{revision, group_ids, add_to_shelf:false}` -> `{intake: Intake, books:[{group_id, book_id, created}]}`；所选组先全部校验再事务写入，其他疑点组可排除。重复已确认请求返回保存映射，不重复写书架或书目。此后不可编辑/重新确认不同内容。
- `POST /book-intakes/{uuid}/cancel` / `retry` JSON `{revision}` -> `{intake: Intake}`；只重提失败项，成功提取不重付费；已取消任务迟到结果不自动入库。

`Intake` 包含 id/mode/status/revision/generation/groups/unassigned_photo_ids/errors/progress/confirmed_books/created_at/updated_at，以及 photos 列表，每项 id/original_name/status/error/url (鉴权图片 URL)/extraction。其错误格式保留 `{code,message}`，用语可理解，不含请求密钥、完整音频或供应商原始敏感日志。

extraction 保留实际 model_name/usage；usage 的 prompt_tokens/completion_tokens/total_tokens 可缺失，缺失不是零。识别 issues 为 `{code,message,field?,photo_id?}`，联网检索 issues 为文字列表。groups 可包含 existing_book_candidates 和 enrichment_candidates；选择已有版本必须明确，不能因标题一致自动复用。合并/拆分后服务端从可信历史声明/提取结果保留照片来源；列表声明可支持最终列表的子集，但不把单个来源原值改成全部聚合值。

Intake 的 `cost_estimate` 与供应商实际 tokens 分开：`{currency:'CNY',amount:decimal_string|null,priced_photos,total_photos,pricing_date,source_url,assumption:'uncached_input',mode}`。只计算已返回有效输入/输出用量且型号有核验价目的照片；所有输入按未命中缓存价计算。当前国内 Flash 实时输入/输出为 1/2 元每百万 tokens，Pro 为 3/6 元，Batch 为对应实时单价的一半，价格日期 2026-09-29；`MIMO_COST_PRICING_REGION` 非 cn 时不估算。无可估算项为 null，实际返回 0 tokens 才可显示 0；失败、重试和未返回调用可能不在覆盖项内，不能称为任务总费用或账单。

worker 命令：`process_book_intakes --once` 或持续处理。queued 实时任务逐图提取；queued Batch 上传 JSONL 并保存映射；后续轮次查询结果并回填照片。全部当前照片完成后自动归组，等待用户确认。部分失败保留成功照片和草稿并允许失败项重提。自动归组不覆盖人工 revision；用户可以在未完成任务上查看进度，编辑在识别阶段结束后开放。清理命令：`cleanup_intake_media`。

## 验证与交付

每个会话提交自己负责文件并报告 commit SHA、工作树、测试结果。使用根项目共享 .venv/Node 依赖（不得输出 .env）；数据库测试可能使用独立 TEST NAME，避免并发争用 test_baby_study。不要修改 spec/plan；涉及需求变化或需要用户确认时发给主会话。只有主会话进行真实 API 调用、迁移和集成验证，不暴露 API_KEY。Batch 地址待用户提供时继续实现/离线测试，不假造真实通过。
