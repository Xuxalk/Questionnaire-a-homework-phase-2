# MongoDB Schema v1.1：后端开发参考草案

## 文档状态与证据边界

本文将已确认的 Schema v1.0/v1.1 决策与当前后端实现契约整理在一起。当前目录没有此前评审的完整原始字段目录或 MongoDB validator，因此不能据此声称历史草案的每个字段名都已被逐字复原；本文件会明确区分已确认设计与本阶段工程实现约束。

- **已确认**：用户明确确认的集合、嵌入关系、状态和行为。
- **实现契约**：当前代码实际写入/读取的 BSON 字段和取值。
- **工程约束**：落实已确认功能所需的输入边界、索引或状态处理；不是老师原始要求。

**实现进度说明：**账号、问卷管理、填写/提交、统计 API 和原生前端已落地；第 10、11 节记录当前接口与存储契约。实现细节是工程决策，不代表老师原始作业中的额外要求。

MongoDB 顶层 `_id`、`survey_id`、`respondent_id` 使用 BSON `ObjectId`；嵌入问题和选项 ID 使用字符串 UUID。JSON 示例采用 MongoDB Extended JSON 表示日期和 ObjectId。

## 1. 总体模型

| Collection | 用途 | 已确认关系 |
|---|---|---|
| `users` | 注册账号和登录凭据 | 一名用户可以创建多份问卷，也可以填写多份问卷 |
| `surveys` | 问卷元数据及完整题目定义 | `questions`、选项、校验和跳转规则嵌入同一问卷文档 |
| `responses` | 一名用户对一份问卷的一次填写生命周期 | 通过问卷 ID 和用户 ID 关联；按路径保存于 `steps` |

本模型不为 `questions`、统计结果或匿名身份另建集合。匿名问卷仍在答卷内部关联用户；创建者的问卷/统计读取必须隐藏该关联身份。

## 2. `users` 字段目录

老师原始要求是支持用户注册/登录并保存用户名、密码和注册时间；原始要求没有规定密码存储算法、大小写规范、字段名或时间字段命名。以下注册字段和算法是本项目注册模块的工程决策，不是老师原始要求。

| 示例字段 | BSON 类型 | 必填 | 含义/状态 |
|---|---|---:|---|
| `_id` | `ObjectId` | 是 | MongoDB 用户主键；由应用生成后写入。 |
| `username` | `String` | 是 | 去除首尾空白后的用户名，保留用户提交的大小写，长度 1–64 字符。首尾空格清理为本模块工程决策。 |
| `username_normalized` | `String` | 是 | `username.strip().casefold()` 的结果；用作大小写无关的唯一键。规范化算法为本模块工程决策。 |
| `password_hash` | `String` | 是 | Argon2id 编码哈希字符串；输入密码不得明文持久化。 |
| `created_at` | `Date` (UTC) | 是 | 注册创建时间；对应老师要求的注册时间。 |
| `updated_at` | `Date` (UTC) | 是 | 用户文档最近更新时间；注册时与 `created_at` 相同。该字段是本模块工程决策。 |

**注册后的用户 JSON 示例**

```json
{
  "_id": {"$oid": "64f000000000000000000001"},
  "username": "Alice",
  "username_normalized": "alice",
  "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$<随机salt及哈希内容>",
  "created_at": {"$date": "2026-10-16T08:00:00Z"},
  "updated_at": {"$date": "2026-10-16T08:00:00Z"}
}
```

### 2.1 原始要求与注册模块工程决策

| 项目 | 老师原始要求 | 本次注册实现的工程决策 |
|---|---|---|
| 账号字段 | 用户名、密码、注册时间 | 持久化 `_id`、`username`、`username_normalized`、`password_hash`、`created_at`、`updated_at`。 |
| 用户名唯一性 | 未规定大小写或空白规则 | 输入先 `strip()`；`username` 保存清理后原始大小写；唯一键由清理后字符串 `casefold()` 得到。 |
| 密码存储 | 未规定算法或长度 | 用 `argon2-cffi` 的 Argon2id 生成编码哈希；API 接受长度 8–128 的密码字符串，密码不做 trim。长度限制是工程校验，不是课程需求。 |
| 用户名长度 | 未规定 | API 接受清理后 1–64 个字符；为工程校验，不是课程需求。 |
| 时间戳 | 要求注册时间 | 使用 UTC `created_at`；同时增加 `updated_at`，注册时二者相同。 |
| 注册响应 | 未规定 | 返回用户 ID、用户名及时间戳，不返回 `password_hash` 或 `username_normalized`。 |

唯一索引是并发注册的最终裁决点；应用将 MongoDB `DuplicateKeyError` 映射为用户名冲突（HTTP 409）。

## 3. `surveys` 字段目录

问卷需支持标题、说明、匿名设置、重复提交设置、截止/关闭、发布状态和题目列表。下表记录当前代码实际使用的字段；字段名、长度等工程约束不代表老师原始要求。

| 示例字段 | BSON 类型 | 必填 | 含义/状态 |
|---|---|---:|---|
| `_id` | `ObjectId` | 是 | 问卷主键；分享 API 使用其字符串形式作为路径 ID。 |
| `owner_id` | `ObjectId` | 是 | 创建者账号引用；管理查询匹配当前用户。 |
| `title` | `String` | 是 | 问卷标题；当前 API 限制为 1–200 字符。 |
| `description` | `String` | 是 | 问卷说明；缺省为空字符串，最多 2000 字符。 |
| `anonymous` | `Boolean` | 是 | 匿名语义：答题者必须登录，创建者不能看到身份，系统仍保留内部用户关联。 |
| `allow_multiple_submissions` | `Boolean` | 是 | 是否允许同一账号重复提交；按问卷设置。 |
| `status` | `String` | 是 | `draft`、`published` 或 `closed`；状态只向前转换。 |
| `deadline` | UTC `Date` 或 `null` | 是 | 截止时间；带时区输入并规范化为 UTC。 |
| `questions` | `Array<Document>` | 是 | 题目、选项、校验和跳转规则嵌入同一问卷；数组顺序即默认填写顺序。 |
| `created_at`, `updated_at` | UTC `Date` | 是 | 创建及最近更新时间。 |
| `published_at`, `closed_at` | UTC `Date` 或 `null` | 是 | 对应状态转移时间；未发生时为 null。 |

### 3.1 嵌入式问题、选项、校验与跳转

题目 ID 和选项 ID 是字符串，在各自问卷/题目范围内唯一。题目 ID 由后端生成 UUID；选项 ID 默认由后端生成 UUID，也可在编辑时由客户端提交以保留既有引用。以下字段是当前 API 契约。

| 子文档/字段 | BSON 类型 | 必填 | 含义/状态 |
|---|---|---:|---|
| `question_id` | `String` | 是 | 问题稳定标识；在本问卷内唯一。 |
| `type` | `String` | 是 | `single_choice`、`multi_choice`、`text` 或 `number`。 |
| `prompt` | `String` | 是 | 题目文字；当前 API 限制 1–1000 字符。 |
| `required` | `Boolean` | 是 | 是否必答；非必答题允许主动跳过。 |
| `options` | `Array<Document>` | 仅单选/多选 | `option_id` 字符串和 `label`；至少一个选项，至多 100 个。 |
| `validation` | `Document` | 依题型 | 多选 `{min_selected,max_selected}`；文本 `{min_length?,max_length?}`；数字 `{min_value?,max_value?,integer_only}`。 |
| `jump_rules` | `Array<Document>` | 否 | `{priority,condition,target_question_id}`；每题 priority 正整数且唯一，每题最多 100 条。 |

已确认的校验语义：单选题按选项回答；多选题支持最少和最多选择数；文本题支持最少和最多字符数；数字题支持最小/最大值和整数约束。多选跳转条件支持 `ANY`/`ALL`；数字条件支持 `eq`、`ne`、`gt`、`gte`、`lt`、`lte`。跳转按 priority 数值升序取首条命中规则；无命中走下一题；第一阶段目标只能是当前题之后的题目。

下方 JSON 使用当前代码采用的字段和规则语法。

**当前实现的问卷 JSON 风格示例**

```json
{
  "_id": {"$oid": "64f000000000000000000002"},
  "owner_id": {"$oid": "64f000000000000000000001"},
  "title": "使用习惯调查",
  "description": "示例说明",
  "anonymous": true,
  "allow_multiple_submissions": true,
  "status": "published",
  "deadline": null,
  "created_at": {"$date": "2026-10-16T08:00:00Z"},
  "updated_at": {"$date": "2026-10-16T08:30:00Z"},
  "questions": [
    {
      "question_id": "q1",
      "type": "single_choice",
      "prompt": "是否继续？",
      "required": true,
      "options": [
        {"option_id": "continue", "label": "继续"},
        {"option_id": "shortcut", "label": "跳到最后一题"}
      ],
      "jump_rules": [
        {
          "priority": 1,
          "condition": {"operator": "equals", "option_id": "shortcut"},
          "target_question_id": "q5"
        }
      ]
    },
    {
      "question_id": "q2",
      "type": "multi_choice",
      "prompt": "选择喜欢的水果",
      "required": false,
      "options": [
        {"option_id": "apple", "label": "苹果"},
        {"option_id": "banana", "label": "香蕉"},
        {"option_id": "grape", "label": "葡萄"}
      ],
      "validation": {"min_selected": 1, "max_selected": 3},
      "jump_rules": [
        {
          "priority": 1,
          "condition": {"match": "ANY", "option_ids": ["banana"]},
          "target_question_id": "q4"
        },
        {
          "priority": 2,
          "condition": {"match": "ALL", "option_ids": ["apple", "grape"]},
          "target_question_id": "q5"
        }
      ]
    },
    {
      "question_id": "q3",
      "type": "text",
      "prompt": "补充说明",
      "required": true,
      "validation": {"min_length": 1, "max_length": 200}
    },
    {
      "question_id": "q4",
      "type": "number",
      "prompt": "年龄",
      "required": true,
      "validation": {"min_value": 0, "max_value": 120, "integer_only": true},
      "jump_rules": [
        {
          "priority": 1,
          "condition": {"operator": "gt", "value": 100},
          "target_question_id": "q5"
        }
      ]
    },
    {
      "question_id": "q5",
      "type": "text",
      "prompt": "结束前的补充意见",
      "required": false,
      "validation": {"min_length": 0, "max_length": 200}
    }
  ]
}
```

此例中的数字范围和长度只是示范问卷内容，不是系统级固定限制。示例规则目标都位于源问题之后；实际创建/发布问卷时须验证这一点。发布时间后题目不能修改。

## 4. `responses` 字段目录

| 示例字段 | BSON 类型 | 必填 | 含义/状态 |
|---|---|---:|---|
| `_id` | `ObjectId` | 是 | 单份答卷生命周期主键。 |
| `survey_id` | 与 `surveys._id` 相同 | 是 | 所答问卷引用。 |
| `respondent_id` | 与 `users._id` 相同 | 是 | 内部答题账号引用；匿名时仍保存，但不向创建者暴露。 |
| `status` | `String` | 是 | 已确认取值 `draft`、`submitted`；提交后不可修改。 |
| `lifecycle_guard` | `String` | 条件必填 | 用于部分唯一索引的生命周期占位字段；按是否允许重复提交赋值或删除。 |
| `revision` | 整数（BSON `int32`/`int64`） | 是 | 草稿乐观并发版本；创建时为 `0`，每次成功逐题保存和提交时递增。 |
| `steps` | `Array<Document>` | 是 | 只记录实际进入的题目；每步区分 `answered` 与主动 `skipped`。未到达/跳过路径的题目不造 step。 |
| `created_at` | `Date` (UTC) | 是 | 答卷创建时间。 |
| `updated_at` | `Date` (UTC) | 是 | 最近一次成功保存或提交时间。 |
| `submitted_at` | `Date` (UTC) | submitted 时必填 | 成功提交时间；draft 不包含该字段。 |

每个 `steps` 元素包含 `question_id`、`status`，已回答时另含 `value`；主动跳过时不包含答案。以下是当前代码实际采用的答案形状。

### 4.1 答案形状与校验

| 题型 | 候选 `value` | 业务校验 |
|---|---|---|
| 单选 | `{"option_id":"continue"}` | 对象仅含 `option_id`；ID 必须属于该题。 |
| 多选 | `{"option_ids":["apple","grape"]}` | 对象仅含 `option_ids`；ID 属于该题且不重复；数量满足 `min_selected`/`max_selected`。 |
| 文本 | `{"text":"示例回答"}` | 对象仅含字符串 `text`；长度满足该题配置。 |
| 数字 | `{"number":27}` | 对象仅含有限数字 `number`；不得为布尔值；满足范围和整数约束。 |

**draft JSON 风格示例**（匿名/重复提交设置为允许重复；`q2` 是非必答，`q4` 尚未到达/回答，因此没有 step）：

```json
{
  "_id": {"$oid": "64f000000000000000000003"},
  "survey_id": {"$oid": "64f000000000000000000002"},
  "respondent_id": {"$oid": "64f000000000000000000004"},
  "status": "draft",
  "lifecycle_guard": "draft",
  "revision": 3,
  "steps": [
    {
      "question_id": "q1",
      "status": "answered",
      "value": {"option_id": "continue"}
    },
    {"question_id": "q2", "status": "skipped"},
    {
      "question_id": "q3",
      "status": "answered",
      "value": {"text": "我通常在周末填写。"}
    }
  ],
  "created_at": {"$date": "2026-10-16T09:00:00Z"},
  "updated_at": {"$date": "2026-10-16T09:05:00Z"}
}
```

**submitted JSON 风格示例**（四种题型均出现；答卷只因实际经过的路径保存 step）：

```json
{
  "_id": {"$oid": "64f000000000000000000005"},
  "survey_id": {"$oid": "64f000000000000000000002"},
  "respondent_id": {"$oid": "64f000000000000000000004"},
  "status": "submitted",
  "revision": 6,
  "steps": [
    {"question_id": "q1", "status": "answered", "value": {"option_id": "continue"}},
    {"question_id": "q2", "status": "answered", "value": {"option_ids": ["apple"]}},
    {"question_id": "q3", "status": "answered", "value": {"text": "周末填写。"}},
    {"question_id": "q4", "status": "answered", "value": {"number": 27}},
    {"question_id": "q5", "status": "skipped"}
  ],
  "created_at": {"$date": "2026-10-16T09:00:00Z"},
  "updated_at": {"$date": "2026-10-16T09:20:00Z"},
  "submitted_at": {"$date": "2026-10-16T09:20:00Z"}
}
```

当问卷不允许重复提交时，submitted 文档应保留 `"lifecycle_guard":"single"`；上例允许重复提交，故提交时应从文档中 **unset** `lifecycle_guard`，不能保留为 `null`。这里的样例用同一答题账号展示不同状态，仅用于字段说明，不表示实际能从已提交答卷再恢复 draft。

## 5. 索引

索引在应用启动时由对应 Repository 的 `ensure_indexes()` 创建。

### 5.1 第一阶段核心索引

| Collection | 当前启动时确保的索引 | 用途/约束 |
|---|---|---|
| `users` | `{ username_normalized: 1 }`, `unique: true` | 已由注册模块实现；值由清理后的用户名执行 `casefold()` 得到，唯一索引裁决并发冲突。 |
| `surveys` | `{ owner_id: 1, updated_at: -1 }` | 查询某创建者问卷列表并按更新时间排序。若最终不存 `updated_at`，需调整索引。 |
| `responses` | `{ survey_id: 1, respondent_id: 1, lifecycle_guard: 1 }`, `unique: true`, `partialFilterExpression: { lifecycle_guard: { $exists: true } }` | 同一问卷/用户生命周期占位唯一；兼顾最多一份 draft 与禁止重复提交。 |
| `responses` | `{ survey_id: 1, status: 1, submitted_at: -1 }` | 查询问卷提交答卷、按状态/提交时间过滤；统计只读取 submitted。 |

Mongo shell 形式的生命周期索引示例：

```javascript
db.responses.createIndex(
  { survey_id: 1, respondent_id: 1, lifecycle_guard: 1 },
  {
    unique: true,
    partialFilterExpression: { lifecycle_guard: { $exists: true } },
    name: "uq_responses_lifecycle_guard"
  }
)
```

索引如何阻止重复提交：

| 问卷设置 | draft 的 `lifecycle_guard` | 提交时处理 | 结果 |
|---|---|---|---|
| 允许重复 | `"draft"` | 提交时 `$unset` | 释放唯一占位；保留 submitted 文档，之后可新建一份 draft。 |
| 不允许重复 | `"single"` | 状态变为 submitted，但保留 `"single"` | 唯一占位仍被该答卷占用，不能创建第二份 draft/提交。 |

创建 draft 时若并发插入触发 duplicate-key，应用应重新查询并恢复已有 draft，而不是另建答卷。提交和逐题保存均需基于 `status` 与预期 `revision` 做条件更新。MongoDB 唯一索引不负责决定问卷设置或响应 duplicate-key 的业务流程。

### 5.2 性能验证后再决定

| 候选索引 | 适用条件 |
|---|---|
| `responses: { survey_id: 1, status: 1, "steps.question_id": 1 }` | 真实答卷规模下，按题汇总需要过滤单题且当前聚合较慢时再评估；`steps.question_id` 会形成多键索引。 |
| `responses: { respondent_id: 1, status: 1, updated_at: -1 }` | 产品确实提供“我的答卷/历史答卷”列表，且查询计划显示有收益时再加。 |

不为未提出的查询提前增加索引。上线前应使用代表性数据查看 `explain()`、索引大小和写入成本。

## 6. 数据库约束与业务层约束

### 数据库可直接负责

- `_id` 主键唯一。
- `users.username_normalized` 唯一索引保证规范化用户名不重复。
- 上述 `lifecycle_guard` 部分唯一索引，保证其覆盖范围内的生命周期占位不重复。
- MongoDB 单文档原子更新；结合 `{_id, status:"draft", revision:expected}` 条件更新可拒绝过期草稿写入。
- Schema 字段冻结后，可用 collection validator 检查 BSON 类型、必填字段和有限枚举；目前未评审具体 validator，暂不写入工程。

### 必须由业务层执行

- 登录、所有填写者须登录；创建者只能读取自己的问卷和统计。
- 匿名模式隐藏 respondent 身份；不能通过创建者 API、统计结果或日志泄露身份。
- 发布后不允许修改题目；问卷关闭/过期时拒绝新填写/提交并展示截止提示。
- 在同一问卷内保证题目 ID、选项 ID 唯一且稳定；答案中的 option ID 属于对应题目；跳转目标存在且位于源问题之后。
- 根据规则优先级选择首条匹配规则；多选 `ANY`/`ALL` 及数字比较运算按定义解释；无命中进入下一题。
- 校验各题型答案、必答要求、多选数量、文本长度、数字范围和整数要求。
- 只有已进入路径的步骤才需要完成；非必答题可以标记 skipped；跳转未到达的题目不写入已访问步骤。
- submitted 不可改；每次成功提交都保留，不按内容去重。
- 维护 `lifecycle_guard` 与问卷重复提交设置的一致性；处理 duplicate-key 和 revision 冲突。
- 只从 submitted 答卷的 answered steps 计算题目回答数/答案统计；skipped 不算回答，未经过路径的题目不存在于答卷步骤中；问卷提交次数按 submitted 答卷数单独统计。

MongoDB 不提供跨集合外键约束。`survey_id`、`respondent_id` 和嵌入题目/选项/跳转 ID 的存在性与归属关系均由业务校验保证。

## 7. 核心数据写入与读取规则

### 7.1 创建与恢复 draft

1. 从当前问卷设置读取重复提交规则；匿名设置不改变“必须登录”和内部 respondent 关联。
2. 按 `(survey_id, respondent_id)` 查找可恢复的 draft；已存在则返回该 draft，不创建第二份。
3. 不存在时插入 `status:"draft"`、空 `steps`、`revision:0` 及适用的 `lifecycle_guard`。
4. 若禁重问卷已有 submitted 占位，拒绝创建新 draft；允许重复的问卷在前次 submitted 已释放占位后可新建 draft。

### 7.2 逐题保存与 revision

每次回答或主动跳过一题后，在服务器端校验该步骤属于当前可达路径，并按预期 `revision` 更新同一 response 文档。成功更新时写回 `steps`、更新时间并递增 `revision`。条件更新未匹配时表示 draft 已提交/版本已过期；不得静默覆盖较新的步骤。

保存会重算从首题开始的当前路径，并在同一次原子更新中写回仍可达的有序 `steps`。前置答案改变时，不可达步骤及其答案会立即清理；提交时再次校验完整路径。

### 7.3 路径重算与失效步骤清理

1. 从首题开始，按现存已回答答案重新执行动态规则，得到新的可达问题序列。
2. 新路径中仍可达且状态/答案仍有效的步骤可以保留。
3. 不再可达的步骤及其答案必须从 draft `steps` 移除；否则会保留“已保存但不在当前答案路径中”的数据，并污染后续统计/提交校验。
4. 新路径上尚未到达的题目没有 step；到达的非必答题只有用户明确跳过后才记录 `skipped`，未处理状态不得伪装成 skipped。
5. 清理、路径状态更新和 revision 递增必须处于同一次 response 文档原子更新中。

路径计算在 `responses.engine` 中执行，优先级数值较小者先匹配；未匹配规则时进入下一题。

### 7.4 提交

提交请求须携带/使用预期 revision，并在同一草稿文档上执行最终状态更新。业务层先按当前答案重算路径并检查：

- 到达的必答题均为 answered；
- 到达的非必答题为 answered 或 skipped；
- skipped 只用于非必答题，且不含答案 payload；
- 每个 answered 的答案与题型一致，引用的选项有效且校验通过；
- 不可达题目对应的 step 已清除。

校验通过才将状态改为 submitted、写入提交时间，并按 allow-repeat 规则删除或保留 `lifecycle_guard`。草稿和提交状态的写入应有 revision 条件，避免并发保存覆盖提交。

### 7.5 统计读取

- 所有统计均过滤 `responses.status == "submitted"`。
- 问卷总提交数 = submitted response 文档数。
- 题目回答数 = submitted response 中该题 `status == "answered"` 的 step 数；skipped 和不存在的 step 不计。
- 单选按 `option_id` 计人数；多选按每个 answered step 的选项 ID 计选择次数；文本列出 answered 文本；数字平均值只使用有效 answered 数值。
- 匿名问卷统计返回聚合结果，不返回 `respondent_id`。文本答案即使匿名，仍可能包含答题者自愿写入的身份文字；是否额外脱敏规则未确认。

## 8. 乐观并发及一致性边界

- `revision` 是 response 文档的乐观并发令牌，不是问卷版本号。
- 对 draft 写入使用基于 `_id`、`status:"draft"` 和期望 `revision` 的 compare-and-set；同一文档的步骤及 revision 原子修改。
- 同一账号并发创建 draft 由部分唯一索引裁决；duplicate-key 后读取胜出的 draft。
- 多个答卷文档可并行写入；不需要跨用户事务来隔离不同 respondent 的答卷。
- `anonymous` 与 `allow_multiple_submissions` 在问卷发布后锁定，因此生命周期 guard 的策略在存在答卷后不会因问卷编辑而改变。

## 9. 后续扩展对数据结构的影响

| 变化类型 | 可能影响 | 当前不预先实现的内容 |
|---|---|---|
| 新题型 | `questions.type`、该题 `validation`、`steps` 答案 payload、答案校验和统计读取都需扩展。 | 不预建通用插件/任意 JSON 校验框架。 |
| 新跳转条件或回跳 | 影响规则条件结构、循环检测、步骤顺序及路径重算；回跳会改变“每题至多一 step”的假设。 | 第一阶段仅确认向后跳；暂不支持回跳数据结构。 |
| 发布后可修改题目 | 老答卷需要能解释原题目/选项文本和规则；可能需要问卷版本或响应快照。 | 当前发布后不能修改题目，不先建版本集合或快照字段。 |
| 更细匿名/权限规则 | 可能改变 respondent 身份存储、统计投影和授权关系。 | 当前只按匿名不向创建者暴露身份处理。 |
| 统计量/数据规模增长 | 可能新增聚合索引、物化统计或归档策略；嵌入式问卷文档还受 MongoDB 单文档大小上限影响。 | 当前不创建统计集合，也不预设冗余计数。 |

## 10. 当前问卷管理实现契约

本节记录代码实际使用的存储和 API 契约，供前后端联调。它们是当前工程实现决定；若后续需求评审改变，代码和本节应一起更新。

### 10.1 `surveys` 当前字段

问卷文档实际写入：

| 字段 | 类型 | 含义 |
|---|---|---|
| `_id` | `ObjectId` | 问卷主键。 |
| `owner_id` | `ObjectId` | 创建者账号 ID；每个管理查询均同时匹配 `_id` 与 `owner_id`。 |
| `title` | `String` | 标题，首尾空格裁剪，1–200 字符。 |
| `description` | `String` | 说明，最多 2000 字符，缺省保存为空字符串。 |
| `anonymous` | `Boolean` | 匿名设置；填写 API 和创建者统计不返回填写者账号身份字段。自由文本仍可能包含答题者主动写入的身份信息。 |
| `allow_multiple_submissions` | `Boolean` | 重复提交设置。 |
| `status` | `String` | `draft`、`published`、`closed`；状态只允许按 `draft → published → closed` 前进。 |
| `deadline` | `Date` 或 `null` | 带时区输入并规范化为 UTC；已发布时允许修改，关闭后锁定。 |
| `questions` | `Array<Document>` | 按数组顺序保存题目。 |
| `created_at`, `updated_at` | UTC `Date` | 创建及最近修改时间。 |
| `published_at`, `closed_at` | UTC `Date` 或 `null` | 对应状态首次转入时记录。 |

题目 `question_id` 和选项 `option_id` 是问卷内的字符串 ID。未提供选项 ID 时由后端生成 UUID 字符串。编辑题目时前端应回传需要保留的选项 ID，否则新生成的 ID 会导致旧跳转条件引用失效。题目 ID 由后端生成，并在题目编辑时保持不变。

### 10.2 当前题型文档形状

- `single_choice`：`prompt`、`required`、至少一个 `options[{option_id,label}]`、`jump_rules`。
- `multi_choice`：上述选项字段，加必填 `validation:{min_selected,max_selected}`；规则配置可以使用 `ANY`/`ALL` 及 `option_ids`。
- `text`：`prompt`、`required`、`validation:{min_length?,max_length?}`；当前管理 API 不接受文本题跳转规则。
- `number`：`prompt`、`required`、`validation:{min_value?,max_value?,integer_only}`；数值跳转条件保存比较运算符和值。
- 多选的 `min_selected` 从 0 起、`max_selected` 至少为 1，必须满足 min ≤ max ≤ 选项数；文本长度非负且最小值不得超过最大值；数字边界若同时存在，最小值不得超过最大值。
- `jump_rules` 元素形状为 `{priority,condition,target_question_id}`。同一题的 priority 必须唯一；按正整数从小到大排序，较小值优先。无命中时后续答题引擎应按需求继续下一题。
- 第一阶段允许的数字运算符为 `eq`、`ne`、`gt`、`gte`、`lt`、`lte`。单选运算符固定为 `equals`。多选条件要求非空且不重复的 option ID 列表。
- 每题至少一个选项，选项最多 100 个；提示文字 1–1000 字符；选项标签 1–300 字符；每题最多 100 条规则。这些是本轮 API 工程边界，不是老师规定的题量上限。
- 创建题目会追加到问卷数组末尾；当前没有重排接口。通过先创建后续题、再编辑前题，可配置只向后的跳转。被其他规则引用的题目不能删除，需先清除引用。

### 10.3 当前 API、权限与状态约束

所有问卷管理 API 均需登录，接口路径如下：

| 方法 | 路径 | 行为 |
|---|---|---|
| `POST` | `/api/surveys` | 创建草稿；匿名及重复填写策略需显式传入。 |
| `GET` | `/api/surveys` | 列出当前用户问卷，按 `updated_at` 倒序。 |
| `GET` | `/api/surveys/{survey_id}` | 读取自己的完整问卷；非本人或不存在均返回 404。 |
| `PATCH` | `/api/surveys/{survey_id}` | 修改标题、说明；草稿时也可修改匿名及重复填写策略。 |
| `PUT` | `/api/surveys/{survey_id}/deadline` | 设置/清除截止时间；草稿或发布中可用。 |
| `POST` | `/api/surveys/{survey_id}/questions` | 向草稿末尾新增题目。 |
| `PATCH` | `/api/surveys/{survey_id}/questions/{question_id}` | 替换草稿中的题目定义。 |
| `DELETE` | `/api/surveys/{survey_id}/questions/{question_id}` | 删除未被跳转规则引用的草稿题目。 |
| `POST` | `/api/surveys/{survey_id}/publish` | 验证至少一道题、ID/校验/跳转引用后发布。 |
| `POST` | `/api/surveys/{survey_id}/close` | 关闭已发布问卷；重复关闭为幂等操作。 |

发布后，题目和 `anonymous`、`allow_multiple_submissions` 不可修改；标题/说明仍可修改，截止时间可在关闭前修改。这是本轮实现采用的策略边界。发布以题目数组快照作条件更新，避免同时编辑题目时发布未校验版本。题目更新也基于旧题目数组做条件更新；并发编辑冲突返回 409。

应用启动时确保 `users.username_normalized` 唯一索引、`surveys:{owner_id:1,updated_at:-1}` 列表索引，以及第 5 节所列两项 `responses` 索引。

### 10.4 本轮范围外与工程边界

填写 API 使用问卷 ObjectId 作为分享路径标识，不额外生成秘密令牌。匿名仅隐藏答卷中的账号关联；填写者自己仍可读取自己的 draft/submitted 答卷。用户名、密码和题目/答案大小限制是当前 API 工程边界，不是老师规定的上限。

## 11. 当前填写与提交实现契约

### 11.1 分享访问和权限

填写接口均需有效登录会话，包括 `anonymous: true` 的问卷。公开读取只匹配 `published` 或 `closed` 状态；draft、无效 ID 和不存在的问卷统一返回 404。分享投影仅包含展示题目和接收状态，不返回 `owner_id`、`allow_multiple_submissions` 或答卷中的 `respondent_id`。关闭或截止的问卷仍可展示“已截止/已关闭”，但不能开始、保存或提交答卷。

答卷读取始终同时匹配 `{_id, survey_id, respondent_id}`；因此创建者及其他账号无法通过填写端读取别人的答卷。当前没有创建者侧答卷读取 API。

### 11.2 当前填写 API

| 方法 | 路径 | 行为/主要结果 |
|---|---|---|
| `GET` | `/api/fill/{survey_id}` | 登录后读取已发布/已关闭问卷和当前接收状态；draft/不存在返回 404。 |
| `POST` | `/api/fill/{survey_id}/responses` | 开始或恢复当前用户 draft；成功返回 201；禁重问卷已有 submitted 返回 409；已关闭/过期返回 410。 |
| `GET` | `/api/fill/{survey_id}/responses/{response_id}` | 仅当前 respondent 可读取自己的答卷。 |
| `PUT` | `/api/fill/{survey_id}/responses/{response_id}/steps/{question_id}` | 按 revision 保存单题回答或主动跳过；旧 revision/不可达题返回 409，答案不合法/跳过必答题返回 422。 |
| `POST` | `/api/fill/{survey_id}/responses/{response_id}/submit` | 完整校验当前路径并提交；未完成返回 422，旧 revision/重复提交返回 409，成功状态为 `submitted`。 |

`StepSaveRequest` 形状为 `{"revision":0,"answer":{"text":"..."}}` 或 `{"revision":0,"skip":true}`。`skip` 与 `answer` 不可同时使用；缺省 `skip` 为 false。每次成功保存递增 revision，创建 draft 时 revision 为 0，成功提交时再递增一次。响应投影只包含答卷 ID、问卷 ID、状态、revision、steps、当前路径及时间字段，不包含 respondent 标识或生命周期占位字段。

### 11.3 路径与答案约束

- 仅当前从首题按已保存答案推导出的可达题目可保存；跳转未经过的题无 step。
- 单选仅允许属于本题的一个 option ID；多选拒绝重复/外来 ID，并执行最少/最多选择限制。
- 文本按配置检查字符串长度；数字拒绝布尔值、NaN/Infinity，并执行范围及整数限制。
- 动态规则按照每题 priority 升序检查，首条匹配生效；多选 ANY 为任一相交选项，ALL 为条件列出的选项均已选择；数字支持 `eq`、`ne`、`gt`、`gte`、`lt`、`lte`。没有规则命中时进入数组中的下一题。
- 改写历史答案后重算完整路径；仍可达的步骤保留，不再可达的步骤及答案在同次原子保存中清除。非必答题只有用户主动跳过时保存 `{question_id,status:"skipped"}`；题目未被路径经过时不生成步骤。
- 提交会再次重算路径并校验所有已到达题目：必答题必须回答，非必答题必须回答或主动跳过；答案结构及约束再次验证；不得存在当前路径之外的 step。已提交答卷不能修改。

### 11.4 重复填写和并发

同一账号同一问卷最多一份 draft。允许重复填写时 draft 使用 `lifecycle_guard:"draft"`，提交时 unset guard，之后可以创建新 draft；不允许重复填写时 draft 使用 `lifecycle_guard:"single"`，提交后保留该值，阻止再次开始。答案相同的成功提交仍分别保留，不去重。

并发开始由部分唯一索引决定胜出的 draft；DuplicateKey 后重新读取现存 draft。保存和提交通过 `_id`、`survey_id`、`respondent_id`、`status:"draft"`、预期 revision 作 compare-and-set。只有 revision 匹配的一次写入成功；另一写入返回 409，不覆盖成功者数据。

索引与不同集合之间的状态检查不是跨文档事务：问卷被关闭或截止与某个答卷写入同时发生时，当前实现按请求执行时读取到的问卷状态作检查。若未来要求关闭操作与正在执行的答卷写入具有严格全序，需要新增一致性方案评审。

### 11.5 统计读取

统计不新增 Collection 或冗余计数字段，直接读取 `responses`：

- 每个统计查询先由 `surveys` 仓储按 `{_id, owner_id}` 查找；不存在或非创建者统一返回 404。路由需要有效登录会话。
- 问卷提交次数是 `survey_id` 匹配且 `status:"submitted"` 的答卷文档数。
- 答题结果聚合先按 `survey_id`、`status:"submitted"` 筛选，再 unwind `steps` 并只保留 `steps.status:"answered"`。跳过步骤、未到达题目和 draft 均不计入。
- 单选每个 option 的 `count` 是选中该选项的已提交答卷数；多选每个 option 的 `count` 是被选择的次数；文本返回已提交的回答文本及提交时间；数字返回有效已回答数字的平均值和回答数。
- 返回结构只投影题目定义和统计结果，不包含 `respondent_id`、用户文档或答卷 ID。匿名模式不改变统计口径；自由文本内容本身可能由填写者主动写入身份信息。

API 为 `GET /api/statistics/surveys/{survey_id}` 和 `GET /api/statistics/surveys/{survey_id}/questions/{question_id}`。当前沿用 `responses` 的 `{survey_id:1,status:1,submitted_at:-1}` 索引，没有增加统计专用索引。

## 12. 第二阶段第一批：题目版本库实现契约

### 12.1 题目身份和不可变版本

第二阶段第一批新增 `question_families` 作为逻辑题目身份，`question_versions` 保存不可变题目内容。题目族默认 `visibility:"private"`；`owner_id` 是唯一可修改版本、管理授权和恢复历史的用户。被授权用户可以读取和使用精确获准版本，但不能修改来源族或访问其他版本。第一阶段题目迁移时每个 `(survey_id, question_id)` 单独建立身份，不按题干相同合并。

`question_families` 当前字段：

| 字段 | 含义 |
|---|---|
| `_id` | 题目族 ID；迁移到问卷的 `library_ref.source_question_id`。 |
| `owner_id` | 私有题目所有者。 |
| `visibility` | 当前为 `private`；不接受跨用户访问。 |
| `last_version_number` | 分配下一个族内版本号的原子计数器。写入失败可留下编号间隙。 |
| `latest_version_id` | 最近成功建立的版本 ID；题库条目仍显式固定自己的版本。 |
| `origin_survey_id`, `origin_question_id` | 可选的第一阶段题目来源；组合有唯一索引，供迁移/导入幂等。 |
| `created_at`, `updated_at` | UTC 时间。 |

`question_versions` 当前字段：

| 字段 | 含义 |
|---|---|
| `_id` | 不可变版本 ID；关联问卷快照时写入 `source_version_id`。 |
| `family_id`, `version_number` | 题目族及族内版本号。 |
| `parent_version_id` | 基于的版本；分叉可以指向任一旧版本。初始版本为空。 |
| `restored_from_version_id` | 恢复操作所复制的历史版本；恢复版本的父版本仍为恢复前最新版本。 |
| `created_by`, `created_at` | 创建者与 UTC 时间。 |
| `content_hash` | 对规范化题干/题型/必答/选项/校验字段计算的 SHA-256。 |
| `type`, `prompt`, `required`, `options`, `validation` | 题目内容快照。题库版本不存储问卷本地 `question_id` 或 `jump_rules`。 |

版本文档没有更新/删除 API。修改题目会分配新版本和父版本引用；恢复历史版本会复制其内容为新版本，并保留 `restored_from_version_id`。问卷内修改已有题目时，若题目带有来源版本且题目内容发生变化，API 会先建立来源族的新版本，再以 CAS 更新仍为草稿的问卷；仅修改本地跳转规则时保持原来源版本。版本插入与题族最新指针更新不能依赖跨集合事务；若指针写操作异常，Service 会读取当前族状态，并在版本号仍为最新分配值时安全重试一次。持续的数据库故障仍可能留下可恢复的未引用版本或版本号间隙，历史版本不会被自动删除。

### 12.2 题库、条目和版本共享授权

| 集合 | 当前字段/约束 |
|---|---|
| `question_banks` | `_id`、`owner_id`、`name`、`is_default`、`visibility:"private"`、时间戳。首次读取时惰性创建个人默认题库。 |
| `question_bank_items` | `_id`、`bank_id`、`family_id`、明确选用的 `version_id`、`added_by`、时间戳。更换题库版本需显式调用更新接口，不会自动跟随最新版本。 |
| `question_shares` | `_id`、`family_id`、`version_id`、`owner_id`、`recipient_user_id`、`permission:"use"`、`status:"active"/"revoked"`、`granted_by`、`granted_at`、`revoked_at`、`created_at`。唯一授权粒度为题目族 + 精确版本 + 接收者；版本不会继承授权。撤销保留记录并记录 `revoked_at`。 |
| `schema_migrations` | 以唯一 `migration_key` 保存最近一次迁移报告，支持迁移结果核对。dry-run 不写入此集合。 |

### 12.3 问卷快照关联与兼容

新增问卷题目可带可选引用：

```json
{
  "library_ref": {
    "source_question_id": "题目族 ObjectId",
    "source_version_id": "题目版本 ObjectId"
  }
}
```

MongoDB 内部存 BSON `ObjectId`，管理 API 将这两个引用序列化为十六进制字符串。`surveys.questions[]` 仍保存完整题干、题型、选项、校验、问卷本地 `question_id` 和 `jump_rules`；填写引擎和原统计继续只读此问卷快照。复用版本时后端生成新的问卷本地题目 ID，复制题目内容/校验，并初始化空 `jump_rules`；目标问卷作者自行配置跳转。

迁移仅向既有问卷题目添加 `library_ref` 元数据，原 `question_id`、题干、选项 ID、校验、跳转和数组顺序保持原值；`responses` 不迁移、不改字段。按题目族/版本使用查询的索引为 `surveys.questions.library_ref.source_question_id + source_version_id` 多键索引。使用记录来自问卷题目快照，不另建双写 usage 集合；第二批已基于该关联实现按权限过滤的使用清单，第三批基于同一来源关联实现跨问卷统计。

### 12.4 新集合与索引

启动时 `QuestionLibraryRepository.ensure_indexes()` 创建：

| Collection | 索引 | 用途 |
|---|---|---|
| `question_families` | `{owner_id:1, updated_at:-1}` | 所有者题目族列表。 |
| `question_families` | 唯一 `{origin_survey_id:1, origin_question_id:1}`，仅两字段存在时生效 | 保证旧题目的来源映射幂等。 |
| `question_versions` | 唯一 `{family_id:1, version_number:1}` | 族内版本号唯一。 |
| `question_versions` | `{family_id:1, created_at:-1}`；`{parent_version_id:1}` | 历史列表与子版本查找。 |
| `question_banks` | `{owner_id:1, updated_at:-1}` | 题库列表。 |
| `question_banks` | 对 `is_default:true` 文档唯一 `{owner_id:1, is_default:1}` 部分索引 | 每名用户只有一个默认题库。 |
| `question_bank_items` | 唯一 `{bank_id:1, family_id:1}`；另建 `{family_id:1, bank_id:1}` | 防止同一题目族在一本题库重复，及反向查询。 |
| `question_shares` | 唯一 `{family_id:1, version_id:1, recipient_user_id:1}`；另建 `{recipient_user_id:1, status:1, granted_at:-1}`、`{family_id:1, version_id:1, status:1}` | 精确版本授权唯一性、接收者 active 授权列表和所有者版本授权状态查询。 |
| `schema_migrations` | 唯一 `{migration_key:1}` | 保存版本化迁移结果。 |
| `surveys` | `{questions.library_ref.source_question_id:1, questions.library_ref.source_version_id:1}` | 按来源题目/版本定位使用它的问卷快照。 |
| `surveys` | `{owner_id:1, questions.library_ref.source_question_id:1, questions.library_ref.source_version_id:1}` 多键复合索引（`ix_surveys_owner_library_version`） | 跨问卷统计先限定当前用户、问卷状态和精确题目版本，再通过嵌入题目快照定位本地 `question_id`，避免全量问卷应用层扫描。 |

以上新增索引不替换第一阶段 `users`、`surveys.owner_id/updated_at` 和 `responses` 索引。版本/族关联、题库权限及引用一致性仍由 Service 验证，MongoDB 不提供外键约束。第二批启动索引会将第一批空的题目族级授权保留索引替换为精确版本授权索引；若检测到旧索引下已有授权记录或发现不符合预期的索引定义，启动会失败关闭，要求人工审查，不会把旧的宽泛授权自动解释为对所有版本授权。

### 12.5 第二批共享授权与使用清单

`question_shares` 每条文档代表所有者向一个接收者授予某一不可变版本的 `use` 权限。唯一键为 `(family_id, version_id, recipient_user_id)`；记录包含 `owner_id`、`permission`、`status`、`granted_by`、`granted_at`、`revoked_at` 和 `created_at`。重复授权返回已有 active 记录；再次授权已撤销记录会显式恢复 active 并更新时间；撤销是软更新且幂等。版本创建时不复制授权记录。所有共享读取、版本复用、写操作、历史查询和撤销均在 Service 按所有权或精确版本的 active 授权校验；授权不包括题目族编辑、版本历史、恢复或其他版本。

使用记录不增加独立集合，直接按 `surveys.questions[].library_ref` 查询。仓储用 `$elemMatch` 确保题目族和版本 ID 匹配同一个嵌入式题目；服务再以问卷 `owner_id` 过滤可见明细。题目所有者看到自己问卷逐条信息和其他用户按精确版本汇总的数量；共享接收者只能看自己问卷中的逐条信息，不能得到其他用户使用汇总。问卷编辑、移除题目和删除问卷直接改变唯一事实源，因此查询反映当前快照，不会留下冗余 usage 记录。


### 13. 跨问卷统计读取策略

跨问卷统计不新增缓存或汇总集合。统计仓储从 `surveys` 先执行 `$match`：当前用户 `owner_id`、`published/closed` 状态、同一题目快照中的精确 `source_question_id + source_version_id`，必要时附加 `survey_id`；随后 `$unwind questions` 取得本地 `question_id`，用 `$lookup` 一次性关联 `responses` 中 `status:"submitted"` 的文档，并展开 answered steps。MongoDB 查询计划验证 `surveys` 首段使用 `ix_surveys_owner_library_version`，lookup 使用既有 `ix_responses_survey_status_submitted_at`。Service 仅处理被问卷所有权限定的记录，不按题目标题连接。

Service 层核对每个问卷内题目实例的题型、选项 ID/标签及答案校验结构；本地修改为不兼容的实例在分问卷结果标明排除原因，不进入精确版本汇总。统计单位是“问卷内题目实例中的有效已提交回答”；重复提交是独立回答，同一问卷重复使用同一版本时按各本地题目 ID 分开。共享只允许接收者对自己问卷请求该精确版本，不授权读取来源所有者或其他用户答卷。API 字段见 `docs/api.md` 第 9 节，测试和性能基准见 `docs/test-cases-phase1.md` 第 7 节。

2026-10-27 隔离性能样本包含 500 份问卷（当前查询用户 400、其他所有者 100）和 8,100 份响应（8,000 submitted、100 draft）。精确版本查询返回 400 份用户问卷、8,000 个有效回答；3 次端到端测量为 119.45、116.27、122.64 ms，中位数 119.45 ms。MongoDB executionStats 的 winning query plan 选择 `ix_surveys_owner_library_version`，responses lookup 使用 `ix_responses_survey_status_submitted_at`，lookup `collectionScans:0`、`totalKeysExamined:8000`、`totalDocsExamined:8000`。该规模用于功能性性能回归，不构成生产容量承诺。
