# 图书资料与家庭书架接口

2026-09-28，关联 [003 Spec](../specs/003-catalog-enrichment/spec.md) v0.1。路径均以 `/baby/study` 为前缀。保留会话认证、CSRF 和错误格式。GET 也须登录；写请求带同域会话及 `X-CSRFToken`。

## 查询

| 方法与路径 | 行为 |
| --- | --- |
| `GET /books?q=…&language=…` | 返回 `books` 和 `series`。q 匹配规范名、原文名、别名及所属系列名/别名；language 为 english/chinese/bilingual/unknown。系列成员清单不受筛选截断 |
| `GET /books/{id}` | 活动单本详情，包含全部书目、阅读声明与字段证据；已有拥有者亦可读取归档书详情，其他家庭不能读取归档书；历史套装映射不当作单本返回 |
| `GET /series/{id}` | 套装详情，包含完整已收录成员、阅读数量范围、内容及附件说明、字段证据 |
| `GET /shelf` | `{items: […]}`，当前用户已拥有的唯一单本记录，跨套装共享仍只有一条 |

单本列表保留 `id`、`title`、`language`、`series_id`、`series_name`、历史 `series_description`、`lexile_value`、`lexile_source_status`、`is_on_shelf`。新增 `original_title`、`aliases`、`series_ids`、`series` 摘要、`isbn`、`authors`、`illustrators`、`publisher`、`edition`、`cover_url`、`cover_source_url`、`description`、`themes`。旧主系列仍为成员时优先沿用，否则兼容主系列取最小 ID。成员表是维护依据。

单本详情再加 `translators`、`publication_date`（来源原始日期，不补造月日）、`binding`、`pages`、`reading_attributes`、`evidence`。未知文字为空串，未知列表为空数组，未知页数/蓝思为 null。原生级别不作跨体系换算。

阅读声明 `reading_attributes` 按 point_reading、publisher_age、distributor_age、native_level、lexile 等具名存储；每项有 value、status（confirmed/candidate）、source_url。发行商年龄与出版方年龄分别标明，不把 Excel 的年龄声明当作已核实出版方年龄。点读版本须具体匹配。蓝思只有已确认声明才返回数值/原文。

系列列表包含名称、说明、品牌、别名、套装 ISBN、封面/出处、出版方、版本、books（完整已收录成员）、`total_count`、`owned_count`、`expected_count` 和 `completeness`。系列详情再加 publication_date、contents 和 evidence。

- `total_count`：活动实际分册数；继续沿用旧语义。
- `owned_count`：当前用户拥有的实际成员数。
- `expected_count`：已核实读物总数，未知返回 null，附件不计入。
- `completeness`：unknown/partial/complete；成员数与已核实总数不一致时不会响应 complete。
- 零成员系列可提供已核实套装详情，但禁止批量加入。语言筛选只匹配有对应语言分册的系列。

字段证据 `evidence` 包含 field、value、source_url、source_label、status、collected_at。允许多个来源声明，供家长查出处；不会返回原表价格、购买渠道、个人备注或采集候选。原始导入记录只在授权 Django 后台查看。

## 写入

| 方法与路径 | 输入与结果 |
| --- | --- |
| `PUT /shelf/{book_id}` | `{ownership_status, reading_status}`；owned 必须配 unread/reading/familiar/paused，not_owned/unknown 配 null。返回原单本 ShelfItem 结构；归档书仅当前已拥有者可更新/移出，其他家庭不能新增归档书 |
| `POST /shelf/series/{series_id}` | 无业务字段，可发 `{}`；服务端在事务内取当前已收录活动分册。返回 added_count、already_owned_count、total_count |
| `POST /books` | 管理员 `{fields, idempotency_key?, add_to_shelf?}`；返回 `{book, created}`，具体版本复用且不覆盖已有资料；相同幂等 key 的不同内容为409 |
| `DELETE /books/{id}` | 管理员幂等归档，204；保留 ID、来源、成员和书架，活动目录/成员排除归档书 |

已拥有的书保留阅读状态及时间戳。缺少、曾移出、未知的记录加入为 owned/unread。重复加入无重复记录；共享分册加入另一套装时复用原记录。事务失败整体回滚，所有书架写请求通过用户行锁串行处理。以后新增成员不会自动上架。

界面对于完整系列显示“加入整个系列”；不完整系列显示“加入已收录 X 本”。全部已收录分册已拥有时禁用；完整系列部分拥有可“补齐系列”。成功后刷新目录与书架，显示本次实际加入数。一个系列中移出共享分册，会在所有所属系列同步消失；阅读状态亦同步。总数及评估书架数始终按唯一 Book 统计。

## 错误

错误结构为 `{error: {code, message, details?}}`。未登录/CSRF 无效为403，错误枚举/空系列为400 invalid_input，不存在的资源为404 not_found；沿用现有错误处理器。详情和写入失败在页面显示可访问 alert。

## 维护与导入

后台维护具体版本、成员顺序、来源与候选。直接修正的字段、来源声明及成员关系会锁定，后续采集不自动覆盖；可通过 manual_fields 明确解锁。成员关系修正时同时锁定总数/完整性。现有主系列字段只读，统一从成员表维护。

采集结果经明确版本/来源映射核对后才导入，不按标题相似自动合并。ISBN 校验10/13位并规范为13位；套装 ISBN 不复制给分册。错误版本、类型或未确认声明拒绝导入，其他原始行继续处理。书架引用或人工锁定的异常原记录保留现状，不传播状态。

## 2026-09-29：前台管理与照片任务（004）

管理员入口和三模态接入已经实施；详细 API 见 [004 公共契约](../specs/004-catalog-management-ai/contracts.md)，验收限制见 [validation](../specs/004-catalog-management-ai/validation.md)。新增 catalog_status/archived_at，以及 evidence.origin_kind/model_name；照片确认不冒充权威网页核实，详情不公开私有照片 ID/URL。

`GET /catalog-capabilities` 仅返回管理权限和实时/Batch 是否配置的 boolean；绝不返回凭据。`/book-intakes` 支持私有混合上传、任务列表/详情、照片查看、groups PATCH、enrichment、confirm、cancel 和 retry。全部管理员且按任务创建者隔离；写请求需要 CSRF。groups/enrichment/confirm/cancel/retry 检查 revision，资料冲突为409；确认全部所选组保持同一事务，重复确认返回保存映射。

所有照片默认 Batch，可显式选 realtime；worker 独立处理，页面关闭不影响持久化任务。仅重提失败照片，不重付成功项；提交不确定且未知供应商 ID 时阻止盲目重提，已知 ID 则只查询协调。供应商迟到结果不会自动入库。联网补全仅候选，字段应用和统一入库分别要求用户明确操作。

每批30张、单张8MiB、JPEG/PNG/WebP，服务端检查内容并清除EXIF。照片默认30天保留，`cleanup_intake_media` 保留文本及证据。三类实时能力和真实页面已联调；真实 Batch 下载仍等待供应商结果，不能把离线协议测试当作完整真实验收。
