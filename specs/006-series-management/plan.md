# 006：系列图书维护实施计划

| 字段 | 内容 |
| --- | --- |
| 状态 | implemented |
| 关联 Spec | [spec.md](spec.md) |
| Spec 基线版本 | v0.1 |
| 更新日期 | 2026-09-29 |

## 实施方案

扩展 catalog 系列写入服务与接口，复用成员表、人工保护、来源和事务锁。BookIntake 新增可空 target_series 和 confirmation_options，草稿 series_ids 独立于书目字段；旧任务默认为空。前台采用统一维护区域、可复用系列选择器以及现有 CatalogManagement/IntakeReview，保留多系列关联，不复制套装字段到单本。

## 任务分解

| 编号 | 任务 / 路径 | 需求 / 验收 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| TASK-001 | 规格、接口文档与索引 specs/、产品讨论/catalog-api.md | 全部 | 无 | 完成 |
| TASK-002 | catalog/series_services.py、series_api.py、serializers.py、urls.py；成员顺序/来源保护 | REQ-001/002/004/005；AC-001/002/004/005 | TASK-001 | 完成 |
| TASK-003 | services.py、intake_api.py、intake_worker.py、models.py、迁移 0010；确认事务/幂等 | REQ-003/004/005；AC-003/004/005 | TASK-002 | 完成 |
| TASK-004 | SeriesWorkspace.tsx、SeriesSelector.tsx、App.tsx、CatalogDetails.tsx、CatalogManagement.tsx、IntakeReview.tsx、styles.css；录入复用 | REQ-001/002/003/005；AC-001/002/003/005 | TASK-002/003 | 完成 |
| TASK-005 | test_series_management.py、全套检查、桌面及 390×844 手机视口手工验证 | 全部 | TASK-004 | 完成 |

## 需求对应与验证

| 需求 | 任务 | 验收 | 验证 | 结果 |
| --- | --- | --- | --- | --- |
| REQ-001 | TASK-002/004/005 | AC-001 | API及界面 | 通过，见 [验收记录](validation.md) |
| REQ-002 | TASK-002/004/005 | AC-001/002 | 成员/书架/排序测试及界面 | 通过，含并发关联及过期排序 |
| REQ-003 | TASK-003/004/005 | AC-003 | 任务持久化/幂等/回滚测试及界面 | 通过；识别结果使用验收草稿，未新增供应商调用 |
| REQ-004 | TASK-002/003/005 | AC-002/004 | 数量/来源/采集/旧FK测试 | 通过 |
| REQ-005 | TASK-002/003/004/005 | AC-005 | 权限/CSRF/隔离/回归及全套检查 | 通过；手机验证为模拟视口，未验证物理设备 |

## 迁移和边界

现有 Book/BookSeries/成员无需结构重建；迁移 0010 给照片任务增加可空系列引用和确认选项，已应用到本地数据库。新部署需执行迁移并重启应用/worker。现有试用服务已平滑重载 web，继续提供最新构建，原地址保持；已有 worker 可继续运行，缺少草稿 series_ids 时由 API 回退到持久化目标系列，下次正常服务重启加载新 worker 代码。不改动未完成的家庭部署修改。系列合并/删除/自动分册延期，原因仍为本期人工维护闭环之外。

## 调整记录

2026-09-29：用户批准实施，开始TASK-001，基线v0.1。

2026-09-29：TASK-001～005 完成。283 项 Django 测试、3 项部署进程测试、后端检查、前端 lint/格式/类型/构建通过。桌面及手机视口验证覆盖新建、当前单本关联、手工新增、批量已有书、移出、排序、超册数提示、照片合并/拆分/归属刷新及确认。测试样本已清理；完整证据见 [validation.md](validation.md)。
