# 基于 MongoDB 的在线问卷系统

课程项目工程。系统使用 FastAPI、PyMongo 和 MongoDB，保留第一阶段账号、问卷管理、填写、跳转、提交与统计功能，并增量实现个人题库、不可变题目版本、指定用户共享/撤销、使用清单和按精确版本的跨问卷统计。前端继续使用原生 HTML/CSS/JavaScript。

原始课程说明保留在项目根目录：[`大作业一第一阶段.docx`](./大作业一第一阶段.docx)。提交文档：

- [第一阶段项目报告](./docs/phase1-project-report.md)
- [第一阶段测试用例与结果](./docs/test-cases-phase1.md)
- [API 接口说明](./docs/api.md)
- [第二阶段需求变更影响分析与实施记录](./docs/phase2-change-impact-analysis.md)
- [第二阶段项目报告初稿](./docs/phase2-project-report.md)
- [题目版本库迁移说明](./docs/migration-question-library.md)
- [真实 AI 使用日志](./docs/ai-usage-log.md)
- [需求基线](./docs/requirements-baseline-v1.1.md)、[系统架构](./docs/architecture.md)与[MongoDB Schema](./docs/mongodb-schema-v1.1.md)

## 环境要求

- Python 3.9 或更高版本
- MongoDB（开发环境示例使用 `mongodb://127.0.0.1:27017`）

MongoDB URI 和数据库名通过 `MONGODB_URI`、`MONGODB_DATABASE` 配置，不在代码中保存凭据。`.env.example` 只是变量示例；应用不自动读取 `.env` 文件。

登录会话使用签名 Cookie。需要配置随机生成且至少 32 个字符的 `SESSION_SECRET_KEY`；HTTPS 部署设置 `SESSION_COOKIE_SECURE=true`。开发时可用 `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'` 生成密钥，再通过环境变量设置。没有会话密钥时，注册及健康检查仍可用，但登录和所有需要认证的问卷/填写/统计接口会返回 503。

API 的 POST/PUT/PATCH/DELETE 请求会校验浏览器 `Origin` 与 Fetch Metadata，拒绝跨站或同站跨源的浏览器变更请求；同时使用 SameSite=Lax Cookie。没有发送这些浏览器头的非浏览器 API 客户端仍可调用。当前没有同步器 CSRF token，因此生产环境还应启用 `SESSION_COOKIE_SECURE=true` 并使用 HTTPS。

## 安装依赖

在项目根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows PowerShell 激活命令为：

```powershell
.venv\Scripts\Activate.ps1
```

## 配置并启动

```bash
export MONGODB_URI='mongodb://127.0.0.1:27017'
export MONGODB_DATABASE='survey_system'
export SESSION_SECRET_KEY='replace-with-a-random-secret-of-at-least-32-characters'
uvicorn backend.app.main:app --reload
```

Windows PowerShell：

```powershell
$env:MONGODB_URI = 'mongodb://127.0.0.1:27017'
$env:MONGODB_DATABASE = 'survey_system'
$env:SESSION_SECRET_KEY = 'replace-with-a-random-secret-of-at-least-32-characters'
uvicorn backend.app.main:app --reload
```

访问 <http://127.0.0.1:8000/health>，正常时返回 `{"status":"ok"}`。该接口只检查应用进程，不代表 MongoDB 已就绪。MongoDB 连接与会话密钥必须分别配置。

## 当前 API

- 账号：`POST /api/auth/register`、`POST /api/auth/login`、`POST /api/auth/logout`、`GET /api/auth/me`。
- 问卷（均需登录）：`POST /api/surveys`、`GET /api/surveys`、`GET/PATCH /api/surveys/{survey_id}`、`PUT /api/surveys/{survey_id}/deadline`、`POST /api/surveys/{survey_id}/publish`、`POST /api/surveys/{survey_id}/close`。
- 题目（均需登录且只能编辑本人草稿）：`POST /api/surveys/{survey_id}/questions`、`PATCH/DELETE /api/surveys/{survey_id}/questions/{question_id}`。
- 个人题库与版本（均需登录）：`GET/POST /api/question-library/banks`、题库条目管理、题目版本历史/新版本/恢复；草稿可通过 `POST /api/surveys/{survey_id}/questions/from-library` 复用精确版本。版本所有者可以向指定用户授权/撤销精确版本；接收者只能查看和复用 active 授权版本。题目族/版本使用清单按问卷所有权过滤，并对其他用户问卷仅返回允许的版本汇总，不泄漏问卷详情。
- 填写（均需登录；只能访问已发布或已关闭问卷）：`GET /api/fill/{survey_id}`、`POST /api/fill/{survey_id}/responses`、`GET /api/fill/{survey_id}/responses/{response_id}`、`PUT /api/fill/{survey_id}/responses/{response_id}/steps/{question_id}`、`POST /api/fill/{survey_id}/responses/{response_id}/submit`。
- 统计（均需登录）：单问卷统计只对问卷创建者开放；跨问卷统计按精确题目版本，只读取当前用户有权查看的已发布/关闭问卷。接口包括 `GET /api/statistics/surveys/{survey_id}`、`GET /api/statistics/surveys/{survey_id}/questions/{question_id}`、`GET /api/statistics/question-library/{family_id}/versions` 和 `GET /api/statistics/question-library/{family_id}/versions/{version_id}`（可用 `survey_id` 限定单问卷）。个人题库有“跨问卷统计”入口；共享题目不授予查看来源所有者答卷的权限。

交互式 API 文档在启动后可通过 <http://127.0.0.1:8000/docs> 查看；完整前端位于 <http://127.0.0.1:8000/>。接口路径、请求体、权限和状态码见[API 接口说明](./docs/api.md)。FastAPI 同源托管 `frontend/` 文件，不需要单独构建步骤。问卷题目顺序由数组创建顺序决定；当前没有重排接口。填写端按题目优先级执行动态跳转，逐题保存并支持恢复；更改历史答案时会重算路径并清理不可达步骤。匿名问卷仍要求登录，创建者不能通过填写接口读取填写者身份或他人答卷。

统计按问卷提交次数展示总量；单选统计各选项人数，多选统计各选项被选择次数，文本题列出已提交文本回答，数字题计算已回答数字的平均值。跨问卷统计按题目族和精确版本隔离，提供逐问卷及汇总视图，不按题目标题合并。仅当前用户拥有的已发布/关闭问卷中 `submitted` 答卷的 `answered` 步骤参与统计；跳过、未到达步骤和 draft 不计入。跨版本答题语义不兼容的问卷题目会明确排除，汇总不返回无权查看的问卷或用户数据。

## 运行测试

账号和问卷集成测试需要 MongoDB。测试只读取 `MONGODB_TEST_URI`，每个测试使用随机命名的 `survey_test_*` 数据库，并在结束时仅删除自己创建的测试库；不会读取 `MONGODB_URI` 或 `MONGODB_DATABASE`。

```bash
MONGODB_TEST_URI='mongodb://127.0.0.1:27017' \
python -m pytest -q
```

未设置 `MONGODB_TEST_URI` 时，MongoDB 集成测试会跳过；健康检查测试仍可运行。题目迁移默认 dry-run，需人工检查后显式运行 `--apply`；应用启动不会自动迁移现有数据。完整测试步骤、输入、预期与实测结果见[测试用例文档](./docs/test-cases-phase1.md)，迁移操作见[迁移说明](./docs/migration-question-library.md)。

最终审计在本机专用隔离 MongoDB 上完整 pytest 为 **64 passed、0 failed、0 skipped**；测试将 `UnsupportedFieldAttributeWarning` 提升为错误，本轮未复现 warning。编译、前端 JS 语法、文档链接与 `git diff --check` 的结果见[测试用例与交付验证](./docs/test-cases-phase1.md)。旧 Schema 迁移故障恢复演练和查询 explain/性能记录也位于该文档。以上不代表已在生产数据库执行迁移或生产容量验证。

## 当前目录结构

```text
backend/       FastAPI 应用、配置、MongoDB 连接入口和业务模块包
frontend/      原生 HTML/CSS/JavaScript 单页前端
tests/         pytest 自动化测试
docs/          报告、测试用例、API、需求、架构、Schema 和真实 AI 使用日志
大作业一第一阶段.docx  原始课程作业说明
```

## 第一阶段交付材料

第一、二阶段项目报告、测试用例、API 说明、需求/架构/Schema 文档、迁移说明及 AI 使用日志均保存在既有 `docs/`。AI 使用日志依据可核验的提示词、代码改动和测试结果整理；模型标识或早期逐字提示词无法从记录确认时会明确注明，不补写推测内容。

## Git

当前仓库位于 `main` 分支。交付审计没有重置或清理已有工作区修改，也没有自动提交或推送 Git。
