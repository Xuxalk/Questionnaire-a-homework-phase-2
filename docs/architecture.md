# 系统架构决策摘要

## 已确认

- 采用 Python、FastAPI、PyMongo、MongoDB；前端使用简单 HTML/CSS/JavaScript；测试使用 pytest。
- 采用轻量级模块化单体，不拆分微服务。
- 业务模块为 `accounts`、`questionnaires`、`responses`、`statistics`，第二阶段新增 `question_library`；第二批在该模块扩展精确版本共享授权与使用清单，第三批在 `statistics` 增加精确版本跨问卷统计。
- HTTP 路由与跳转规则、答案校验等业务逻辑分离。
- 工程初始化阶段先只建立了模块边界和最小 HTTP 应用；后续已按本架构实现账号与问卷管理用例。

## 模块职责边界

| 模块 | 预期职责 |
|---|---|
| accounts | 用户身份与账号相关用例；当前已实现注册、登录、注销和会话认证 |
| questionnaires | 问卷创建、编辑、发布、关闭及题目定义 |
| responses | draft 恢复、逐题保存、路径执行、提交与答卷生命周期 |
| statistics | 基于已提交答卷读取并汇总问卷/题目统计；按精确题目版本执行跨问卷、分问卷统计和版本权限过滤 |
| question_library | 私有题库、题目身份、不可变版本、历史恢复、草稿问卷复用、精确版本共享与授权校验、隐私过滤后的使用清单 |

跳转规则和答案校验应由业务/领域逻辑执行，而不是写入 HTTP 路由处理函数。具体类名、接口与文件拆分留待业务实现时确定。

`accounts`、`questionnaires` 与 `responses` 均按模块内 API → Service → Repository 分层。路由处理 HTTP 参数、依赖和状态码；Service 实现用例、状态转换及权限相关的查询协调；Repository 只负责 MongoDB 查询和写入。跳转目标校验留在 questionnaires Service 中；答题时的路径执行和答案值校验位于 `responses.engine`，与 HTTP 路由分离。

### 身份验证

- 注册和登录在 `modules/accounts`；Argon2id 哈希由 `passwords.py` 生成和验证。
- 登录建立 Starlette `SessionMiddleware` 签名 Cookie，只存用户 ObjectId 字符串；每个需登录的请求再从 `users` 读取账号。没有增加 sessions 集合。
- 会话密钥只能来自 `SESSION_SECRET_KEY`，最少 32 个字符；应用不会使用内置默认密钥。Cookie 名为 `survey_session`，HttpOnly、SameSite=Lax、有效期 7 天。HTTPS 部署应设置 `SESSION_COOKIE_SECURE=true`。
- 所有 `/api/` 的 POST/PUT/PATCH/DELETE 请求都会拒绝 `Sec-Fetch-Site: cross-site` 或 `same-site`，并在提供 `Origin` 时校验其与请求来源一致。这与 SameSite=Lax 一起缓解浏览器 CSRF；未发送浏览器来源头的 API 客户端仍可调用。当前没有同步器 CSRF token，旧浏览器/不发送来源头的场景仍主要依赖 SameSite=Lax。
- 签名 Cookie 内容可被客户端读取但不能在没有密钥时篡改；因此 Cookie 只放用户 ID，不放密码或其他私密信息。注销清除浏览器 Cookie。由于没有服务端会话表，已复制的 Cookie 在过期前无法由管理员单独撤销。
- 未配置密钥时，健康检查和无需认证的注册仍可运行；登录和需认证接口返回 503。

### 问卷管理

`modules/questionnaires/routes.py` 暴露认证后的问卷管理接口；`service.py` 将每个查询限定为当前用户的 `owner_id`，找不到或非本人问卷统一返回 404，避免泄露问卷是否存在。嵌入式题目随 `surveys` 文档整体保存。

问卷生命周期为 `draft → published → closed`，不支持重新打开。草稿可改题目及关键填写策略（`anonymous`、`allow_multiple_submissions`）；发布后这两类字段锁定。标题和说明仍可修改。截止时间可在草稿或已发布状态设置、修改或清除，关闭后不能修改。填写/提交服务在开始、保存和提交时检查状态与截止时间；分享读取仍显示关闭/截止状态。

题目数组顺序即填写顺序；本模块不提供重排接口。题目删除若仍被跳转规则引用会返回 409。跳转优先级使用正整数且数值小者先匹配；目标必须已存在并位于源题之后。单选条件使用 `equals`，多选条件使用 `ANY`/`ALL`，数字条件支持 `eq`、`ne`、`gt`、`gte`、`lt`、`lte`。文本题不支持跳转条件。发布前会再次检查全部规则。

## 当前应用

`backend.app.main:app` 是 FastAPI 入口。`GET /health` 只报告 HTTP 应用存活，不声明 MongoDB 就绪。设置 `MONGODB_URI` 和 `MONGODB_DATABASE` 后，应用启动时连接 MongoDB 并确保 accounts、surveys、responses、题目版本库和题库所需索引。测试只读取 `MONGODB_TEST_URI`，使用随机命名的独立数据库并在结束时只删除该数据库。

账号 API 提供注册、登录、注销和当前用户查询；问卷管理 API 负责创建、草稿编辑、题目定义、发布、截止时间及关闭。填写端位于独立的 `/api/fill` 路由组，仅向登录用户展示已发布/已关闭问卷，不复用创建者的管理查询。`responses` 提供逐题保存、路径执行、草稿恢复、答案修改后的失效步骤清理、revision 并发控制和提交；匿名响应投影不含填写者 ID。`statistics` 仅从 submitted 答卷的 answered steps 生成全卷和单题统计；跨问卷查询按精确题目版本关联 `surveys.questions[].library_ref`，且只匹配当前用户拥有的已发布/关闭问卷。原生 HTML/CSS/JavaScript 前端由 FastAPI 同源托管在 `/`，不增加前端框架或构建步骤。

`question_library` 将可复用身份保存在 `question_families`，内容版本追加写入 `question_versions`；题库和选用版本分别存放在 `question_banks`、`question_bank_items`。版本默认私有，授权由 `question_shares` 按题目族、精确版本和接收者记录；所有权、active 授权、撤销、复用和写入权限均在 Service 层验证。共享只允许读取和复用已获准版本，不允许修改来源题目或访问其他版本，新版本不继承授权。`surveys.questions[]` 继续保存自身题目快照，带有可选 `library_ref`；填写、跳转和单问卷统计仍从 survey 快照读取。使用清单和跨问卷统计均实时查询问卷快照，不增加冗余统计缓存；问卷明细和答卷统计严格限于当前用户拥有的问卷。题目所有者对其他用户问卷的使用清单只收到版本级计数，不会得到问卷标识信息；共享接收者不能读取来源所有者或其他用户的答卷。跨问卷统计按精确 `question_version_id`（`library_ref.source_version_id`）隔离，兼容实例和重复提交分别计数。版本创建、历史查看和恢复不会重写已发布问卷，撤销授权也不影响已复制快照、答卷或历史统计。

填写状态存放在 `responses` 集合的单文档 `steps` 中。创建 draft 时 revision 为 0；保存和提交均以预期 revision 做原子 compare-and-set。部分唯一索引保证最多一份 draft，并在禁止重复提交时保留 `lifecycle_guard:"single"`。关闭问卷与答卷写入分别更新不同集合，当前不使用跨文档事务；请求按检查时读到的状态执行。

### 统计和前端

- `GET /api/statistics/surveys/{survey_id}` 返回问卷提交数及每题结果；`GET /api/statistics/surveys/{survey_id}/questions/{question_id}` 返回单题结果；`GET /api/statistics/question-library/{family_id}/versions/{version_id}` 按精确版本返回本人问卷汇总及逐问卷视图，并支持 `survey_id` 精确限定。所有统计排除 draft 与 skipped steps，统计投影不包含 `respondent_id` 或用户名。
- `frontend/index.html`、`frontend/css/app.css`、`frontend/js/app.js` 构成无构建步骤的单页界面。包含账号注册登录、问卷列表/编辑、题目及跳转规则表单、个人题库、版本历史/恢复/复用、版本共享/撤销、使用清单、按版本的跨问卷统计、分享链接、填写/恢复/提交和统计表格。
- 浏览器与 API 使用同源签名 Cookie；所有 DOM 模板对问卷内容和回答文本进行 HTML 转义。业务校验、路径计算与授权仍由后端负责。
