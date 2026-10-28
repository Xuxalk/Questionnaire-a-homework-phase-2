# API 接口说明（第一阶段与第二阶段）

## 1. 通用约定

- 默认地址：`http://127.0.0.1:8000`。所有接口使用 JSON；路径 ID 为字符串形式的 MongoDB `ObjectId`，题目 ID 和选项 ID 是问卷内的字符串。
- 登录成功后由服务端设置 `survey_session` 签名 Cookie。除注册、登录、健康检查外，业务接口均需有效登录会话；统计和问卷管理还要求当前用户是问卷创建者。
- 变更请求（POST、PUT、PATCH、DELETE）会校验浏览器 `Origin` 和 Fetch Metadata。跨站及同站跨源浏览器请求返回 403。
- 成功响应不包含 `password_hash`、`respondent_id` 或内部生命周期标记。匿名问卷填写仍要求登录；匿名主要约束创建者侧数据投影。
- FastAPI 运行时也提供 `/docs`（Swagger UI）和 `/openapi.json`。本文件以当前代码路由及 Pydantic 请求模型为准。

## 2. 账号与会话

| 方法 | 路径 | 登录要求 | 成功结果 | 主要错误 |
|---|---|---|---|---|
| `POST` | `/api/auth/register` | 否 | 201，公开用户对象 | 409 用户名重复；422 输入不合法；503 未配置 MongoDB |
| `POST` | `/api/auth/login` | 否 | 200，公开用户对象并设置 Cookie | 401 凭据错误；503 未配置会话密钥或数据库 |
| `POST` | `/api/auth/logout` | 否 | 204，清除 Cookie | — |
| `GET` | `/api/auth/me` | 是 | 200，当前用户 | 401 未登录 |

注册/登录请求：

```json
{"username":"Alice","password":"Correct horse 42!"}
```

用户名首尾空格会清理，大小写不敏感地检查唯一性；密码长度为 8–128 字符（登录密码最少 1 字符）。注册和登录响应形状：

```json
{"id":"64f000000000000000000001","username":"Alice","created_at":"2026-10-19T03:00:00Z","updated_at":"2026-10-19T03:00:00Z"}
```

密码使用 Argon2id 哈希后保存。登录 Cookie 为 HttpOnly、SameSite=Lax、有效期 7 天；是否设置 Secure 由 `SESSION_COOKIE_SECURE` 决定。

## 3. 问卷管理

除健康检查外，以下管理路由均要求登录，并以当前用户 `owner_id` 限定访问范围。非本人和不存在问卷统一返回 404。

| 方法 | 路径 | 用途 | 成功状态 |
|---|---|---|---:|
| `POST` | `/api/surveys` | 创建草稿 | 201 |
| `GET` | `/api/surveys` | 列出自己的问卷，按更新时间倒序 | 200 |
| `GET` | `/api/surveys/{survey_id}` | 查看自己的问卷详情 | 200 |
| `PATCH` | `/api/surveys/{survey_id}` | 编辑标题、说明；草稿时可编辑匿名及重复提交设置 | 200 |
| `PUT` | `/api/surveys/{survey_id}/deadline` | 设置或清除截止时间 | 200 |
| `POST` | `/api/surveys/{survey_id}/publish` | 校验并发布草稿 | 200 |
| `POST` | `/api/surveys/{survey_id}/close` | 关闭已发布问卷 | 200 |
| `POST` | `/api/surveys/{survey_id}/questions` | 在草稿末尾添加题目 | 201 |
| `PATCH` | `/api/surveys/{survey_id}/questions/{question_id}` | 替换一题的定义 | 200 |
| `DELETE` | `/api/surveys/{survey_id}/questions/{question_id}` | 删除草稿中的题目 | 200 |

创建问卷：

```json
{
  "title":"用户体验调查",
  "description":"请按实际体验填写",
  "anonymous":true,
  "allow_multiple_submissions":false
}
```

题目顺序由数组创建顺序决定，目前没有重排接口。所有题型共有 `type`、`prompt`、`required`、`jump_rules` 字段；选项题还需 `options`：

```json
{
  "type":"single_choice",
  "prompt":"你选择哪项？",
  "required":true,
  "options":[{"label":"A"},{"label":"B"}],
  "jump_rules":[
    {"priority":1,"condition":{"operator":"equals","option_id":"option-id"},"target_question_id":"later-question-id"}
  ]
}
```

四类题目的专属请求字段：

| `type` | 请求字段 | 当前校验 |
|---|---|---|
| `single_choice` | `options:[{option_id?,label}]` | 至少 1 个选项；单选跳转条件 `equals + option_id` |
| `multi_choice` | `options`、`validation:{min_selected,max_selected}` | 0 ≤ min ≤ max ≤ 选项数；跳转条件 `{match:"ANY" 或 "ALL",option_ids:[...]}` |
| `text` | `validation:{min_length?,max_length?}` | 非负且 min ≤ max；文本题目前不支持跳转条件 |
| `number` | `validation:{min_value?,max_value?,integer_only}` | 数字边界需有限且 min ≤ max；跳转支持 `eq/ne/gt/gte/lt/lte` |

跳转目标必须存在且位于源题之后；同一题 priority 不重复，按数值升序检查，第一条命中生效；无匹配规则进入下一题。规则随问卷保存，不写死在跳转引擎中。发布后题目、匿名和重复提交策略锁定；标题/说明可改；问卷关闭后截止时间锁定。

截止时间使用带时区的 ISO 8601 日期时间；`null` 清除截止时间。无时区时间返回 422。

## 4. 填写与提交

填写路由均要求登录，包括匿名问卷。只可读取已发布或已关闭问卷；草稿和不存在的问卷均返回 404。分享链接由前端生成 `/?survey={survey_id}`。

| 方法 | 路径 | 用途 | 成功状态 |
|---|---|---|---:|
| `GET` | `/api/fill/{survey_id}` | 读取分享问卷、当前截止/关闭状态和题目 | 200 |
| `POST` | `/api/fill/{survey_id}/responses` | 开始或恢复当前账号的唯一 draft | 201 |
| `GET` | `/api/fill/{survey_id}/responses/{response_id}` | 读取当前账号自己的答卷进度 | 200 |
| `PUT` | `/api/fill/{survey_id}/responses/{response_id}/steps/{question_id}` | 保存一题答案或主动跳过 | 200 |
| `POST` | `/api/fill/{survey_id}/responses/{response_id}/submit` | 校验并提交答卷 | 200 |

保存答案示例（当前 question type 决定 `answer` 形状）：

```json
{"revision":0,"answer":{"option_id":"option-id"}}
```

```json
{"revision":1,"answer":{"option_ids":["option-a","option-b"]}}
```

```json
{"revision":2,"answer":{"text":"补充内容"}}
```

```json
{"revision":3,"answer":{"number":27}}
```

主动跳过非必答题：

```json
{"revision":4,"skip":true}
```

`answer` 与 `skip:true` 不可同时使用。每次保存需携带最新 `revision`；成功后 revision 递增。服务端重新计算当前路径并清除不可达步骤和答案。提交请求为 `{"revision":5}`；只有当前路径中所有必答题已回答、非必答题已回答或主动跳过时才能提交。已提交答卷不可再修改。

填写进度响应主要字段：`id`、`survey_id`、`status`、`revision`、`steps`、`path_question_ids`、`next_question_id`、`complete`、时间戳。匿名读取投影不包含答题账号 ID。

主要错误：401 未登录；404 问卷/答卷不存在、不可分享或不属于当前账号；409 重复提交、已提交修改、过期 revision 或不可达题目；410 问卷已关闭/过期；422 答案不合法、跳过必答题或答卷不完整。答卷不完整时 `detail` 为 `{"message":"Response is incomplete","next_question_id":"..."}`。

## 5. 统计

统计 API 仅登录后的问卷创建者可访问；非本人或不存在问卷/题目返回 404。

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/statistics/surveys/{survey_id}` | 问卷提交次数及全部题目统计 |
| `GET` | `/api/statistics/surveys/{survey_id}/questions/{question_id}` | 单题统计 |

返回内容包括 `submitted_count`、题目 ID/题干/类型/`answered_count`；单选选项返回每项人数，多选选项返回选择次数，文本题返回回答列表和提交时间，数字题返回平均值。只统计 `submitted` 答卷中的 `answered` 步骤；draft、skipped 和未经过的题目不计入。统计响应不返回答卷 ID、用户 ID 或用户名。

## 6. 常见错误响应与限制

| HTTP | 含义 |
|---:|---|
| 401 | 缺少或失效的会话；或登录凭据不正确 |
| 403 | 浏览器来源校验拒绝跨源变更请求 |
| 404 | 无权限查看的问卷/答卷按不存在处理 |
| 409 | 唯一性冲突、状态冲突、重复提交或草稿 revision 过期 |
| 410 | 问卷关闭或截止 |
| 422 | JSON 字段/题目/答案/提交路径校验失败 |
| 503 | MongoDB 未配置，或登录会话密钥未配置 |

请求模型默认拒绝未识别字段。MongoDB URI、数据库名、会话密钥及 Cookie Secure 开关通过环境变量配置，详见项目根目录 README 和 `.env.example`。

## 7. 第二阶段第一批：个人题库与版本

下列接口全部需要登录。题库和题目族默认私有；题目所有者可管理版本，接收者只能读取和使用明确授权的精确版本。未授权、已撤销或非所有者访问按 404 处理。题目版本不可更新或删除；版本编辑与恢复都会创建新的版本文档。

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/question-library/banks` | 列出当前用户题库；首次访问时惰性创建默认“个人题库”。 |
| `POST` | `/api/question-library/banks` | 创建私有题库，正文 `{"name":"常用问题"}`。 |
| `GET` | `/api/question-library/banks/{bank_id}/items` | 列出题库固定版本及题目族最新版本。 |
| `POST` | `/api/question-library/banks/{bank_id}/items` | 将已有题目族/版本加入题库，正文 `{"family_id":"...","version_id":"..."}`；重复加入返回 409。 |
| `PUT` | `/api/question-library/banks/{bank_id}/items/{family_id}` | 显式更新题库条目使用版本，正文 `{"version_id":"..."}`。 |
| `DELETE` | `/api/question-library/banks/{bank_id}/items/{family_id}` | 从该题库移除条目；不删除题目族、版本或问卷快照。 |
| `POST` | `/api/question-library/questions` | 直接创建私有题目族及 v1，并加入默认题库或 `?bank_id=...` 指定题库。请求使用原题型结构，`jump_rules` 必须为空。 |
| `POST` | `/api/question-library/from-survey/{survey_id}/{question_id}` | 将自己问卷中的题目保存到个人题库；正文可传 `{"bank_id":"..."}`，省略时使用默认库。题目内容不改，只增加来源版本元数据。 |
| `GET` | `/api/question-library/questions/{family_id}/versions` | 按版本号升序读取当前用户题目族的版本历史。 |
| `POST` | `/api/question-library/questions/{family_id}/versions?parent_version_id=...` | 从指定父版本建立不可变版本；省略父 ID 时基于题目族最新版本。正文使用原题型结构，且 `jump_rules` 必须为空。内容与父版本相同则返回父版本而不重复建档。 |
| `POST` | `/api/question-library/questions/{family_id}/versions/{version_id}/restore` | 将历史版本内容恢复为新版本；`parent_version_id` 是恢复前最新版本，响应含 `restored_from_version_id`。 |

### 将题目放入问卷

`POST /api/surveys/{survey_id}/questions/from-library` 只允许本人草稿问卷，正文：

```json
{
  "source_question_id": "题目族 ID",
  "source_version_id": "选定版本 ID"
}
```

后端生成新的问卷本地 `question_id`，复制题目/校验内容，记录 `library_ref`；原题选项 ID 保持在本份题目定义内。来源版本中的跳转目标不会复制，返回题目的 `jump_rules` 为空。问卷作者可以按本问卷题目重新添加跳转规则。

原有 `POST/PATCH/DELETE /api/surveys/{survey_id}/questions...` 保持可用。对带来源引用的草稿题目执行 PATCH 时，如果题目内容改变，服务会创建一个以来源版本为父的新不可变版本并将问卷草稿关联到新版本；只改变问卷本地跳转规则则不创建题目内容版本。发布后题目仍锁定。

### 错误码

题库接口沿用项目通用错误：401 未登录；404 题库/题目/版本不存在或无权访问；409 重复加入、草稿状态/并发变化冲突；422 输入题型、校验或题库名称不合法；503 MongoDB 未配置。

## 8. 第二阶段第二批：版本共享与使用清单

共享权限按**题目族 + 精确不可变版本 + 接收用户**记录。分享不授予编辑、版本历史、恢复或其他版本访问权；新版本不会继承授权。撤销是软状态变更，不删除授权记录，也不修改任何已复制的问卷题目快照。

| 方法 | 路径 | 用途与权限 |
|---|---|---|
| `POST` | `/api/question-library/questions/{family_id}/versions/{version_id}/shares` | 题目所有者向已有用户名授权精确版本；正文 `{"recipient_username":"reader"}`。重复授权返回现存状态；重新授权已撤销记录会显式恢复 active。 |
| `GET` | `/api/question-library/questions/{family_id}/versions/{version_id}/shares` | 所有者查看该版本的授权状态，包括 active/revoked、接收者用户名及时间。 |
| `DELETE` | `/api/question-library/questions/{family_id}/versions/{version_id}/shares/{recipient_user_id}` | 所有者撤销指定用户；重复撤销幂等返回 revoked。不存在的授权返回 404。 |
| `GET` | `/api/question-library/shares/received` | 接收者列出仍 active 的授权版本。返回题目快照内容，不返回父版本、恢复来源、最新版本指针或其他未授权版本信息。 |
| `GET` | `/api/question-library/questions/{family_id}/usages` | 所有者查看该题目族各版本使用情况；可加 `?version_id=...` 限定精确版本。接收者必须指定当前获准的精确版本。 |

### 使用清单字段与隐私

使用记录实时查询 `surveys.questions[].library_ref`，不写冗余 usage 集合。`own_surveys` 只包含当前用户拥有的问卷，逐项返回 `survey_id`、标题、`owner_id`、问卷状态、问卷内 `question_id`、来源 `version_id`，以及快照内容是否仍匹配精确版本。只改标题或问卷状态不改变题目使用关联；删除/移除题目后查询即不再返回该项。

题目所有者对其他用户的使用只得到按版本汇总的 `exact_survey_count` 和 `adapted_survey_count`，不会返回其他问卷的 ID、标题、所有者、问卷内题目 ID 或答卷/统计数据。共享接收者只会得到自己拥有的问卷明细，不返回其他用户使用汇总。若授权被撤销，接收者的 received 列表、版本使用清单和新增复用均不可再访问；此前复制的本地快照及已发布问卷、答卷和单问卷统计仍按原规则工作。

修改共享版本副本时，问卷所有者只编辑自己的草稿快照。若其不是来源题目族所有者，不会向来源题目族追加版本；来源引用保留为血缘，使用清单将该条目标为“已有本地修改”。题目族所有者在自己的来源草稿中修改内容时，仍会创建新版本。

共享端点：401 未登录；404 资源不存在、未授权、非所有者或用户名不存在；422 输入格式错误或不能向自己分享；成功授权/撤销/查询分别为 200。接收者成功读取 `shares/received` 和 usage 为 200。访问版本历史/恢复/新版本写接口仍仅限所有者。

## 9. 第二阶段第三批：跨问卷题目统计

跨问卷统计只读取当前用户拥有的已发布或已关闭问卷。对共享题目的接收者，精确版本授权只允许其查看自己问卷中的统计，不会授权查看题目所有者或其他用户的答卷。版本目录对题目所有者显示完整历史；接收者仅看到当前仍授权的精确版本。撤销后该版本统计访问返回 404。

| 方法 | 路径 | 用途与权限 |
|---|---|---|
| `GET` | `/api/statistics/question-library/{family_id}/versions` | 题目所有者列出题目身份下全部版本；接收者列出自己仍获准的精确版本。 |
| `GET` | `/api/statistics/question-library/{family_id}/versions/{version_id}` | 按精确不可变版本统计当前用户有权查看的问卷；可附 `?survey_id=...` 限定一份自己拥有的已发布/已关闭问卷。响应同时包含 `aggregate` 汇总视图和 `surveys[]` 分问卷视图，前端可切换显示。 |

统计先用问卷快照中的 `library_ref.source_question_id` 和 `source_version_id` 精确查找问卷内题目实例，再按本地 `question_id` 关联 `responses.steps[]`。只有 `status:"submitted"` 且 `steps.status:"answered"` 的记录计入；draft、skipped、未到达及不兼容答案结构排除。重复提交按每条有效提交回答计数；同一问卷中重复引用同一版本的不同本地题目 ID 分别计数。单选比例分母是有效回答数；多选比例分母是有效回答数，多个选项的比例合计允许超过 100%。文本结果带问卷和本地题目上下文；数字结果提供数量、平均值、最小/最大值，`integer_only` 版本还返回数值分布。

响应包含 `version`、`access_scope`、`scope`、`unit`、`selected_survey_id`、`aggregate` 与 `surveys`。汇总含匹配/纳入/不兼容问卷数、兼容题目实例数及有效回答数；每个问卷项列出问卷 ID/标题/状态及本地题目 ID、兼容状态和该实例的题型统计。结果为空时数量为 0、平均值为 `null`、选项计数为零、文本列表为空。若某个快照的题型/选项 ID 与标签/答案校验与选定版本不兼容，该实例在分问卷视图标记原因，不进入跨问卷汇总；不尝试按标题或题目文案合并版本。

统计请求对不存在题目族/版本、未授权版本、他人问卷、draft 问卷或无权访问的问卷统一返回 404。对本人已关闭问卷仍允许读取历史统计；关闭状态不抹除先前的 submitted 答卷。统计端点不会返回答卷 ID、答题人 ID 或用户名。
