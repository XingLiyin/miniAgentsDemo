# Skill 内置工具参考

以下三个工具专用于 Skill 任务上下文，仅在任务已绑定某个 skill（`task.settings.skill_name`）时可用。

---

## `get_skill_files`

列出当前任务 skill 目录下的文件。

### 输入参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `pattern` | string | 否 | `**/*` | Glob 匹配模式（仅本地 skill 有效），例如 `scripts/*.py`、`references/**` |
| `limit` | integer | 否 | `200` | 返回结果数上限（仅本地 skill 有效） |

### 返回值

- 换行分隔的文件路径列表（本地 skill）或文件名列表（远端 skill）。
- 远端 skill 同时返回可传入 `load_skill_reference` / `exec_skill_script` 的文件 ID。
- 超出 `limit` 时末尾附加 `[truncated at N results]`。
- 无匹配时返回 `(no files)`。

### 行为说明

- **本地 skill**：在 `skill_dir` 下执行 glob，自动过滤目录、隐藏路径（`.` 开头的路径段）。
- **远端 skill**：通过私有 MCP 连接调用 `getSkillFiles`。
- 若任务未绑定 skill，抛出 `MISSING_SKILL_NAME` 错误。

---

## `load_skill_reference`

读取当前任务 skill 目录中的参考文件内容。

### 输入参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `reference_path` | string | **是** | — | 相对于 skill 目录的文件路径，例如 `references/background.md`、`checks/self_check.md` |

### 返回值

文件的文本内容（字符串）。

### 行为说明

- **本地 skill**：通过 `SkillLoader.load_resource` 读取本地文件，路径必须位于 `skill_dir` 内。
- **远端 skill**：通过私有 MCP 连接拉取资源。
- 常见错误码：
  - `INVALID_ARGUMENT`：`reference_path` 为空或路径非法。
  - `FILE_NOT_FOUND`：文件不存在。
  - `MISSING_SKILL_NAME`：任务未绑定 skill。
  - `REMOTE_REFERENCE_ERROR`：远端读取失败。

---

## `exec_skill_script`

执行当前任务 skill 目录 `scripts/` 下的脚本。

### 输入参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `script_name` | string | **是** | — | 脚本文件名，**不含扩展名**，例如 `extract_olt_config` |
| `args` | string | 否 | `""` | 拼接在脚本路径之后的命令行参数字符串 |

### 返回值

脚本的 stdout + stderr 合并输出（字符串）。输出超过 `bash_exec_output_limit_bytes` 时末尾附加截断提示。`is_error=true` 表示脚本退出码非零。

### 行为说明

- **脚本查找顺序**：依次尝试 `scripts/<name>.py` → `scripts/<name>.sh` → `scripts/<name>`（无扩展名），取第一个存在的文件。
- **Python 脚本**：若 skill 目录存在 `requirements.txt`，自动在 `skill_dir/.venv` 创建虚拟环境并安装依赖，然后用 venv 解释器执行；否则使用系统 Python。
- **工作目录**：继承任务的 `cwd`，若未设置则为进程当前目录。
- **超时**：受 `bash_exec_timeout_ms`（全局配置）控制，超时抛出 `TOOL_TIMEOUT`。
- **安全检查**：脚本路径不得逃出 `skill_dir`；命令内容通过黑名单（`_BASH_BLACKLIST`）过滤，违规抛出 `TOOL_COMMAND_BLOCKED`。
- **远端 skill**：通过私有 MCP 连接调用 `execScript`，忽略本地路径逻辑。
- 常见错误码：
  - `INVALID_ARGUMENT`：`script_name` 为空或路径逃出 skill 目录。
  - `SCRIPT_NOT_FOUND`：在 `scripts/` 下未找到匹配脚本。
  - `SCRIPT_NONZERO_EXIT`：脚本退出码非零（`is_error=true`，不抛异常）。
  - `SCRIPT_LAUNCH_ERROR`：启动进程失败。
  - `VENV_CREATE_FAILED` / `VENV_INSTALL_FAILED`：虚拟环境创建或依赖安装失败。
  - `TOOL_TIMEOUT`：执行超时。
  - `MISSING_SKILL_NAME`：任务未绑定 skill。
