# PostgreSQL ER 与 Agent 工具契约 v0.1

> 产品：1.5～6 岁儿童英语分级阅读辅助工具  
> MVP 闭环：现状了解 → 出具周计划 → 家长反馈 → 学习状态更新 → 动态调整计划  
> 配套 DDL：[`db/schema-v0.1.sql`](db/schema-v0.1.sql)  
> 配套图：[`diagrams/mvp-er-agent-loop.svg`](diagrams/mvp-er-agent-loop.svg)

## 1. 本文解决什么问题

本文把领域本体转换为第一版可以实现的 PostgreSQL 模型、事务边界、Agent 工具和 REST API。目标不是一次性建完所有教育知识，而是先可靠支撑一个可解释、可回滚、可持续迭代的家庭学习闭环。

已确认的产品决策：

- 服务 1.5～6 岁儿童，低龄阶段不要求孩子答题、跟读或录音；
- MVP 只做现状了解、计划、反馈、状态更新和计划迭代；
- 计划周期按周；首版一个家庭主要服务一个孩子，但数据模型支持多个孩子；
- 家庭可使用牛津树、RAZ 等不同体系；蓝思值仅作跨体系参考，不作为唯一主等级；
- 轻微调整通过策略校验后可自动执行；阶段、主等级、目标和明显工作量变化必须由家长确认；
- 资源只要求先有标题、语言、已知分级和是否拥有；其他元数据可以由 Agent 建议、家长逐步确认；
- 当前书架不完整不阻塞 MVP。可以先确认 5～10 本种子书，之后通过“添加图书”持续补充。

## 2. 为什么 MVP 只用 PostgreSQL

当前核心数据是家庭、孩子、资源、状态版本、计划版本、反馈、提案和审计记录，关系明确且写入一致性要求高。PostgreSQL 已能提供：

- 外键、唯一约束和事务，保证计划与反馈不会写成半套数据；
- JSONB，承载仍在演化的问卷答案、状态维度和解释；
- 数组和普通索引，支持首版规则与候选筛选；
- 版本表和审计表，支持解释、回放与纠错；
- 行锁与乐观锁，防止家长和 Agent 同时修改时相互覆盖。

MVP 不引入图数据库。领域概念之间虽然构成“本体关系”，但首版查询深度有限，关系表和递归 CTE 足够。MVP 也不依赖 `pgvector`；等资源规模扩大且确实需要语义召回时，再以旁路索引加入，不改变事实数据模型。

## 3. 总体边界

系统分成六个领域边界：

| 边界 | 聚合根 | 负责内容 | 不负责内容 |
|---|---|---|---|
| 家庭与身份 | `family`、`learner` | 家庭成员、孩子、目标和硬约束 | 身份提供商实现 |
| 资源目录 | `resource`、`family_resource` | 书目、版本、分级来源、家庭是否拥有 | 完整电子书内容与版权 |
| 现状了解 | `assessment_session` | 问卷、回答、初始证据 | 医疗或发育诊断 |
| 学习状态 | `learner_state_version` | 带证据和置信度的版本化状态 | 原地覆盖历史状态 |
| 计划与执行 | `learning_plan` | 周计划、版本、任务、活动和反馈 | 任意长期课程一次性生成 |
| Agent 治理 | `agent_run`、`change_proposal` | 工具调用、策略判断、家长决策、审计 | 让模型直接写数据库 |

Agent 只能看到本文定义的领域工具，不获得 SQL 执行工具和数据库账号。所有读取都自动限定家庭范围，所有写入都经过鉴权、参数校验、策略校验、幂等控制和审计。

## 4. 核心表与关系

### 4.1 家庭、孩子、目标和约束

| 表 | 关键字段 | 说明 |
|---|---|---|
| `family` | `id`、`timezone` | 数据隔离的顶层边界 |
| `app_user` | `id`、`external_auth_subject` | 家长或照护者账号 |
| `family_membership` | `family_id`、`user_id`、`role` | 成员对家庭的权限 |
| `learner` | `family_id`、`birth_date` | 孩子；年龄由生日动态计算 |
| `learning_goal` | `goal_type`、`priority`、`status` | 家长当前目标 |
| `learning_constraint` | `constraint_type`、`value`、`is_hard` | 时长、频率、禁用活动等限制 |

### 4.2 资源和多分级体系

| 表 | 关键字段 | 说明 |
|---|---|---|
| `resource` | `scope`、`title`、`language_codes`、`metadata_status` | 全局或家庭私有的资源实体，允许信息不完整 |
| `resource_alias` | `normalized_alias` | 同一本书的别名和导入名称 |
| `resource_source` | `authority_level`、`raw_payload` | 官方、出版方、第三方、家长或模型估算的来源记录 |
| `family_resource` | `ownership`、`usage`、`lock_version` | 家庭是否拥有以及使用状态；决定计划资格 |
| `level_system` | `code`、`system_type` | 牛津树、RAZ、Lexile 等体系 |
| `level_node` | `level_system_id`、`code`、`sort_order` | 某体系中的一个等级 |
| `resource_level_assignment` | `assignment_kind`、`confidence`、`confirmation` | 资源与一个或多个分级的带来源关联 |

`Lexile` 的 `system_type` 是 `cross_system_measure`。牛津树、RAZ 等原生体系保留自己的等级，不被强制换算为一个“真实统一等级”。跨体系值必须注明来源、置信度和是原生值、对照值还是估算值。

### 4.3 评估、证据与状态

| 表 | 关键字段 | 说明 |
|---|---|---|
| `assessment_session` | `questionnaire_version`、`status` | 一次现状了解过程 |
| `assessment_response` | `question_id`、`answer_value` | 原始回答，不丢失“不确定” |
| `learning_evidence` | `dimension`、`value`、`confidence` | 从回答、活动和反馈产生的原子证据 |
| `learner_state_version` | `version_no`、`dimension_values`、`state_status` | 不可覆盖的孩子状态版本 |
| `learner_state_evidence` | `evidence_role` | 为状态结论提供可校验的证据引用 |

### 4.4 计划、执行和反馈

| 表 | 关键字段 | 说明 |
|---|---|---|
| `learning_plan` | `period_start`、`period_end`、`current_revision_id` | 一周计划聚合根 |
| `plan_revision` | `revision_no`、`state_version_id`、`status` | 每次调整的新版本 |
| `plan_revision_evidence` | `evidence_role` | 触发或支持调整的证据 |
| `learning_task` | `task_role`、`scheduled_date`、`stopping_rule` | 一次家庭可执行的共读任务 |
| `task_resource` | `family_resource_id`、`resource_snapshot` | 计划时使用的资源快照 |
| `learning_session` | `task_id`、`completion` | 一次实际执行 |
| `parent_feedback` | `completion`、`engagement`、`original_text` | 家长原始反馈与结构化候选 |

### 4.5 Agent、提案、决策和审计

| 表 | 关键字段 | 说明 |
|---|---|---|
| `agent_run` | `run_type`、`prompt_version`、`input_snapshot` | 一次可重放的 Agent 运行 |
| `agent_tool_call` | `request_id`、`tool_name`、输入/输出 | 每次受控工具调用 |
| `change_proposal` | `risk`、`base_*_version_id`、`payload` | 尚未执行或已自动执行的变更提案 |
| `parent_decision` | `decision`、`comment` | 高影响提案的家长决定 |
| `audit_event` | `actor_type`、`before_data`、`after_data` | 领域写入审计 |
| `outbox_event` | `event_type`、`status`、`available_at` | 与写事务一起保存的异步事件，可靠触发后续 Agent 处理 |
| `idempotency_record` | `operation`、`idempotency_key`、`request_hash` | 防止重试造成重复写入 |

## 5. 必须成立的不变量

### 5.1 数据库直接保证

- 一个孩子最多只有一个 `active` 状态版本；
- 一个孩子最多只有一个 `active` 周计划；一个计划最多只有一个 `active` 版本；
- 状态的前一版本必须属于同一个孩子；计划的前一版本和当前版本必须属于同一个计划；
- `resource_level_assignment.level_node_id` 必须属于同一个 `level_system_id`；
- `owned` 和 `not_owned` 必须记录确认人和确认时间，`candidate` 和 `unknown` 不得伪装成已确认；
- 状态与计划的证据通过关联表引用真实 `learning_evidence`；
- 同一家庭、操作和幂等键只能产生一条写入记录；
- 更新 `resource`、`family_resource` 或 `learning_plan` 时自动增加 `lock_version`。

### 5.2 领域服务保证

以下规则跨越多个聚合，不用复杂触发器隐藏业务逻辑，由领域服务在同一事务中校验：

- 用户、孩子、资源、计划和任务必须属于当前认证家庭；
- `primary_goal_id` 必须属于计划中的孩子；
- 计划引用的状态版本必须属于同一个孩子；
- `task_resource` 必须是该孩子家庭的资源；只有 `ownership=owned` 才能直接进入正式计划；
- `learning_session.learner_id` 必须与任务所属计划的孩子一致；
- 已完成或家长锁定的任务不能被自动调整；
- 原始评估回答、原始反馈、学习证据和已经生效的历史版本只追加，不原地修改；纠错创建新记录并用 `supersedes_*` 指向旧记录；
- `major` 变更不得在没有有效 `parent_decision=accept` 的情况下生效。

## 6. 一致性与事务边界

### 6.1 添加图书

一个事务内完成：

1. 归一化标题并查找疑似重复；
2. 根据家长选择复用现有 `resource`，或创建家庭私有 `resource`；
3. 创建 `family_resource`；家长明确表示“家里有”时可直接为 `owned` 并记录确认人；
4. 写入原始来源和已知分级；不确定的 Agent 补全使用 `estimated + unconfirmed`；
5. 写 `audit_event` 和幂等响应。

相似标题只返回候选，不自动合并。标题和语言足以先保存；无作者、主题、蓝思值或单本图片都不报错。

### 6.2 记录反馈

一个事务内完成：

1. 追加 `parent_feedback`，保留家长原文；
2. 校验活动、任务和孩子的家庭归属；
3. 更新本次 `learning_session` 与任务完成状态；
4. 把结构化且可直接确定的字段追加为 `learning_evidence`；
5. 写审计和 `outbox_event`；后台发布器再可靠触发后续 Agent 运行。

Agent 对自然语言的提取可以异步完成，但不能阻止反馈保存。单次“没学习”只产生计划调度证据，不产生能力下降证据。

### 6.3 应用轻微计划调整

一个事务内完成：

1. 锁定 `learning_plan`，比较 `expected_plan_lock_version` 和 `base_revision_id`；
2. 再次执行策略判断；
3. 创建新 `plan_revision`，复制不变任务并创建变更后的任务；
4. 保持已完成和锁定任务的内容与状态；
5. 将旧版本置为 `superseded`，新版本置为 `active`；
6. 更新 `current_revision_id`，记录提案、工具调用与审计。

任何版本冲突都整体回滚，返回当前版本给 Agent 重新读取，不做静默覆盖。

### 6.4 接受高影响变更

一个事务内完成：

1. 锁定仍为 `pending` 且未过期的提案；
2. 写入唯一的 `parent_decision`；
3. 若接受，创建并激活新状态版本或计划版本；若拒绝，不写目标聚合；
4. 更新提案状态和解决时间；
5. 写工具调用和审计。

## 7. 添加图书的 MVP 流程

表单首屏只要求：

- `title`：必填；
- `language_codes`：默认 `['en']`，可改；
- `known_level`：可空，如 `Oxford Reading Tree / Stage 2`；
- `ownership`：`owned`、`not_owned` 或 `candidate`。

保存后 Agent 可以异步建议：系列、资源类型、主题、重复句式、互动方式、可能等级和别名。建议必须同时展示来源和置信度，家长可逐项确认。未确认建议保留在 `metadata` 或等级关联中，但不能伪装为官方事实。

当书架没有任何 `owned` 资源时，计划接口返回 `insufficient_owned_resources` 和最小补充动作，而不是要求整理全部书架。确认 1 本即可生成一个最小计划，推荐确认 5～10 本以获得更好的首周编排。

## 8. Learner Context 读取模型

`get_learner_context` 返回一次规划需要的稳定快照，避免 Agent 自己拼接几十张表：

```json
{
  "learner": {
    "id": "lrn_01",
    "age_months": 22,
    "daily_language_codes": ["zh-CN", "en"]
  },
  "active_goals": [
    {"id": "goal_01", "type": "enjoy_shared_reading", "priority": 1}
  ],
  "constraints": {
    "sessions_per_week": 4,
    "duration_minutes": {"preferred": 6, "maximum": 10},
    "hard": ["no_required_child_speech"]
  },
  "state": {
    "id": "state_03",
    "version_no": 3,
    "stage_hypotheses": [
      {"stage": "shared_reading_emergent", "confidence": 0.82}
    ],
    "interests": ["animals", "body_actions"],
    "evidence_gaps": ["tolerance_for_one_pause_prompt"]
  },
  "current_plan": {
    "id": "plan_01",
    "lock_version": 2,
    "revision_id": "rev_02",
    "revision_no": 2
  },
  "resource_summary": {
    "owned_count": 8,
    "metadata_incomplete_count": 5,
    "planning_eligible_count": 8
  },
  "pending_proposals": [],
  "snapshot_at": "2026-09-17T10:00:00+08:00"
}
```

`metadata_incomplete_count` 只是补全提示，不影响 `planning_eligible_count`。资源筛选仍通过独立工具进行，避免上下文过大。

## 9. Agent 工具通用协议

### 9.1 通用调用包络

```json
{
  "request_id": "run_01:tool_04",
  "family_id": "fam_01",
  "learner_id": "lrn_01",
  "actor": {"type": "agent", "agent_run_id": "run_01"},
  "input": {}
}
```

约束：

- `request_id` 在一次 Agent 运行内唯一；写工具还必须映射到 HTTP `Idempotency-Key`；
- `family_id` 由服务端认证上下文覆盖，不信任模型传入值；
- 写工具带基础版本或 `expected_lock_version`；
- 成功和失败都写 `agent_tool_call`；成功写入领域数据时还写 `audit_event`；
- 返回 `reason_codes` 和面向家长的 `explanation`，不只返回“成功”；
- 工具参数使用封闭枚举和 JSON Schema，拒绝额外字段；模型不能提交 SQL、表名或任意 patch 路径。

### 9.2 通用成功和冲突响应

```json
{
  "ok": true,
  "data": {},
  "reason_codes": ["A_001_REPEAT_REQUEST"],
  "explanation": "孩子主动要求重复，因此本周保留一次熟悉内容。",
  "audit_event_id": "aud_01"
}
```

```json
{
  "ok": false,
  "error": {
    "code": "version_conflict",
    "message": "计划已被更新，请重新读取后再生成调整。",
    "latest": {
      "plan_lock_version": 4,
      "revision_id": "rev_04",
      "revision_no": 4
    }
  }
}
```

## 10. 受控 Agent 工具契约

### 10.1 `get_learner_context`

用途：读取孩子、目标、约束、当前状态、当前计划摘要和待确认提案。

```json
{
  "input": {
    "learner_id": "lrn_01",
    "include_recent_evidence_days": 14
  }
}
```

返回第 8 节的上下文快照以及最近证据摘要。它替代旧草案中的 `get_learner_profile`、`get_learning_state`、`get_active_goals_and_constraints` 和常用 `get_supporting_evidence` 组合调用；调试场景仍可保留细粒度只读接口。

### 10.2 `search_family_resources`

用途：只在当前家庭范围内查找计划候选。

```json
{
  "input": {
    "learner_id": "lrn_01",
    "query": "动物 动作",
    "ownership": ["owned"],
    "usage": ["reading", "completed", "unread"],
    "target": {
      "stage": "shared_reading_emergent",
      "max_results": 10
    }
  }
}
```

```json
{
  "ok": true,
  "data": {
    "items": [
      {
        "family_resource_id": "fr_01",
        "title": "From Head to Toe",
        "usage": "reading",
        "known_levels": [],
        "metadata_completeness": "partial",
        "eligibility": "eligible",
        "match_reasons": ["interest:animals", "interaction:action_imitation"]
      }
    ]
  },
  "reason_codes": ["FAMILY_OWNED_RESOURCE", "PARTIAL_METADATA_ALLOWED"]
}
```

旧名 `search_resources` 作为兼容别名，但必须默认加家庭和 `owned` 过滤。

### 10.3 `add_resource_candidate`

用途：新增最小图书记录并返回可能重复项；不自动合并。

```json
{
  "input": {
    "title": "Good Night Gorilla",
    "language_codes": ["en"],
    "ownership": "owned",
    "known_level": null,
    "source": {"type": "parent_manual", "name": "add-book-form"}
  }
}
```

```json
{
  "ok": true,
  "data": {
    "resource_id": "res_18",
    "family_resource_id": "fr_18",
    "ownership": "owned",
    "lock_version": 1,
    "possible_duplicates": [],
    "enrichment_status": "suggestions_pending"
  },
  "reason_codes": ["MINIMUM_METADATA_ACCEPTED"]
}
```

### 10.4 `get_current_plan`

用途：获取计划、当前版本、任务、资源快照和锁信息。

```json
{"input": {"learner_id": "lrn_01", "include_completed_tasks": true}}
```

返回 `plan_id`、`plan_lock_version`、`revision_id`、`revision_no`、`state_version_id` 和任务列表。任务明确标记 `completed`、`locked_by_parent` 和 `mutable`。

### 10.5 `record_parent_feedback`

用途：保存家长原始反馈和确定性字段，触发后续结构化与评估。

```json
{
  "input": {
    "session_id": "ses_01",
    "completion": "completed",
    "duration_band": "5_10_min",
    "engagement": "high",
    "behavior_tags": ["requested_repeat", "imitated_action"],
    "original_text": "跟着动物做动作，还要求再读一遍，没有跟说单词。"
  }
}
```

```json
{
  "ok": true,
  "data": {
    "feedback_id": "fb_01",
    "evidence_ids": ["ev_31", "ev_32"],
    "follow_up_run": "queued"
  },
  "reason_codes": ["FEEDBACK_RECORDED", "NO_SPEECH_NOT_FAILURE"]
}
```

### 10.6 `propose_state_update`

用途：根据证据创建状态草稿或变更提案，不原地覆盖状态。

```json
{
  "input": {
    "learner_id": "lrn_01",
    "base_state_version_id": "state_03",
    "evidence_ids": ["ev_31", "ev_32"],
    "changes": [
      {
        "dimension": "action_imitation",
        "operation": "raise_confidence",
        "new_value": {"observed": true, "confidence": 0.88}
      }
    ],
    "reason_codes": ["EVIDENCE_REPEATED_ACTION_IMITATION"]
  }
}
```

返回 `risk=minor|major`、状态草稿或 `proposal_id`。阶段和主等级变化始终为 `major`。

### 10.7 `generate_plan_revision_draft`

用途：在不写入活动计划前生成并验证候选版本。

```json
{
  "input": {
    "plan_id": "plan_01",
    "base_revision_id": "rev_02",
    "base_state_version_id": "state_03",
    "trigger_evidence_ids": ["ev_31"],
    "intent": "repeat_preferred_resource",
    "requested_changes": [
      {"task_id": "task_04", "operation": "replace_resource", "family_resource_id": "fr_01"}
    ]
  }
}
```

```json
{
  "ok": true,
  "data": {
    "draft_id": "draft_07",
    "risk": "minor",
    "policy_checks": [
      {"code": "PRESERVE_COMPLETED", "passed": true},
      {"code": "WITHIN_DURATION_LIMIT", "passed": true},
      {"code": "RESOURCE_OWNED", "passed": true}
    ],
    "diff": {
      "unchanged_task_ids": ["task_01", "task_02", "task_03"],
      "changes": [{"task_id": "task_04", "summary": "改为重复熟悉绘本"}]
    }
  },
  "reason_codes": ["A_001_REPEAT_REQUEST"]
}
```

旧名 `simulate_plan_adjustment` 映射到此工具。

### 10.8 `apply_minor_plan_revision`

用途：只应用已经通过所有自动调整策略的轻微草稿。

```json
{
  "input": {
    "draft_id": "draft_07",
    "plan_id": "plan_01",
    "base_revision_id": "rev_02",
    "expected_plan_lock_version": 2
  }
}
```

返回新 `revision_id`、`revision_no`、新的 `plan_lock_version`、任务差异和审计 ID。工具内部重新执行策略，不信任草稿中的 `risk`。

### 10.9 `submit_change_for_confirmation`

用途：把高影响状态或计划草稿提交给家长。

```json
{
  "input": {
    "learner_id": "lrn_01",
    "proposal_type": "plan_revision",
    "draft_id": "draft_08",
    "base_plan_revision_id": "rev_02",
    "reason_codes": ["LEVEL_RANGE_CHANGE"],
    "explanation": "连续三次反馈显示当前级别偏难，建议降低主要选书范围。"
  }
}
```

返回 `proposal_id`、过期时间、面向家长的变更摘要和确认项。旧名 `submit_adjustment_for_parent_decision` 映射到此工具。

### 10.10 `record_parent_decision`

用途：保存家长对高影响提案的唯一决定，并在接受时原子应用。

```json
{
  "input": {
    "proposal_id": "prop_09",
    "decision": "accept",
    "comment": "先降低一周看看",
    "expected_base_revision_id": "rev_02"
  }
}
```

```json
{
  "ok": true,
  "data": {
    "decision_id": "dec_09",
    "proposal_status": "accepted",
    "applied_plan_revision_id": "rev_03"
  },
  "reason_codes": ["PARENT_CONFIRMED_MAJOR_CHANGE"]
}
```

## 11. REST API v1

### 11.1 现状了解

```text
POST /v1/learners/{learner_id}/assessments
GET  /v1/assessments/{session_id}/next-question
POST /v1/assessments/{session_id}/responses
POST /v1/assessments/{session_id}/complete
GET  /v1/assessments/{session_id}/initial-state
POST /v1/assessments/{session_id}/confirm-state
POST /v1/assessments/{session_id}/corrections
GET  /v1/learners/{learner_id}/context
```

`complete` 创建新的状态版本，不覆盖当前状态。需要家长确认时状态先为 `draft`，确认后才切换为 `active`。

### 11.2 书架和添加图书

```text
GET   /v1/learners/{learner_id}/bookshelf-candidates
POST  /v1/learners/{learner_id}/bookshelf-confirmations
GET   /v1/learners/{learner_id}/resources
POST  /v1/learners/{learner_id}/resources
GET   /v1/family-resources/{family_resource_id}
PATCH /v1/family-resources/{family_resource_id}
POST  /v1/family-resources/{family_resource_id}/enrichment-decision
```

`POST .../resources` 接受第 7 节的最小字段。`PATCH` 必须带 `If-Match: "<lock_version>"`。

### 11.3 周计划和任务

```text
POST /v1/learners/{learner_id}/weekly-plans/generate
GET  /v1/weekly-plans/{plan_id}
POST /v1/weekly-plans/{plan_id}/confirm
POST /v1/weekly-plans/{plan_id}/constraints

GET  /v1/tasks/{task_id}
POST /v1/tasks/{task_id}/start
POST /v1/tasks/{task_id}/feedback
POST /v1/tasks/{task_id}/skip
POST /v1/tasks/{task_id}/lock
```

### 11.4 调整与家长决策

```text
POST /v1/weekly-plans/{plan_id}/revisions:simulate
POST /v1/weekly-plans/{plan_id}/revisions
GET  /v1/weekly-plans/{plan_id}/proposals
POST /v1/proposals/{proposal_id}/decision
```

为了与已有设计稿迁移：

- `/simulate-adjustment` → `/revisions:simulate`；
- `/adjustments/{id}/decision` → `/proposals/{id}/decision`；
- 老路径可短期保留为 HTTP 别名，但数据库和领域模型统一使用 `change_proposal`。

### 11.5 写接口共同要求

- 请求头 `Idempotency-Key` 必填，建议使用 UUID；
- 服务端从登录态验证家庭成员身份和角色；
- 修改可变聚合时用 `If-Match` 或请求体 `expected_lock_version`；
- 每次响应返回 `request_id`、`reason_codes` 和资源的新版本；
- Agent 发起的请求必须携带内部签发的 `agent_run_id`，客户端不能伪造；
- 幂等键默认保留 24 小时；相同键和相同请求返回原响应，相同键但不同请求体返回冲突。

### 11.6 错误约定

| HTTP | code | 场景 |
|---:|---|---|
| 400 | `invalid_request` | 字段、枚举或 JSON Schema 不合法 |
| 403 | `family_scope_violation` | 访问了其他家庭的数据 |
| 404 | `aggregate_not_found` | 孩子、资源、计划或提案不存在 |
| 409 | `version_conflict` | `If-Match` 或基础版本已过期 |
| 409 | `idempotency_key_reused` | 同一个键用于不同请求 |
| 409 | `proposal_already_resolved` | 重复决定或提案已过期 |
| 422 | `policy_violation` | 自动调整越权、资源未确认或破坏锁定任务 |
| 422 | `insufficient_owned_resources` | 没有可用于计划的家庭资源 |

## 12. 自动调整策略

### 12.1 轻微变更，可自动执行

同时满足所有硬约束时，以下变更可标记 `minor`：

- 在同一周内移动一个尚未开始的任务；
- 在家长批准的范围内缩短单次时长；
- 用同等难度、已确认拥有的资源替换一个未开始任务；
- 增加一个不计完成压力的可选复习；
- 根据主动重复反馈，再安排一次喜欢的资源；
- 改变一个低风险互动提示，但不要求低龄孩子必须输出。

轻微也不是“模型说了算”。必须通过资源资格、时长、任务锁、已完成任务、周边界和基础版本检查，才调用 `apply_minor_plan_revision`。

### 12.2 高影响变更，必须家长确认

- 改变发展阶段、主要分级级别或主分级体系；
- 改变主要学习目标；
- 超出家长约束增加每周次数、总时长或单次时长；
- 替换多个核心任务或重建本周大部分计划；
- 把未确认拥有的资源加入正式计划；
- 低置信度状态判断会实质改变计划；
- 明显降低或提升内容复杂度。

策略输出必须包含 `risk`、命中的规则、证据引用、计划差异和家长能理解的说明。无法确定时按 `major` 处理。

## 13. 幂等、并发和审计

### 13.1 幂等

领域服务开始写事务时先插入 `idempotency_record`。若唯一键冲突：

- `request_hash` 相同且原请求完成：返回保存的状态码和响应；
- `request_hash` 相同但仍处理：返回 `409 request_in_progress`；
- `request_hash` 不同：返回 `409 idempotency_key_reused`。

不要用 Agent 的自然语言作为请求哈希；使用规范化 JSON、HTTP 方法、路由和认证家庭 ID 计算。

### 13.2 乐观锁

`resource`、`family_resource` 和 `learning_plan` 使用 `lock_version`。更新语句必须包含：

```sql
UPDATE babybook.learning_plan
SET current_revision_id = :new_revision_id
WHERE id = :plan_id AND lock_version = :expected_lock_version;
```

影响行数为 0 就返回 `version_conflict`。触发器自动将版本加一，不能由客户端指定新版本值。

### 13.3 审计

每个 Agent 写工具至少产生：

1. 一条 `agent_tool_call`，保存参数、结果、耗时和错误；
2. 每个被改变的领域聚合一条 `audit_event`；
3. 状态或计划调整的 `reason_codes`、证据引用和解释；
4. 高影响变化的一条 `change_proposal`，以及最终 `parent_decision`。

审计中避免保存完整提示词中的个人信息；`input_snapshot` 保存结构化、最小化且可重放的业务输入。

## 14. 首个纵向切片实施顺序

第一个可演示切片使用一个家庭、一个 22 个月孩子和 5～10 本已确认资源，贯通一次真实反馈：

1. **数据库迁移和种子数据**：执行 DDL，加入分级体系、一个家庭、孩子、约束和种子书；
2. **添加/确认资源**：实现最小表单、重复候选和 `owned` 确认；
3. **现状了解**：保存问卷，生成带证据的初始 `learner_state_version`；
4. **上下文与资源查询**：实现 `get_learner_context` 和 `search_family_resources`；
5. **确定性计划器 v0**：先用规则生成 3～4 个任务和解释，再由 Agent 调用，不让模型自由拼装不可校验计划；
6. **家长确认与任务卡**：激活周计划，展示停止条件、互动建议和观察目标；
7. **10 秒反馈**：保存完成度、时长、参与表现、标签与原文；
8. **证据和状态更新**：把反馈转换为证据，建立状态候选；
9. **一次轻微自动调整**：例如主动重复后保留同一本书；
10. **一次高影响确认**：用测试场景验证阶段/等级变化无法绕过家长；
11. **验收与回放**：从审计记录回答“为何推荐、因何改变、谁确认、使用哪个版本”。

第一版先实现可解释规则，再接 Agent 负责信息抽取、候选选择和解释。这样即使模型暂时不可用，添加图书、反馈记录和现有计划仍然可用。

## 15. MVP 暂不做

- 不做语音评分、孩子强制测验或发育诊断；
- 不采购或存储未授权电子绘本正文；
- 不声称不同分级体系可以精确互换；
- 不因缺少蓝思值、主题、作者或封面阻止添加图书和低龄计划；
- 不允许 Agent 直接执行 SQL、任意 JSON Patch 或批量覆盖状态；
- 不在首版引入图数据库、向量数据库、复杂多 Agent 协作或自动长期课程。

## 16. 开发完成判定

纵向切片达到以下条件才算完成：

- 书架只有 5 本且元数据不完整时仍能生成合规计划；
- 计划中的每本书都已由家庭确认拥有；
- 每个状态和计划变更能追溯到证据、规则、Agent 运行和审计事件；
- 重复请求能安全返回同一结果，不创建重复反馈或重复版本；
- 并发修改返回 `version_conflict`，不会静默覆盖；
- 轻微调整保留完成和锁定任务；
- 高影响调整在家长确认前不会生效；
- “没有跟说单词”和“当天没学习”不会被误判为能力下降；
- 家长可以随后继续添加图书，无需先整理完整书架。
