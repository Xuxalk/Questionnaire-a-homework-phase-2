# 第一阶段题目版本化迁移说明

## 目的与安全边界

`backend/scripts/migrate_question_library.py` 将第一阶段 `surveys.questions[]` 中每个可安全映射的题目实例建立为一个私有题目族及初始不可变版本，并把题目加入其创建者的默认个人题库。映射键是 `(survey_id, question_id)`，相同题干不会自动合并。脚本只向原题目增加 `library_ref.source_question_id/source_version_id` 元数据；不重写题干、题目/选项 ID、校验、跳转规则、数组顺序或 `responses`。

脚本默认 dry-run；dry-run 不创建索引、不写集合、不记录迁移运行。只有显式提供 `--apply` 才会写入。此脚本没有删除旧数据或回滚删除行为。不要将 `--apply` 指向真实/共享数据库，除非已完成备份、评审 dry-run 报告并明确安排迁移窗口。

## 执行步骤

1. 在隔离测试数据库或数据库副本准备第一阶段数据；确认 `MONGODB_URI` 指向目标 MongoDB、`MONGODB_DATABASE` 是专用数据库名。
2. 默认运行只读预检：

   ```bash
   export MONGODB_URI='mongodb://127.0.0.1:27017'
   export MONGODB_DATABASE='survey_test_question_library_migration'
   python -m backend.scripts.migrate_question_library
   ```

   也可显式写 `--dry-run`。输出为 JSON，包含问卷/题目扫描数、预计映射数、已映射数、跳过数、失败数和每项失败原因。需要保存报告时传入临时路径，例如 `--report-file /tmp/question-library-dry-run.json`。
3. 检查所有失败项、重复 `question_id`、无效 owner、未知题型、损坏的来源引用及估算数量。脚本会拒绝不合法或引用缺失的数据，不通过猜测补字段。
4. 确认该数据库是隔离副本且已备份后，才显式执行：

   ```bash
   python -m backend.scripts.migrate_question_library --apply \
     --report-file /tmp/question-library-apply.json
   ```

   运行中会建立新集合索引。对每条问卷题目执行内容匹配的 compare-and-set，只把 `library_ref` 写入仍与预检内容一致的题目；问卷若已并发变化，该题跳过并记录失败，不覆盖当前问卷。
5. 核对 JSON 报告及 `schema_migrations` 集合的 `question-library-v1` 最近运行结果，比较迁移前后的题目 ID/内容/跳转/答卷和统计。确认异常项后再次运行 `--apply` 可以继续完成尚未链接的题目。

## 重复执行与恢复

- 题目族以来源问卷/题目 ID 唯一；版本内容以哈希检查；重复运行会识别已正确链接的来源，不会重复创建相同迁移题目或版本。
- 版本、题库条目先于问卷关联写入。若在跨集合步骤中断，重跑会复用匹配版本和题库条目，再完成来源关联；已写入但未引用的不可变版本保留，不会自动删除。
- MongoDB 没有在本操作中启用跨集合事务。报告把每题的扫描/映射结果分开记录；一个题目失败不会导致其他合法题目回滚，也不会覆盖失败题目。
- 对 `library_ref` 部分缺失、指向其他用户私有题目、缺失版本或版本内容哈希与问卷快照不符的记录，脚本标记失败并保持该题现状。先人工核实来源和历史，再修复输入或保留为未迁移旧题；不要直接改写旧版题目或答卷。
- 检测到问卷在预检后被编辑时，会留下可识别的未使用版本并报告 CAS 冲突；先核查最新问卷内容，再重新 dry-run/apply。脚本不会覆盖并发编辑。

## 隔离测试

自动测试使用 `tests/conftest.py` 创建随机 `survey_test_*` 数据库，并在 fixture 清理时只删除它自己创建的测试库。`tests/test_question_library_migration.py` 覆盖 dry-run 无写入、正常迁移、重复执行、保留发布题目的 ID/跳转/历史答卷统计、不可映射内容跳过，以及 CAS 冲突后重新运行。运行方式：

```bash
MONGODB_TEST_URI='mongodb://127.0.0.1:27017' \
  python -m pytest tests/test_question_library_migration.py -q
```

应用不会自动执行该迁移；MongoDB 应用启动时只创建集合索引，不会批量改写现存问卷。

## 2026-10-28 最终审计隔离演练

在专用本机 MongoDB 8.3.11 实例和数据库 `survey_phase2_final_rehearsal_20261028` 中，从空的新集合开始插入第一阶段原始形状的数据：1 用户、1 份已发布问卷、3 道旧题（单选题带跳转到数字题、选答文本题、整数数字题）、3 份 submitted 答卷（两份在跳转路径上跳过文本题）和 1 份 draft 答卷。

| 阶段 | users | surveys | responses | question_families | question_versions | question_banks | question_bank_items | question_shares | schema_migrations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 迁移前 / dry-run 后 | 1 | 1 | 4 | 0 | 0 | 0 | 0 | 0 | 0 |
| 注入单题部分失败后 | 1 | 1 | 4 | 3 | 3 | 1 | 2 | 0 | 1 |
| 重跑恢复后 | 1 | 1 | 4 | 3 | 3 | 1 | 3 | 0 | 1 |

dry-run 扫描 1 份问卷和 3 道题，报告 `would_link=3, failed=0`，前后集合计数相同。旧统计基线为 3 次提交、单选选项人数 2/1、1 条有效文本回答、数字均值 34。

apply 时通过单次故障注入，在首题的版本文档插入后、族的 `latest_version_id` 更新前抛出模拟中断。首次 apply 报告 2 题已链接、1 题失败；旧数据保持，新增的不可变族/版本保留以供恢复。该实验复现了旧重试逻辑会复用已有版本却不修复最新版本指针的问题。本轮已修改迁移重试路径：若已有版本正是族当前最高分配版本而指针缺失/落后，则重试 CAS 更新指针。再次 apply 报告 `linked=1, already_linked=2, failed=0`，题库条目总数为 3，版本总数仍为 3，指针指向正确版本。

恢复后对照显示：原题目 ID、题目字段/顺序、跳转规则、已发布状态和发布时间不变；仅题目增加 `library_ref`、问卷更新时间改变。全部 4 份 response 文档逐文档相等；单问卷统计 JSON 与迁移前相等。dry-run/apply/恢复过程均只操作该专用数据库；演练结束删除了此测试库。没有对真实数据库执行写迁移。

迁移重试的故障注入现在由 `test_migration_retry_repairs_version_inserted_before_latest_pointer_failure` 回归测试覆盖。应用版本追加服务也增加一次 latest 指针安全核验和重试，相关 `test_question_creation_recovers_if_latest_pointer_write_is_interrupted` 验证版本、指针与题库条目关联完整。跨集合事务未启用；持续不可用的 MongoDB 仍可能留下未引用的不可变记录或版本号间隙，报告该失败后可在隔离环境修复/重试，不能通过删除历史版本来掩盖。
