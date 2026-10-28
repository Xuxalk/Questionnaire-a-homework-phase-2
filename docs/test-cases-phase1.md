# 第一阶段测试用例与结果

## 1. 测试说明

自动化用例对应 `tests/` 中的 pytest 测试函数，覆盖真实 MongoDB 集成路径；参数化用例按参数展开计数。每个数据库用例使用随机 `survey_test_*` 数据库，结束后删除本用例数据库。运行完整用例：

```bash
MONGODB_TEST_URI='mongodb://127.0.0.1:27017' pytest -q
```

本次最终验证在全新虚拟环境、Python 3.9.6 和 MongoDB 8.3.11 上执行，结果为 **41 passed in 7.20s**。自动化表中的“实际结果”表示测试断言通过；浏览器用例来自真实 Chrome/Playwright 手工验收，未将 API 测试冒充浏览器测试。

## 2. 自动化测试用例

| 编号 | 测试步骤与输入 | 预期输出 | 实际结果 / 测试依据 |
|---|---|---|---|
| A-01 | 注册 `  Alice  `，密码 `Correct horse 42!`；检查用户文档和响应 | 201；用户名清理为 Alice；响应不含哈希；DB 保存 Argon2id 哈希和规范化值 | 通过；`test_register_creates_user_and_returns_public_fields` |
| A-02 | 连续注册相同用户名 `sora` | 首次 201，第二次 409 | 通过；`test_duplicate_username_returns_conflict` |
| A-03 | 先注册 `  MiXeD  `，再注册 `mixed` | 第二次 409；users 只保留一条 | 通过；`test_username_uniqueness_ignores_case_and_outer_whitespace` |
| A-04 | 分别提交空/全空格用户名、短密码、错误类型及缺少密码 | 请求均为 422 | 通过；`test_invalid_registration_input_returns_unprocessable_entity`（5 个输入） |
| A-05 | 注册并查看 MongoDB 用户文档 | 哈希以 `$argon2id$` 开头，可由 Argon2 验证；明文不存储或回传 | 通过；`test_password_is_stored_as_argon2id_hash_not_plaintext` |
| A-06 | 两个并发客户端使用同一用户名注册 | 一个 201，一个 409；数据库一条用户记录 | 通过；`test_concurrent_same_username_has_one_success_and_one_conflict` |
| A-07 | 登录前请求 `/api/auth/me`；登录后检查 Cookie 和 `/me` | 未登录 401；登录 200；Cookie 有 HttpOnly、SameSite=Lax、7 天 Max-Age | 通过；`test_login_creates_signed_session_and_me_requires_authentication` |
| A-08 | 用不存在/错误密码登录 | 通用错误，不区分用户名是否存在；401 | 通过；`test_invalid_login_has_generic_error` |
| A-09 | 登录后注销，再请求 `/me` | 注销 204，后续请求 401 | 通过；`test_logout_clears_session` |
| A-10 | 登录请求附 `Origin: https://attacker.example`、`Sec-Fetch-Site: cross-site`；再用 cross-site 创建问卷 | 登录/变更请求 403；不创建问卷；同源创建 201 | 通过；`test_cross_origin_authentication_and_mutations_are_rejected` |
| A-11 | 未配置 `SESSION_SECRET_KEY` 时尝试登录 | 503，说明会话认证未配置 | 通过；`test_login_is_disabled_without_a_session_secret` |
| H-01 | 启动 FastAPI，访问 `/health`、首页及静态资源 | `/health` 为 200 和 `{"status":"ok"}`；HTML/CSS/JS 可读取 | 通过；`test_application_starts_and_health_endpoint_returns_ok`、`test_frontend_shell_and_assets_are_served_by_fastapi` |
| Q-01 | 未登录创建问卷；登录后创建草稿、读取列表/详情、修改标题和匿名设置、设置含时区截止时间；切换到另一账号访问 | 未登录 401；本人成功；截止换算为 UTC；非本人读写为 404 | 通过；`test_questionnaire_owner_scope_and_draft_crud` |
| Q-02 | 创建单选、多选、文本、数字和后续题；给单/多/数字题配置跳转；编辑题目后发布 | 题目 ID 稳定；规则保存并按 priority 排序；发布成功 | 通过；`test_four_question_types_rules_publish_and_locked_fields` |
| Q-03 | 发布后尝试编辑题目/填写策略；修改标题、截止；关闭后再次设置截止 | 锁定字段返回 409；允许的描述/截止变更成功；关闭后截止变更 409 | 通过；`test_four_question_types_rules_publish_and_locked_fields` |
| Q-04 | 提交超出选项数的多选题；删除被跳转引用题；提交无效目标 | 多选结构 422；引用题删除 409；无效目标 422；未引用题可删除 | 通过；`test_question_validation_and_referenced_question_cannot_be_deleted` |
| R-01 | 未登录访问分享接口；再登录访问草稿问卷 | 未登录 401；草稿问卷在填写端统一 404 | 通过；`test_share_access_hides_private_surveys_and_requires_login` |
| R-02 | 建立四类题目；跳过必答题、提交外来单选 ID、重复/过多/过少多选值、超短/超长文本、越界/非整数/布尔数字 | 非法操作 422；有效值逐题保存；必答完整后可提交；提交后修改返回 409 | 通过；`test_answer_validation_resume_skip_and_submit` |
| R-03 | 保存第一题后再次开始填写；使用 ANY/ALL 同时命中、数字阈值跳转；修改前序答案 | 恢复同一 draft；较低优先级号命中；路径变化后不可达步骤清除；不可达题写入 409 | 通过；`test_priority_dynamic_jumps_and_history_change_cleans_unreachable_steps` |
| R-04 | 匿名问卷下填写并读取自己的答卷；创建者尝试经填写接口访问该答卷 | 公开问卷/答卷投影不含用户 ID/用户名；创建者读取他人答卷 404 | 通过；`test_anonymous_response_endpoints_do_not_expose_respondent_to_creator` |
| R-05a | 禁止重复问卷提交一份答卷后再次开始 | 再次开始 409；已提交答卷仍有 `lifecycle_guard:"single"` | 通过；`test_repeat_submission_policy_and_no_content_deduplication[False]` |
| R-05b | 允许重复问卷两次提交完全相同的文本 | 两份独立 submitted 文档均保留；成功提交不按内容去重 | 通过；`test_repeat_submission_policy_and_no_content_deduplication[True]` |
| R-06 | 设置过去截止时间；尝试新建、保存和提交；另建问卷手动关闭后尝试填写 | 分享读取显示截止/关闭原因；写入均为 410 | 通过；`test_deadline_and_close_are_reported_on_share_link_and_block_writes` |
| R-07 | 两个登录会话并发开始同一答卷；再用相同 revision 并发写答案 | 开始返回同一 draft；写入一个成功 200、一个冲突 409；revision 变为 1 | 通过；`test_concurrent_start_resumes_one_draft_and_revision_allows_one_writer` |
| R-08 | 两个不同登录账号同时开始、保存并提交同一问卷 | 两次提交均为 200；得到两份不同 response；MongoDB 中有两份 submitted 答卷 | 通过；`test_distinct_respondents_can_submit_same_survey_concurrently` |
| R-09 | 初始化 responses 索引并检查定义 | 存在部分唯一生命周期索引和提交统计查询索引 | 通过；`test_response_indexes_are_present` |
| R-10 | 对数字条件分别测试 eq、ne、gt、gte、lt、lte 以及不命中边界 | 返回对应跳转目标或默认下一题 | 通过；`test_numeric_jump_operators`（7 个输入组合） |
| R-11 | 直接校验 NaN、正 Infinity、负 Infinity 数字答案 | 均抛出 `InvalidAnswer`，API 校验路径拒绝非有限值 | 通过；`test_numeric_answer_rejects_non_finite_values`（3 个输入） |
| S-01 | 提交一份包含四类题答案并跳过末题的答卷；另保存一份 draft；查询全卷和单题统计 | 只计已提交 answered 步骤；全卷提交数 1；单选/多选/文本/数字分别计数；跳过和 draft 不计 | 通过；`test_statistics_aggregate_only_submitted_answered_steps` |

## 3. 真实浏览器验收用例

以下步骤通过实际浏览器 UI 完成，Playwright 用于浏览器操作；pytest API 集成测试不是这些用例的替代证据。

| 编号 | 步骤与输入 | 预期输出 | 实际结果 |
|---|---|---|---|
| B-01 | 浏览器注册并登录创建者账号；退出后注册/登录第二个账号 | 两账号可独立建立会话；未登录功能跳转到登录页 | 通过 |
| B-02 | 创建四题型问卷；设置文本长度、多选数量、数字范围及条件跳转；发布 | 编辑器可保存规则和题目；发布后题目只读 | 通过 |
| B-03 | 第二账号通过分享链接进入；选择触发跳转的单选项，再选择另一个分支 | 按配置显示后续题，不经过题目不要求填写 | 通过；实测答案分支变化 |
| B-04 | 保存草稿后刷新页面 | 恢复相同答卷和当前路径 | 通过 |
| B-05 | 已保存后修改前序答案 | 重新计算路径，旧分支答案从 draft 清理 | 通过 |
| B-06 | 对文本题输入短于限制的值，对多选题提交不足数量的值 | 页面显示错误提示，不能继续提交非法答案 | 通过 |
| B-07 | 跳过选答题并提交有效答卷 | 成功提交并显示完成状态；已提交后不能修改 | 通过 |
| B-08 | 另一浏览器会话访问创建者统计页面 | 全卷提交数、单选/多选/文本/数字统计与答卷一致；界面不显示填写者用户名 | 通过；单题数字平均值为 77，提交总数为 1 |
| B-09 | 对禁止重复的问卷再次打开分享链接 | 页面提示已提交，不能产生第二份成功提交 | 通过 |
| B-10 | 创建者把截止时间设为过去，填写者重开链接 | 明确显示“这份问卷已经截止”，不允许继续写入 | 通过 |
| B-11 | 在 390×844 移动视口查看登录页、工作台、统计页和截止提示 | 无横向溢出；主要操作控件可见且可用 | 通过；实测 `scrollWidth == innerWidth == 390` |
| B-12 | 已登录浏览器从另一个本地端口提交 HTML 表单 POST 到问卷关闭接口 | 返回 403，问卷没有被关闭 | 通过；实际响应 `Cross-origin request blocked` |

移动视口为 Chrome 设备尺寸模拟，不是实体手机；本次浏览器检查没有保存截图文件。当前项目没有将 Playwright 浏览器脚本加入依赖，手工用例可按上表复跑。

## 4. 测试范围限制

- 未进行高并发吞吐、千万级数据、MongoDB 故障切换或负载性能测试；并发用例只验证同一用户名/草稿/revision 冲突语义。
- 未测试实体手机、屏幕阅读器和多浏览器矩阵。
- 自动化测试默认 MongoDB 可连接；未配置 `MONGODB_TEST_URI` 时集成用例会 skip，因此需按 README 明确设置测试 URI 才能复现 41 项完整结果。并发用例验证冲突处理和两个不同答题者同时提交，不代表吞吐量或高负载基准。

## 6. 第二阶段第二批共享授权与使用清单

新增自动化用例位于 `tests/test_question_library_sharing.py`，使用隔离 MongoDB 测试数据库。该批完整 pytest 结果记录在本节，不覆盖第一阶段及第一批的历史实测数字。

| 范围 | 验收内容 | 自动化依据 |
|---|---|---|
| 登录和分享权限 | 未登录访问 401；非所有者不能授权；不存在的题目版本/接收用户返回 404 | `test_shares_are_version_scoped_authorized_and_idempotent` |
| 精确版本授权 | 接收者只能看到及复用被授权版本；重复授权幂等；不同版本分别授权；不能查看历史、写原题或恢复原所有者版本；新版本不继承旧授权 | `test_shares_are_version_scoped_authorized_and_idempotent` |
| 撤销与兼容 | 重复撤销幂等；撤销后不能再次读取、查看使用清单或复用；原发布快照、历史提交答卷和单问卷统计保持 | `test_revoke_is_idempotent_and_keeps_existing_published_data` |
| 使用清单权限/版本 | 接收者只看自己问卷明细；所有者查看自己的问卷明细并获得其他用户按版本汇总；不泄漏他人问卷 ID、标题、所有者；分别识别精确副本与本地修改 | `test_usage_listing_filters_owner_details_tracks_edits_and_versions` |
| 实时关联 | 标题更新反映到清单；题目移除后清单不再返回该用法；不同版本按来源版本区分 | `test_usage_listing_filters_owner_details_tracks_edits_and_versions` |
| 索引升级失败安全 | 空的第一批预留索引可替换为精确版本索引；检测到歧义旧授权文档时拒绝启动，要求人工审查 | `test_share_index_upgrade_replaces_empty_first_batch_reservation`、`test_ambiguous_legacy_share_records_fail_closed` |

2026-10-26 在本机隔离 MongoDB `mongodb://127.0.0.1:27017` 上重新执行完整测试命令：

```bash
MONGODB_TEST_URI='mongodb://127.0.0.1:27017' .venv/bin/python -m pytest -q
```

实测结果为 **54 passed、0 failed、0 skipped in 23.92s，无 warning**。测试期间未执行真实数据库迁移写入。另通过本机浏览器 UI 操作完成双账号创建题目/授权/接收者复用/权限范围查看/撤销验证；该流程是一次手动浏览器核验，不是完整自动化 E2E 套件。仅使用的浏览器隔离测试库已删除。

## 5. 第二阶段第一批自动化测试与本轮结果

第二阶段第一批测试位于 `tests/test_question_library.py` 和 `tests/test_question_library_migration.py`，仍使用 `MONGODB_TEST_URI` 与随机 `survey_test_*` 数据库。覆盖：

| 范围 | 验收内容 |
|---|---|
| 题目族与版本 | 创建 v1、从指定父版本建立 v2、历史顺序和父版本；恢复 v1 创建 v3 并记录 `restored_from_version_id`，不覆盖 v1/v2。 |
| 个人题库 | 创建默认库与自定义库、加题、防止重复加入、更新条目固定版本、移除条目但保留版本。 |
| 跨问卷复用 | 保存现有题目到库；不同问卷生成不同本地题目 ID；复用题不复制旧跳转目标；目标问卷可重新设置跳转。 |
| 快照隔离 | 发布后修改来源题目版本，已发布问卷的题干、跳转和版本关联保持不变；已迁移发布问卷的历史 response 文档及统计值不变。 |
| 草稿编辑 | 编辑带来源的草稿题目时新增不可变版本；历史版本保留。 |
| 私有权限 | 另一用户不能查看题目历史或把私有题目加入问卷。 |
| 迁移 | dry-run 不写数据；apply 将每个旧题目实例分别迁移并加入默认库；重复 apply 不重复建身份/版本；保留题目 ID、跳转和历史答卷。 |
| 迁移失败/恢复 | 无效题目不改原文并输出失败原因；模拟并发编辑导致 CAS 失败后不覆盖编辑内容，可重新运行完成迁移。 |

2026-10-26 在本机 MongoDB `mongodb://127.0.0.1:27017` 上执行完整套件：

```bash
MONGODB_TEST_URI='mongodb://127.0.0.1:27017' python -m pytest -q
```

**实测：49 passed in 20.53s，0 skipped，0 failed。** 测试运行产生 1 条 Pydantic `UnsupportedFieldAttributeWarning`（`alias="survey_id"`，出现在账号注册并发测试期间）；没有静默忽略该 warning。另执行 `python -m compileall -q backend`、`node --check frontend/js/app.js` 和 `git diff --check`，均退出码为 0。

迁移 CLI 在独立空库名 `survey_test_phase2_cli_dry_run` 上以默认 dry-run 执行，实测扫描 0 份问卷/0 道题，`failed=0`、退出码 0；本次没有对真实或共享数据库执行迁移写入。隔离数据库 apply、幂等和失败恢复由 pytest 用例实际执行。

## 7. 第二阶段第三批跨问卷统计、性能与浏览器验收

### 7.1 自动化用例

新增文件为 `tests/test_cross_survey_statistics.py`。全量测试仍运行第一阶段、第一批和第二批用例；结果不是只运行新增测试。

| 范围 | 验收内容 | 自动化依据 |
|---|---|---|
| 题型统计 | 单选人数/比例、多选选择次数/比例、文本答案列表、整数数字平均值/极值/分布；重复提交逐条计数 | `test_cross_survey_statistics_aggregate_exact_version_and_repeated_submissions`（题型参数化） |
| 精确版本/重复实例 | 相同版本跨问卷合并；另一版本不进入；同问卷多次引用保留不同本地题目实例 | `test_cross_survey_statistics_aggregate_exact_version_and_repeated_submissions`、`test_jump_skipped_and_duplicate_instances_are_counted_per_question` |
| 路径状态 | draft、skipped 和跳转未到达题目排除 | `test_cross_survey_statistics_aggregate_exact_version_and_repeated_submissions`、`test_jump_skipped_and_duplicate_instances_are_counted_per_question` |
| 权限/隐私 | 无权访问返回 404；共享仅允许查接收方自己的问卷统计；不返回其他用户问卷或身份；不兼容题目实例排除 | `test_cross_survey_statistics_permissions_filtering_and_incompatible_instances` |
| 空/关闭/限定范围 | 空结果、已关闭问卷可查、指定问卷和草稿问卷拒绝 | `test_cross_survey_statistics_empty_closed_and_survey_filter` |
| 索引 | 检查问卷来源索引、owner+version 查询索引和 responses submitted lookup 索引 | `test_cross_survey_statistics_indexes_are_present` |

2026-10-27 使用 `MONGODB_TEST_URI=mongodb://127.0.0.1:27017` 在本机隔离随机数据库执行完整 pytest：**62 passed、0 failed、0 skipped in 29.57s**。出现 1 条 Pydantic `UnsupportedFieldAttributeWarning`，为账号注册并发测试现有的 `alias="payload"` 字段提示。没有将跳过用例记作通过。

静态检查实际结果：`.venv/bin/python -m compileall -q backend tests` 退出码 0；`node --check frontend/js/app.js` 退出码 0；`git diff --check` 退出码 0。

### 7.2 MongoDB 性能与 explain

性能验证使用专用隔离库 `survey_test_phase3_perf_recheck_20261027`（结束后已删除），数据为 500 份问卷，其中查询用户 400、其他用户 100；8,100 份响应，其中 submitted 8,000、draft 100。查询精确题目族/版本后返回 400 份可见问卷和 8,000 个有效回答。3 次完整聚合耗时 **119.45、116.27、122.64 ms**，中位数 **119.45 ms**。

`executionStats` 的 winning query plan 在问卷首段选择 `ix_surveys_owner_library_version`；`$lookup` 使用 `ix_responses_survey_status_submitted_at`，lookup 报告 `collectionScans:0`、`totalKeysExamined:8000`、`totalDocsExamined:8000`。查询用一条 `$match → $unwind → $lookup` 聚合，不逐问卷/逐答卷发查询；Mongo cursor 流式交给 Service，非文本题只在 Service 中保留累计统计，文本回答按产品定义返回列表。样本仅为功能性基准，不是高并发或生产容量保证。

### 7.3 双账号浏览器端到端

2026-10-27 在隔离库 `survey_test_phase3_browser_20261026` 的本地 FastAPI/Chrome UI 实际完成：

1. 所有者注册、登录，创建单选题，保存至个人题库，建立 v2，查看历史并恢复出 v3。
2. 创建并发布两份使用 v3 的问卷；为其中一份配置单选条件跳转，中间选答题被跳过，仍到达后续文本题。
3. 所有者指定授权 `phase3_reader` 使用 v3；接收者登录后看到获准版本，创建自己的草稿问卷并复用。复用生成本地题目 ID，接收者在自己的问卷中重新配置跳转并发布。
4. 接收者通过浏览器分别填写并提交两份所有者问卷及自己的复用问卷。已发布问卷的跳转链正确；提交成功。
5. 所有者题库“跨问卷统计”页显示两份自有问卷和两个有效回答，选项分布为 Neutral 1、Unsatisfied 1；逐问卷视图分别显示问卷与本地题目 ID。选择未用过的 v1 显示 0 份问卷/0 个回答。
6. 接收者跨问卷统计只显示自己拥有的一份问卷和一条回答，没有显示所有者两份问卷及其回答。
7. 所有者新增 v4 后，已发布问卷仍显示 v3 原题干、原 `library_ref` 版本和原跳转。v4 授权管理显示无接收者，不继承 v3 授权。
8. 所有者撤销 v3 授权后，接收者题库不再显示共享题目；接收者自己已发布问卷和提交答卷仍保留，打开单问卷统计仍为提交数 1，复用快照中的题干和跳转配置未变化。

这是通过浏览器实际交互的端到端验收，不以 API 集成测试替代。浏览器测试账号为专用测试账号；不代表 Playwright 自动化回归、多浏览器矩阵、移动实体设备或辅助技术验收。服务停止后删除本次专用隔离数据库，未对真实数据库执行写入迁移。

### 7.4 本次未覆盖

- 未完成千万级数据、高并发吞吐、故障切换、目标生产 MongoDB 索引上线或生产迁移。
- 浏览器完整流程为一次本机双账号验收，不是可重复运行的自动化浏览器 suite。
- 用户只有权查看本人问卷的跨问卷答卷统计；共享不会提供跨所有者答卷统计，也没有按标题、相似问题或不同版本进行统计兼容合并。

## 8. 最终质量审计与迁移兼容性演练（2026-10-28）

### 8.1 自动化完整回归

最终全量命令在专用 MongoDB 8.3.11 测试实例上运行：

```bash
MONGODB_TEST_URI='mongodb://127.0.0.1:27039' \
  .venv/bin/python -m pytest -q -W error::pydantic.warnings.UnsupportedFieldAttributeWarning
```

实测 **64 passed、0 failed、0 skipped in 31.30s**。将 `UnsupportedFieldAttributeWarning` 提升为错误后，全套仍通过，当前环境没有产生该 warning；没有通过忽略 warning 或删除测试来获得通过。另有两条定向故障注入测试在全量运行前单独执行，结果 **2 passed**；它们也包含在最终 64 项全量结果中，不重复计数。

新增迁移重试测试模拟版本文档已写入而 `latest_version_id` 尚未更新时进程中断；重试后指针、问卷引用和题库条目均一致。新增普通版本创建故障测试模拟最新版本指针首次写失败，服务核验并重试后请求成功，族指针与版本/题库条目指向一致。原有失败迁移测试仍验证 CAS 冲突保留并发编辑并可恢复。

### 8.2 第一阶段原始 Schema 迁移演练

在专用隔离数据库 `survey_phase2_final_rehearsal_20261028` 中，直接构造旧版 `users/surveys/responses` 数据，包含一份已发布问卷、带跳转题、答题分支中的 skipped 步骤、文本及数字回答、重复提交和 draft 答卷。运行顺序为基线采集 → dry-run → 显式 apply → 故障注入后重跑 → 数据及统计对比。完整集合计数、故障报告与恢复过程见[迁移说明](migration-question-library.md)末尾。

迁移前与 dry-run 后计数一致（users 1、surveys 1、responses 4；所有新增集合为 0）；dry-run 报告 3 题可链接且 0 失败。故障注入在版本插入后、最新指针写入前触发；初次 apply 2 题完成、1 题失败，明确保留 partial 记录。重跑后 3 题全部链接、版本仍为 3 个、题库条目为 3 个、失败为 0。迁移前后题目 ID、内容字段、数组顺序、跳转、发布状态/发布时间和 response 文档一致；单问卷统计 JSON 前后相同（submitted 3、单选人数 2/1、文本 1、数字均值 34）。测试库在演练后删除。没有访问或写入真实业务数据库。

### 8.3 查询计划与性能复核

另在专用隔离库 `survey_phase2_final_perf_20261028` 建立 500 份问卷和 8,100 份答卷：400 份当前用户问卷、100 份其他用户问卷、8,000 submitted、100 draft。该聚合返回 8,000 行有效回答；三次端到端时间为 **125.68、147.29、125.85 ms**，中位数 **125.85 ms**。MongoDB 8.3.11 `executionStats` 的首段 winning plan 使用 `ix_surveys_owner_library_version`，返回/检查 400 个问卷文档和索引键；`$lookup` 使用 `ix_responses_survey_status_submitted_at`，`collectionScans=0`、检查 8,000 个索引键/文档并返回 8,000 行。性能库已删除。此样本不代表生产容量或并发能力。

### 8.4 浏览器与静态检查

第二阶段完整双账号 Chrome 手工 UI 验收已在 2026-10-27 完成，记录见第 7.3 节；本轮只对后端迁移错误恢复做了改动，未重新执行完整浏览器流程。当前项目及虚拟环境没有 Playwright、Selenium 或 Puppeteer；虽有交互式 Chrome，但它不能直接作为仓库中可重复运行的浏览器测试。为避免为本轮测试引入新的依赖/测试栈，没有添加自动化浏览器套件，也没有将 API 测试称为浏览器测试。多浏览器和实体设备验收仍待后续。

最终交付静态检查实测：`.venv/bin/python -m compileall -q backend tests`、`node --check frontend/js/app.js` 和 `git diff --check` 均退出码 0；扫描 README 与 docs 中 33 个本地 Markdown 链接，没有缺失链接；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。仓库文件扫描仅发现 `.env.example`，未发现带用户名/密码的 MongoDB URI；未发现超过 20 MiB 的文件。根目录现有 946,395 字节的未跟踪 PDF 未删除或移动。分支仍为 `main`，既有工作区变更保留；未重置、提交或推送。
