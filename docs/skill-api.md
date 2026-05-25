# Skill API

Base path: `/api`

---

## 数据结构

### SkillResponse

| 字段 | 类型 | 说明 |
|------|------|------|
| id | String | Skill ID |
| name | String | Skill 名称 |
| description | String | Skill 描述 |
| domain | String | 专业域（如 `IP`、`CLOUD_CORE`），可为 null |
| createTime | String | 创建时间，ISO 8601 格式，如 `2024-01-15T10:30:00` |

### SkillFileResponse

| 字段 | 类型 | 说明 |
|------|------|------|
| id | String | 文件 ID |
| skillId | String | 所属 Skill ID |
| name | String | 文件路径名（含目录，如 `scripts/run.sh`） |
| createTime | LocalDateTime | 创建时间 |

### ProjectSkillResponse

| 字段 | 类型 | 说明 |
|------|------|------|
| id | String | 项目 Skill ID |
| name | String | Skill 名称 |
| description | String | Skill 描述 |
| domain | String | 专业域，可为 null |

### ProjectSkillFileResponse

| 字段 | 类型 | 说明 |
|------|------|------|
| id | String | 文件 ID |
| projectSkillId | String | 所属项目 Skill ID |
| name | String | 文件路径名 |
| createTime | LocalDateTime | 创建时间 |

### CreateSkillRequest

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| name | String | 是 | Skill 名称 |
| description | String | 是 | Skill 描述 |
| domain | String | 否 | 专业域 |

### UpdateSkillRequest

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| name | String | 是 | Skill 名称 |
| description | String | 否 | Skill 描述 |
| domain | String | 否 | 专业域 |

### CreateSkillFileRequest

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| name | String | 是 | 文件路径名（含目录，如 `scripts/run.sh`） |
| content | String | 否 | 文件初始文本内容 |

---

## 知识库 Skill

### 获取 Skill 列表

`GET /skills`

**Response** `200 List<SkillResponse>`

---

### 创建 Skill

`POST /skills`

**Request Body** `CreateSkillRequest`

**Response** `200 SkillResponse`

---

### 导入 Skill（zip）

`POST /skills/import`  `multipart/form-data`

| 参数 | 类型 | 说明 |
|------|------|------|
| file | MultipartFile | skill zip 包 |

**Response** `200 SkillResponse`

---

### 更新 Skill

`PUT /skills/{skillId}`

**Request Body** `UpdateSkillRequest`

**Response** `200 SkillResponse`

---

### 删除 Skill

`DELETE /skills/{skillId}`

**Response** `204 No Content`

---

### 导出 Skill（zip）

`GET /skills/{skillId}/export`

**Response** `200 application/zip`，`Content-Disposition: attachment; filename="skill.zip"`

---

## 知识库 Skill 文件

### 获取 Skill 文件列表

`GET /skills/{skillId}/files`

**Response** `200 List<SkillFileResponse>`

---

### 创建 Skill 文件

`POST /skills/{skillId}/files`

**Request Body** `CreateSkillFileRequest`

**Response** `200 SkillFileResponse`

---

### 上传 Skill 文件

`POST /skills/{skillId}/files/upload`  `multipart/form-data`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| file | MultipartFile | 是 | 文件内容 |
| targetDir | String | 否 | 目标目录，默认为根目录 |

**Response** `200 SkillFileResponse`

---

### 获取文件原始内容

`GET /skill-files/{fileId}/content`

> 直接返回文件原始字节，以 UTF-8 解码，不做格式转换。

**Response** `200 text/plain`

---

### 获取文件文本内容

`GET /skill-files/{fileId}/text`

> 对于 PDF、Office 等格式会提取纯文本；普通文本文件与 `/content` 返回相同。

**Response** `200 text/plain`

---

### 更新文件内容

`PUT /skill-files/{fileId}/content`  `Content-Type: text/plain`

> 若文件名为 `SKILL.md`，会同步解析 front matter 并更新 Skill 的 name / description。

**Request Body** 文件文本内容（纯字符串）

**Response** `204 No Content`

---

### 删除 Skill 文件

`DELETE /skill-files/{fileId}`

**Response** `204 No Content`

---

## 项目 Skill

### 获取项目的 Skill 列表

`GET /projects/{projectId}/skills`

**Response** `200 List<ProjectSkillResponse>`

---

### 获取 Session 的 Skill 列表

`GET /sessions/{sessionId}/skills`

**Response** `200 List<ProjectSkillResponse>`

---

### 获取单个项目 Skill

`GET /project-skills/{projectSkillId}`

**Response** `200 ProjectSkillResponse`

---

### 获取项目 Skill 文件列表

`GET /project-skills/{projectSkillId}/files`

**Response** `200 List<ProjectSkillFileResponse>`

---

### 获取项目 Skill 文件原始内容

`GET /project-skill-files/{fileId}/content`

> 直接返回文件原始字节，以 UTF-8 解码，不做格式转换。

**Response** `200 text/plain`

---

### 获取项目 Skill 文件文本内容

`GET /project-skill-files/{fileId}/text`

> 对于 PDF、Office 等格式会提取纯文本；普通文本文件与 `/content` 返回相同。

**Response** `200 text/plain`

---

### 更新项目 Skill 文件内容

`PUT /project-skill-files/{fileId}/content`  `Content-Type: text/plain`

**Request Body** 文件文本内容（纯字符串）

**Response** `204 No Content`
