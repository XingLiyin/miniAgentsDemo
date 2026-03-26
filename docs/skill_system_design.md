# Skill System 设计文档

## 1. 概述

### 1.1 背景

本文档描述 miniAgents 框架中 Skill 机制的设计与实现方案。
参考来源：Anthropic 官方 Agent Skills 规范（platform.claude.com/docs/en/agents-and-tools/agent-skills/overview）。

### 1.2 Skill 是什么

Skill 是**按需加载的模块化能力包**。

与现有机制的区别：

| 概念    | 触发方式          | 执行主体        | 内容形式              |
|---------|-------------------|-----------------|-----------------------|
| Tool    | LLM function call | ToolGateway     | 代码 handler          |
| Skill   | LLM 计划阶段识别  | SkillRouter     | Markdown 文件 + 资源  |

Skill 不是代码——它是给 LLM 的**结构化知识和操作指导**，以文件形式存储，
当 LLM 判断当前任务与某个 Skill 相关时，框架按需将该 Skill 的内容加载进 context。

### 1.3 核心价值

- **专家化**：将领域知识封装成可复用模块，不需要在每次 session 中重复提供
- **按需加载**：避免把所有知识一次性塞入 context，降低 token 消耗
- **可插拔**：新增 Skill 只需在 `data/skills/` 下创建目录，框架自动发现

---

## 2. Skill 文件格式

每个 Skill 是 `data/skills/{skill-name}/` 下的一个目录，必须包含 `SKILL.md`。

### 2.1 目录结构

```
data/skills/
├── code-review/
│   ├── SKILL.md          ← 必须，Level 1 + Level 2
│   ├── checklist.md      ← 可选，Level 3 资源
│   └── examples/
│       └── bad_code.py   ← 可选，Level 3 资源
├── data-analysis/
│   └── SKILL.md
└── web-research/
    ├── SKILL.md
    └── search_template.md
```

### 2.2 SKILL.md 格式

```markdown
---
name: code-review
description: >
  审查代码质量，检查安全漏洞、性能问题和最佳实践违规。
  当任务涉及代码审查、PR review、代码质量评估时触发。
triggers:
  - code review
  - PR review
  - 代码质量
version: "1.0"
---

## Instructions

[具体的操作步骤和指导，Level 2，< 5k tokens]

### 步骤一：...
### 步骤二：...

## Resources

[Level 3 资源的引用，按需加载]

- checklist.md — 完整审查清单
- examples/   — 典型问题示例
```

### 2.3 YAML Frontmatter 字段

| 字段          | 必须  | 说明                                           |
|---------------|-------|------------------------------------------------|
| `name`        | ✓     | 唯一标识符，与目录名一致                       |
| `description` | ✓     | 供 LLM 判断是否触发的关键字段，应精准描述场景  |
| `triggers`    |       | 关键词列表，辅助匹配                           |
| `version`     |       | 版本号，用于追踪变更                           |

---

## 3. 三层加载模型（Progressive Disclosure）

```
┌─────────────────────────────────────────────────────────────┐
│  Level 1 — Metadata                                         │
│  内容：YAML frontmatter（name + description）               │
│  加载时机：Plan 阶段开始前，始终注入                        │
│  Token 开销：~100 tokens / skill                            │
│  用途：让 LLM 感知所有可用 Skill，决定是否触发              │
├─────────────────────────────────────────────────────────────│
│  Level 2 — Instructions                                     │
│  内容：SKILL.md 主体（## Instructions 及以下）              │
│  加载时机：LLM 计划中出现该 Skill 的任务时                  │
│  Token 开销：< 5k tokens / skill                            │
│  用途：提供具体操作步骤，注入执行阶段的 system prompt       │
├─────────────────────────────────────────────────────────────│
│  Level 3 — Resources                                        │
│  内容：skill 目录下的其他文件                               │
│  加载时机：执行阶段 LLM 显式引用资源文件时                  │
│  Token 开销：不预加载，按需读取，无上限                     │
│  用途：大型参考资料、模板、示例文件                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. 框架集成设计

### 4.1 数据流

```
启动时
  SkillLoader.scan() → 读取所有 SKILL.md frontmatter → SkillRegistry（Level 1 缓存）

Plan 阶段（AgentLoop._plan）
  SkillRegistry.get_metadata_block() → 拼入 plan prompt
  LLM 返回 plan JSON，可包含 type="skill" 的任务

CreateTask 阶段（AgentLoop._create_tasks）
  识别 type="skill" → SkillRouter.load_level2(skill_name) → 注入 task.inputs

Execute 阶段（TaskExecutor）
  执行 skill task 时，LLM 可请求 Level 3 资源
  SkillRouter.load_resource(skill_name, resource_path) → 按需读取文件
```

### 4.2 Plan JSON 中的 Skill 任务格式

LLM 在 Plan 阶段可输出以下格式触发 Skill：

```json
{
  "type": "skill",
  "title": "审查 PR #42",
  "description": "对提交的代码进行安全和质量审查",
  "inputs": {
    "skill_name": "code-review",
    "context": "..."
  }
}
```

### 4.3 注入 Plan Prompt 的 Skill Metadata 格式

```
Available Skills:
- code-review: 审查代码质量，检查安全漏洞、性能问题和最佳实践违规。
  当任务涉及代码审查、PR review、代码质量评估时触发。
- data-analysis: ...
- web-research: ...

To use a skill, create a task with "type": "skill" and "skill_name": "<name>".
```

---

## 5. 新增组件

### 5.1 SkillLoader（`app/skills/loader.py`）

```
职责：文件系统扫描与加载
- scan(skills_dir) → 发现所有 SKILL.md
- load_metadata(path) → 解析 frontmatter，返回 SkillMetadata（Level 1）
- load_instructions(path) → 读取 SKILL.md 主体（Level 2）
- load_resource(skill_dir, resource_path) → 读取资源文件（Level 3）
```

### 5.2 SkillDefinition（`app/skills/definition.py`）

```python
@dataclass
class SkillMetadata:       # Level 1，常驻内存
    name: str
    description: str
    triggers: list[str]
    version: str
    skill_dir: Path

@dataclass
class SkillDefinition:     # Level 2，按需加载
    metadata: SkillMetadata
    instructions: str      # SKILL.md 主体文本
```

### 5.3 SkillRegistry（`app/skills/registry.py`）

```
职责：内存索引 + Level 1 缓存
- register(SkillMetadata)
- get_metadata(name) → SkillMetadata
- list_all() → [SkillMetadata]
- get_metadata_block() → str（注入 prompt 的格式化文本）
```

### 5.4 SkillRouter（`app/runtime/skill_router.py`）

```
职责：识别 skill task，加载 Level 2/3，封装执行上下文
- load_for_task(task) → SkillDefinition（触发 Level 2 加载）
- build_skill_prompt(skill_def, task_inputs) → str（拼入 task system prompt）
- load_resource(skill_name, resource_path) → str（Level 3 按需加载）
```

---

## 6. 现有组件修改

| 组件                         | 修改内容                                                    |
|------------------------------|-------------------------------------------------------------|
| `AgentLoop._plan()`          | 将 `SkillRegistry.get_metadata_block()` 注入 plan prompt    |
| `AgentLoop._plan_prompt`     | 添加 skill task 格式说明（type="skill"）                    |
| `AgentLoop._create_tasks()`  | 识别 type="skill"，调用 `SkillRouter.load_for_task()`       |
| `TaskExecutor`               | 新增 skill task 执行分支，使用 skill 专用 system prompt     |
| `app/config/settings.py`     | 新增 `skills_dir: Path = Path("data/skills")`               |
| `app/api/v1/deps.py`         | 注入 `SkillRegistry` 和 `SkillRouter`                       |

---

## 7. 与现有 `app/skills/` 目录的关系

当前 `app/skills/` 目录（computer use 风格实现）需要**完整替换**为本方案：

```
app/skills/
├── __init__.py
├── definition.py    ← SkillMetadata, SkillDefinition
├── loader.py        ← SkillLoader（文件系统读写）
└── registry.py      ← SkillRegistry（内存索引）

app/runtime/
└── skill_router.py  ← SkillRouter（运行时调度）
```

`LLMRequest.skills` 字段和 `LLMSkill` 类型也应从 `llm_base.py` 中移除，
因为 Skill 的加载和执行完全在框架层处理，不需要传给 LLM API。

---

## 8. 示例：内置 Skill `code-review`

```
data/skills/code-review/
├── SKILL.md
└── security_checklist.md
```

**SKILL.md**
```markdown
---
name: code-review
description: >
  审查代码质量、安全性和最佳实践。
  当任务包含 code review、PR review、代码审查、安全审计等关键词时触发。
triggers:
  - code review
  - PR review
  - 代码审查
  - 安全审计
version: "1.0"
---

## Instructions

### 审查步骤

1. **安全性检查**：SQL 注入、XSS、命令注入、敏感信息泄露
2. **逻辑正确性**：边界条件、错误处理、空值处理
3. **性能**：N+1 查询、不必要的循环、内存泄漏
4. **可维护性**：命名规范、函数长度、重复代码

### 输出格式

按严重程度分级：CRITICAL / HIGH / MEDIUM / LOW / INFO

## Resources

- security_checklist.md — 详细安全审查清单（按需加载）
```

---

## 9. 实现顺序

1. 清理当前 `app/skills/` 和 `llm_base.py` 中的 computer use 实现
2. 实现 `SkillMetadata` / `SkillDefinition` 数据模型
3. 实现 `SkillLoader`（文件系统扫描 + frontmatter 解析）
4. 实现 `SkillRegistry`（内存索引 + metadata block 生成）
5. 修改 `AgentLoop._plan()` 注入 skill metadata
6. 修改 `AgentLoop._create_tasks()` 识别 skill 任务类型
7. 实现 `SkillRouter`（Level 2/3 加载 + prompt 封装）
8. 修改 `TaskExecutor` 支持 skill task 执行分支
9. 创建示例 Skill（`data/skills/code-review/`）
