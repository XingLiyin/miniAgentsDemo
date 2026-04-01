# miniAgents 设计增量文档

**v1.2 delta** · 2026-03-30

> 本文档为增量内容，覆盖 v1.1 的以下章节：
> - **第 4 章**：Agent Loop — CreateTask 阶段描述更新（Skill L2 展开）
> - **第 9 章**：上下文拼装优先级 — C2 条目描述修正
> - **第 10 章**：Tool 与 Skill 系统（全文替换）
> - **第 13 章**：Schema — `tool_specs` 表字段补充

---

# 4. Agent Loop 与 Guard 机制（部分更新）

## 4.1 root-agent Loop 阶段（CreateTask 阶段更新）

> 仅更新 CreateTask 行，其余行不变。

| 阶段 | 核心动作 | 关键约束 |
|---|---|---|
| **CreateTask** | 创建 Task，设置 requires_approval / DAG 依赖 / output_topic；HITL Task 直接进入 PENDING_APPROVAL。**若 Task 类型为 `skill`，由 SkillRouter 在此阶段展开 L2 Instructions，将完整 skill 指令注入 `task.inputs["skill_instructions"]`，TaskExecutor 执行时不再访问文件系统** | spawn_agents 调用在 Execute 阶段，不在此处 |

---

# 9. Memory 管理（部分更新）

## 9.2 上下文拼装优先级（C2 条目更新）

> 仅更新 C2 行，其余行不变。

**第二段：消息上下文（每轮 Observe 阶段动态拼装）**

| # | 来源 | 条件 | 说明 |
|---|---|---|---|
| C2 | 匹配到的 Skill 元数据（L1）| Skill Router 有匹配结果时 | **Observe 阶段只注入 L1 Metadata（name + description）作为提示，告知 LLM 可用的 skill；L2 完整指令在 CreateTask 阶段展开并写入 task.inputs，不在此处加载** |

---

# 10. Tool 与 Skill 系统（全文替换）

## 10.1 Tool 系统

### 10.1.1 整体流程概览

Tool 系统分五个阶段：注册 → 感知 → 鉴权 → 执行 → 结果回流。

```
注册阶段（启动时）
  ToolRegistry.register_provider()
    ├─ BuiltinToolProvider → bash_exec, http_request, search_tools
    └─ MCPProvider（可选）→ 外部 MCP 工具

感知阶段（每轮 Plan）
  AgentLoop._plan()
    └─ tool_registry.to_llm_tools(agent.tool_list) → LLMTool[]（仅元数据，无 handler）

鉴权阶段（每次调用前）
  ToolGateway.call()
    └─ PolicyEngine.authorize()
         ├─ 第一层：registry.is_registered(tool_name)  → TOOL_NOT_FOUND
         └─ 第二层：tool_name in agent.tool_list        → TOOL_NOT_AUTHORIZED

执行阶段
  ToolGateway.call()
    └─ registry.get(tool_name).handler(arguments)

结果回流
  TaskExecutor → TaskManager.complete() → AgentLoop._update_memory()
```

### 10.1.2 注册阶段

应用启动时初始化 `ToolRegistry`（懒初始化，首次调用触发）：

**ToolDefinition 结构**：`name` + `description` + `InputSchema` + `handler`

内置工具（`BuiltinToolProvider`）在启动时自动注册，包括 `bash_exec`、`http_request`、`search_tools`。MCP 外部工具可选注册：

```
registry.register_mcp_stdio(name, command)
  └─ MCPStdioProvider.start()      ← 后台线程跑 asyncio loop
       └─ connect() + load_tools()
  └─ register_provider(provider)
       └─ _map_function_tool()     ← MCP FunctionTool → ToolDefinition
```

注册时同步到外部 Tool Store（可选，`TOOL_STORE_BASE_URL` 非空时生效）：

```
register_provider()
  └─ _sync_added(tool_defs)
       └─ ToolSyncService.on_tools_added()
            └─ ToolStoreClient.upsert()
                 └─ POST {TOOL_STORE_BASE_URL}/tools/upsert
                      ← 外部向量 DB 负责生成 embedding 并存储
```

### 10.1.3 感知阶段

LLM 通过 `to_llm_tools()` 感知当前 agent 有权使用的工具列表。LLMTool 只含元数据（name / description / inputSchema），不含 handler，不暴露执行逻辑。

LLM 返回的 Plan JSON 中包含 `type: "tool-call"` 的 task，指定 `tool_name` + `arguments`。

### 10.1.4 鉴权阶段（双层白名单）

```
PolicyEngine.authorize(agent, tool_name)
  ├─ 第一层：registry.is_registered(tool_name)
  │    └─ 工具必须存在于全局 Registry（系统级）→ 失败返回 TOOL_NOT_FOUND
  └─ 第二层：tool_name in agent.tool_list
       └─ 工具必须在该 Agent 的 tool_list 中（模板级授权）→ 失败返回 TOOL_NOT_AUTHORIZED
```

两层含义：第一层是系统级检查（工具是否存在），第二层是模板级授权（不同 Agent 可有不同权限）。

### 10.1.5 执行阶段

```
ToolGateway.call(tool_name, arguments, agent, task_id)
  ├─ PolicyEngine.authorize()           ← 双层白名单
  ├─ ToolCallStore.append(RUNNING)      ← 审计，arguments 脱敏（Authorization/cookie → ***）
  ├─ registry.get(tool_name).handler(arguments)
  │    ├─ bash_exec.handler:
  │    │    ├─ 黑名单正则检查（rm -rf / sudo / curl|bash...）→ TOOL_COMMAND_BLOCKED
  │    │    ├─ subprocess.run(shell=True, timeout=timeout_ms)
  │    │    └─ 超限截断（64KB）+ 返回 ToolResult
  │    ├─ http_request.handler:
  │    │    ├─ SSRF 防护（localhost / 127.x / 10.x / 192.168.x）→ SSRF_BLOCKED
  │    │    ├─ httpx.Client.request(...)
  │    │    └─ 超限截断（512KB）+ 返回 ToolResult
  │    └─ search_tools.handler:          ← 见 10.1.6
  └─ ToolCallStore.append(SUCCEEDED/FAILED)  ← result 截断至 200 字符
```

### 10.1.6 search_tools 内置工具

`search_tools` 是一个内置工具，**agent 在 Plan 阶段自行决定是否调用**，用于动态发现未在 `tool_list` 中预置的工具。

**inputSchema**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `query` | string | 是 | 自然语言描述，如"发送邮件"、"查询数据库" |
| `top_k` | integer | 否 | 返回条数，默认 5 |

**执行逻辑**

```
search_tools.handler(query, top_k)
  ├─ ToolStoreClient.enabled（TOOL_STORE_BASE_URL 非空）
  │    └─ POST {TOOL_STORE_BASE_URL}/tools/search
  │         └─ 外部向量 DB 返回 [{name, description, score}, ...]
  └─ ToolStoreClient.disabled（降级）
       └─ 在内存 Registry 中全量遍历，按 description 关键词匹配
            └─ 返回匹配结果（无语义排序）

返回格式（JSON 字符串，直接给 LLM）：
[
  {"name": "send_email", "description": "发送邮件...", "score": 0.92},
  ...
]
```

> **注意**：`search_tools` 只返回工具的元数据描述，不自动将工具加入 `agent.tool_list`。agent 发现感兴趣的工具后，由规划逻辑决定后续如何使用（通常是将工具名写入下一个 task 的 inputs，由上层重新鉴权）。

**search_tools 与 tool_list 预置的关系**

- `tool_list` 是静态白名单，在 AgentTemplate 导入时由 `tools_md` 提取，决定 agent 有权限调用哪些工具
- `search_tools` 是运行时发现机制，用于找到当前任务可能需要但未预置的工具，**不绕过 PolicyEngine 的鉴权**

### 10.1.7 MCP 工具生命周期管理

**刷新**（工具列表变更时）：

```
POST /api/v1/mcp-servers/{name}/refresh
  └─ ToolRegistry.refresh_mcp(name)
       ├─ provider.reload_tools()
       ├─ diff: added = new - old,  removed = old - new
       ├─ 更新 _tools + _provider_tool_names
       ├─ _sync_added(added_defs)   → POST /tools/upsert
       └─ _sync_removed(removed)   → POST /tools/delete
```

**注销**：

```
DELETE /api/v1/mcp-servers/{name}
  └─ ToolRegistry.shutdown_one(name)
       ├─ provider.stop()
       ├─ 从 _tools 删除
       └─ _sync_removed(tool_names) → POST /tools/delete
```

### 10.1.8 完整调用链

```
AgentLoop._plan()
  → LLM 生成 tool-call 任务
AgentLoop._create_tasks()
  → Task(type="tool-call", inputs={tool_name, arguments})
AgentLoop._execute()
  → TaskExecutor.execute(task)
    → TaskExecutor._run_tool_call()
      → ToolGateway.call()
        → PolicyEngine.authorize()       [双层白名单]
        → ToolCallStore.append(RUNNING)  [审计]
        → ToolRegistry.get().handler()   [实际执行]
        → ToolCallStore.append(DONE)     [审计]
        → return ToolResult
    → TaskManager.complete(result)
AgentLoop._update_memory()
  → MemoryService.append_message()
  → BlackboardService.publish()
```

---

## 10.2 Skill 系统

### 10.2.1 三层加载模型

Skill 系统采用三层按需加载，核心原则是：**只在需要时加载需要的内容，外部 DB 不可用时不影响功能**。

| 层 | 内容 | 加载时机 | 存储位置 |
|---|---|---|---|
| **L1 Metadata** | name, description, triggers, version | 应用启动 | 内存 + 外部 DB（可选）|
| **L2 Instructions** | SKILL.md 正文（完整指令）| CreateTask 阶段 | 内存（写入 task.inputs）|
| **L3 Resources** | skill 目录下的辅助文件 | Execute 阶段按需 | 文件系统 |

外部 DB 仅参与 L1 的持久化和语义召回。L2 展开和 L3 资源加载始终走本地文件系统，**外部 DB 不可用时整个系统降级为 L1 全量内存注入，功能不受影响**。

### 10.2.2 启动阶段（L1 加载）

```
get_skill_registry()
  → SkillRegistry.load_from_dir(settings.skills_dir)
      → SkillLoader.scan("data/skills/")
          → 遍历 data/skills/*/SKILL.md
          → 解析 YAML frontmatter
              → name, description, triggers, version, skill_dir
      → _sync_added(newly_registered)   ← 批量一次性推送（可选）
          → SkillSyncService.on_skills_added()
          → SkillStoreClient.upsert()
               ├─ SKILL_STORE_BASE_URL 非空 → POST {base_url}/skills/upsert
               └─ 为空 → no-op
```

启动后 `SkillRegistry._skills` 中有所有 skill 的 L1 元数据（常驻内存）。

**Skill 目录结构**：

```
data/skills/
  code-review/
    SKILL.md          ← L1 frontmatter + L2 正文指令
    scripts/          ← L3 辅助脚本（可选）
    assets/           ← L3 参考资料（可选）
    references/       ← L3 外部引用（可选）
```

**SKILL.md Frontmatter 字段**：

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | skill 唯一标识，如 `code-review` |
| `description` | 是 | 一句话描述，注入 Plan prompt 供 LLM 感知 |
| `triggers` | 否 | 触发关键词列表，用于隐式匹配 |
| `version` | 是 | 语义化版本号 |

### 10.2.3 Plan 阶段（L1 感知）

```
AgentLoop._plan(context, agent, session)
  │
  ├─ 1. 获取 skill 元数据块（注入给 LLM 的文本）
  │       if skill_registry && agent.skill_list:
  │           if SkillStoreClient.enabled:
  │               SkillStoreClient.search(context.goal, top_k=5)
  │               → POST {base_url}/skills/search
  │               → 返回 [{name, description, score}]
  │               ├─ 有结果 → 构建 top-K metadata block（语义相关的 skill）
  │               └─ 无结果 → 降级：SkillRegistry.get_metadata_block()（全量）
  │           else:
  │               SkillRegistry.get_metadata_block()（全量，V1 默认行为）
  │
  ├─ 2. 构建 plan system prompt
  │       _build_plan_prompt(has_tools, skill_metadata_block)
  │       → "Available Skills:
  │           - code-review: 审查代码质量...
  │           To use a skill, create a task with type 'skill'..."
  │
  └─ 3. 调用 LLM
         → LLM 输出包含 skill 任务的 plan：
             {
               "type": "skill",
               "title": "Review Code",
               "inputs": {
                 "skill_name": "code-review",
                 "context": "..."
               }
             }
```

Store 启用时，LLM 只看到与当前 goal 语义相关的 top-K 个 skill，而非全量列表，减少 token 消耗。

### 10.2.4 CreateTask 阶段（L2 展开）

```
AgentLoop._create_tasks(session_id, agent_id, plan, context)
  │
  └─ task_spec.type == "skill":
       skill_name = inputs["skill_name"]
       SkillRouter.load_for_task(skill_name)
           → SkillRegistry.load_definition(skill_name)
               → SkillLoader.load_instructions(skill_dir)
                   → 读取 SKILL.md frontmatter 之后的正文   ← L2 展开
               → 返回 SkillDefinition { metadata, instructions }
       SkillRouter.build_skill_prompt(skill_def, inputs)
           → 拼装：[context +] instructions 正文
       inputs["skill_instructions"] = assembled_prompt    ← 注入 task.inputs
       inputs.setdefault("messages", [goal + recent_history + task_description])
       TaskService.create(type="skill", inputs=inputs)
```

L2 展开后，`task.inputs["skill_instructions"]` 包含完整的 skill 指令，TaskExecutor 执行时不再访问文件系统。

### 10.2.5 Execute 阶段（L2 使用 + L3 按需加载）

```
TaskExecutor._run_skill(task, agent)
  → system_prompt = task.inputs["skill_instructions"]   ← 直接使用 L2
  → messages = task.inputs["messages"]
  → llm_client.send_message(messages, system_prompt)
  │
  │  执行期间若 LLM 按指令需要辅助文件：
  │  SkillRouter.load_resource(skill_name, relative_path)
  │      → SkillLoader.load_resource(skill_dir, relative_path)
  │          → 路径遍历安全检查（拒绝 ../）           ← L3 加载
  │          → 返回文件内容
  │
  → 返回 result + usage
```

### 10.2.6 注销路径

```
SkillRegistry.unregister(skill_name)
  → del _skills[skill_name]
  → _sync_removed([skill_name])
       → SkillSyncService.on_skills_removed([skill_name])
       → SkillStoreClient.delete([skill_name])
            → POST {base_url}/skills/delete
```

### 10.2.7 外部 DB 在整个流程中的角色

```
启动注册 ──────────────────────────────► 外部向量 DB
                                          ↑ embedding 生成（DB 自身负责）
                                          │
Plan 阶段 search(goal) ─────────────────► 语义检索（可选）
                                          │
                                          ▼ top-K skill metadata
                         注入 plan prompt → LLM 只感知相关 skill

降级路径（store_base_url 为空）：
  → L1 全量注入内存，功能不受影响
  → V1 默认走此路径，不部署外部 DB
```

---

## 10.3 policy_engine 授权

- 按 `(user_role, task_type, tool_name)` 三元组授权，V1 简化为白名单配置
- 全部调用写入 `tool_calls` 表，输入输出必须脱敏（api_key、密码等替换为 `***`）
- `tool_calls` 审计记录不可删除，保留 90 天
- `compensation` 字段记录有副作用工具的回滚动作

---

# 13. Schema 补充说明

## tool_specs 表（字段补充）

`tool_specs` 表是 ToolRegistry 的持久化镜像，在工具注册/注销时同步写入，供查询和审计使用。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR PK | tsp_ULID |
| `tool_name` | VARCHAR UNIQUE | 工具唯一标识 |
| `provider` | VARCHAR | 提供者名称（builtin / mcp 服务名称）|
| `input_schema` | JSONB | JSON Schema，来自 ToolDefinition.InputSchema |
| `annotations` | JSONB | 可选注解，如 `readOnlyHint`（只读工具标记）|
| `is_active` | BOOLEAN DEFAULT true | 工具是否当前可用（注销时置 false，不删除行）|
| `updated_at` | TIMESTAMPTZ | 最后同步时间 |

## 新增配置项（环境变量）

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `TOOL_STORE_BASE_URL` | 空 | Tool Store 外部向量 DB 地址；空则禁用语义搜索，search_tools 降级为关键词匹配 |
| `SKILL_STORE_BASE_URL` | 空 | Skill Store 外部向量 DB 地址；空则 Plan 阶段使用 L1 全量内存注入（V1 默认）|
| `SKILLS_DIR` | `data/skills/` | skill 目录路径 |

---

*miniAgents Design Document v1.2 delta · 2026-03-30*
