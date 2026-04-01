# 开发方案：3A Agent 定义文件系统

## 1. 目标与背景

### 目标

实现设计文档 §3A 所描述的 **Agent 定义文件系统**：用户以本地 Markdown 文件（SOUL.md / ROLE.md / TOOLS.md / STYLE.md）维护 agent 身份，系统启动时从指定目录扫描并自动加载为 `AgentTemplate`，替代当前手动通过 API 传入 `system_prompt` 的方式。

加载机制完全参照 **Skill 机制**：`settings.agents_dir` → `AgentLoader.scan()` → `AgentTemplateRegistry.load_from_dir()` → upsert 到 `AgentTemplateStore`。

### 现状（需要变更的部分）

| 现状 | 目标 |
|------|------|
| `AgentTemplate.system_prompt: str` — 一个字段 | 拆分为 `soul_md / role_md / tools_md / style_md` 四个字段 |
| `POST /agent-templates` 传入 JSON `system_prompt` 创建 | 保留该接口作兼容路径；主路径改为目录扫描 |
| `tool_list` 由调用方手动传入 | 优先从 TOOLS.md frontmatter `tools` 字段读取；缺省时懒加载 |
| `AgentLoop._observe()` 直接用 `agent.system_prompt` | 改为按规则拼装四个字段 |

---

## 2. 目录结构约定

参照 `data/skills/<skill-name>/SKILL.md`，agent 定义存放于：

```
data/agents/
├── research-agent/
│   ├── SOUL.md       ← 必填
│   ├── ROLE.md       ← 必填
│   ├── TOOLS.md      ← 可选
│   └── STYLE.md      ← 可选
└── code-reviewer/
    ├── SOUL.md
    ├── ROLE.md
    └── TOOLS.md
```

`settings.agents_dir` 默认值 `data/agents`，可通过环境变量 `MINIAGENTS_AGENTS_DIR` 覆盖。

---

## 3. 文件格式规范

每个文件使用 YAML Frontmatter + Markdown 正文（与 SKILL.md 相同格式，复用 `_parse_simple_yaml` 解析工具）。

**SOUL.md**（必填，frontmatter 承载模板元数据）
```yaml
---
name: research-agent          # 与 AgentTemplate.name 对应，upsert 匹配键
version: 1.2.0
description: 专注信息收集与分析的研究型 agent
---

## 核心性格
你是一个追求信息准确性的研究者...
```

**ROLE.md**（必填，frontmatter 可留空）
```yaml
---
---

## 职责范围
负责从公开数据源收集信息并归纳摘要...
```

**TOOLS.md**（可选，frontmatter `tools` 字段提供快速路径）
```yaml
---
tools:
  - http_request
  - bash_exec
---

## 工具使用指南
### http_request
用于抓取网页内容...
```

**STYLE.md**（可选，frontmatter 可留空）
```yaml
---
---

输出使用中文，结论先行，引用来源...
```

---

## 4. 加载模块（类比 Skill 机制）

### 4.1 数据模型

**新文件**：`app/agent_def/definition.py`

```python
@dataclass
class AgentDefMetadata:
    """Level 1 — 常驻内存的元数据（从 SOUL.md frontmatter 解析）。"""
    name: str
    version: str
    description: str
    tool_list: list[str]      # 来自 TOOLS.md frontmatter.tools（可能为空）
    tool_list_ready: bool     # True = tool_list 已从 frontmatter 提取
    agent_dir: Path           # 四个文件所在目录

@dataclass
class AgentDefContent:
    """Level 2 — 四个文件的正文内容（按需加载，用于拼装 system prompt）。"""
    metadata: AgentDefMetadata
    soul_md: str
    role_md: str
    tools_md: str
    style_md: str
```

### 4.2 AgentLoader

**新文件**：`app/agent_def/loader.py`（类比 `app/skills/loader.py`）

```python
class AgentLoader:
    def scan(self, agents_dir: Path) -> list[AgentDefMetadata]:
        """扫描 agents_dir，每个子目录至少有 SOUL.md + ROLE.md 才加载。"""

    def load_metadata(self, agent_dir: Path) -> AgentDefMetadata:
        """解析 SOUL.md frontmatter + TOOLS.md frontmatter.tools → Level 1。"""

    def load_content(self, agent_dir: Path) -> AgentDefContent:
        """读取四个文件正文 → Level 2（按需调用）。"""
```

**scan 逻辑**（与 `SkillLoader.scan` 完全类似）：
```
遍历 agents_dir 子目录
  ├── 跳过：SOUL.md 不存在 or ROLE.md 不存在
  ├── 调用 load_metadata(agent_dir)
  │     ├── 解析 SOUL.md frontmatter → name, version, description
  │     └── 解析 TOOLS.md frontmatter（若存在）→ tools → tool_list, tool_list_ready=True
  │         若 TOOLS.md 不存在或无 tools 字段 → tool_list=[], tool_list_ready=False
  └── 返回 AgentDefMetadata
```

frontmatter 解析直接复用 `app/skills/loader.py` 中的 `_parse_simple_yaml`（提取为共享工具函数或直接引用）。

### 4.3 AgentTemplateRegistry

**新文件**：`app/agent_def/registry.py`（类比 `app/skills/registry.py`）

```python
class AgentTemplateRegistry:
    """内存索引 + 启动时批量 upsert 到 AgentTemplateStore。"""

    def load_from_dir(self, agents_dir: Path) -> None:
        """扫描目录，批量 upsert AgentTemplate（类比 SkillRegistry.load_from_dir）。"""

    def get_metadata(self, name: str) -> AgentDefMetadata | None: ...
    def list_all(self) -> list[AgentDefMetadata]: ...
    def load_content(self, name: str) -> AgentDefContent | None:
        """Level 2：读取四个文件正文（类比 SkillRegistry.load_definition）。"""
```

`load_from_dir` 内部流程：
```
AgentLoader.scan(agents_dir)
  → 写入内存 _agents dict
  → 对每个 metadata，调用 AgentTemplateService.upsert_by_name():
      若 name 存在 → 更新 soul_md/role_md/tools_md/style_md/version/tool_list
      若不存在   → 新建 AgentTemplate，分配 template_id
```

---

## 5. 数据模型变更

### 5.1 AgentTemplate 模型

**文件**：`app/domain/models/agent_template.py`

新增字段，保留 `system_prompt` 作兼容过渡字段（旧接口兼容，实例化时优先用四个 md 字段）：

```python
@dataclass
class AgentTemplate:
    id: str
    name: str
    version: str = "1.0.0"           # 新增

    # 四个内容字段（frontmatter 之后的 Markdown 正文）
    soul_md: str = ""                 # 新增
    role_md: str = ""                 # 新增
    tools_md: str = ""                # 新增
    style_md: str = ""                # 新增

    # 兼容字段（旧接口写入，实例化时四个 md 字段优先）
    system_prompt: str = ""

    tool_list: list[str] = field(default_factory=list)
    tool_list_ready: bool = False     # 新增
    skill_list: list[str] = field(default_factory=list)

    inject_style: bool = False        # 新增：是否注入 STYLE.md，默认关闭
    has_spawn_permission: bool = False

    summary_threshold: int = 20
    short_window_size: int = 20
    created_at: str = ""
    updated_at: str = ""
```

### 5.2 Settings

**文件**：`app/config/settings.py`

新增一行：

```python
agents_dir: Path = Path("data/agents")
```

对应环境变量 `MINIAGENTS_AGENTS_DIR`。

---

## 6. AgentTemplateService 扩展

**文件**：`app/domain/services/agent_template_service.py`

新增两个方法：

```python
def upsert_by_name(
    self,
    name: str,
    version: str,
    description: str,
    soul_md: str,
    role_md: str,
    tools_md: str,
    style_md: str,
    tool_list: list[str],
    tool_list_ready: bool,
) -> AgentTemplate:
    """按 name 做 upsert（扫描时调用）。"""

def get_or_prepare(self, template_id: str, llm_client) -> AgentTemplate:
    """若 tool_list_ready=False，调用 LLM 从 tools_md 提取工具名并写回。"""
```

---

## 7. tool_list 懒加载

当 TOOLS.md frontmatter 中没有 `tools` 字段时（`tool_list_ready=False`），在 **agent 首次实例化时**由 LLM 提取（类比 Skill 的 L2 按需加载）：

```
SessionService.create_agent() 调用 AgentTemplateService.get_or_prepare()
  ├── tool_list_ready=True → 直接返回
  └── tool_list_ready=False
        ↓
      LLM 读取 tools_md 正文，提取工具名列表
        ↓
      写入 template.tool_list, tool_list_ready=True，保存
        ↓
      后续同模板的 agent 实例不再重复提取
```

提取 prompt：
```
Read the tool usage guide below and extract all tool names.
Return a JSON array of strings only. Example: ["http_request", "bash_exec"]

Tool guide:
{tools_md}
```

---

## 8. system prompt 组装

**修改**：`app/runtime/agent_loop.py`

在 agent 实例化时（`SessionService` 创建 agent 时），调用 `_build_system_prompt()` 将四个字段拼装结果写入 `agent.system_prompt`，后续 AgentLoop 逻辑不变。

```python
def _build_system_prompt(template: AgentTemplate, agent: Agent) -> str:
    parts: list[str] = []

    if template.soul_md:              # S1: 始终注入
        parts.append(template.soul_md)
    if template.role_md:              # S2: 始终注入
        parts.append(template.role_md)
    if template.tools_md and agent.tool_list:   # S3: tool_list 非空时注入
        parts.append(template.tools_md)
    if template.style_md and template.inject_style:  # S4: 显式开启时注入
        parts.append(template.style_md)

    # 兼容路径：四个字段全为空时退回 system_prompt 字段
    if not parts and template.system_prompt:
        return template.system_prompt

    return "\n\n---\n\n".join(parts)
```

> Skill 内容**不在此处**注入，仍由 Plan 阶段 `SkillStoreClient` / `SkillRegistry` 处理。

---

## 9. 启动集成

**文件**：`app/api/v1/deps.py`（或应用启动入口）

类比 `get_skill_registry()` 的全局单例模式：

```python
# 全局单例（类比 get_skill_registry）
_agent_template_registry: AgentTemplateRegistry | None = None

def get_agent_template_registry() -> AgentTemplateRegistry:
    global _agent_template_registry
    if _agent_template_registry is None:
        _agent_template_registry = AgentTemplateRegistry(
            template_service=get_agent_template_service()
        )
        _agent_template_registry.load_from_dir(get_settings().agents_dir)
    return _agent_template_registry
```

应用启动时调用一次 `get_agent_template_registry()` 完成扫描和 upsert。

---

## 10. API 变更（最小化）

原 `POST /agent-templates` JSON 接口**保持不变**（`system_prompt` 兼容路径），**移除**之前方案中的 multipart 导入端点。

`AgentTemplateResponse` 新增字段：`version`, `tool_list_ready`, `inject_style`（不返回四个 md 字段内容，避免响应体过大）。

---

## 11. 受影响文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `app/agent_def/definition.py` | 新增 | `AgentDefMetadata` / `AgentDefContent` 数据类 |
| `app/agent_def/loader.py` | 新增 | 目录扫描 + 文件解析（类比 `skills/loader.py`） |
| `app/agent_def/registry.py` | 新增 | 内存索引 + 启动 upsert（类比 `skills/registry.py`） |
| `app/config/settings.py` | 修改 | 新增 `agents_dir` 字段 |
| `app/domain/models/agent_template.py` | 修改 | 新增七个字段 |
| `app/domain/services/agent_template_service.py` | 修改 | 新增 `upsert_by_name()` / `get_or_prepare()` |
| `app/api/v1/deps.py` | 修改 | 新增 `get_agent_template_registry()` 单例 |
| `app/api/v1/schemas/agent_template.py` | 修改 | 新增 response 字段 |
| `app/runtime/agent_loop.py` | 修改 | `_build_system_prompt()` 拼装逻辑 |
| `tests/test_agent_def_loader.py` | 新增 | 目录扫描 + 解析单测 |
| `tests/test_agent_def_registry.py` | 新增 | upsert 流程单测 |

---

## 12. 实施顺序

```
Step 1  AgentDefMetadata / AgentDefContent 数据类
        — 无依赖，最先定义

Step 2  AgentLoader（扫描 + 解析）
        — 复用 _parse_simple_yaml，纯文件系统操作，最易测试

Step 3  AgentTemplate 模型扩展（新增七个字段）
        — to_dict/from_dict 向后兼容（旧记录缺失字段取默认值）

Step 4  AgentTemplateService.upsert_by_name()
        — 依赖 Step 3

Step 5  AgentTemplateRegistry（内存索引 + load_from_dir）
        — 依赖 Step 2、4

Step 6  Settings 新增 agents_dir + deps 全局单例
        — 依赖 Step 5，完成启动集成

Step 7  AgentTemplateService.get_or_prepare() + ToolListExtractor
        — 依赖 Step 3，需要 LLMClient 注入

Step 8  AgentLoop._build_system_prompt()
        — 依赖 Step 3、7

Step 9  单测补全
```

---

## 13. 关键设计决策

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 加载方式 | 目录扫描（类比 skill）而非 API 上传 | 用户用本地编辑器维护文件，启动时自动同步，无需额外操作 |
| 每个 agent 独立子目录 | `data/agents/<name>/` | 与 `data/skills/<name>/` 完全一致，结构统一 |
| tool_list 快速路径 | TOOLS.md frontmatter `tools` 字段直接写入 | 用户已声明时无需 LLM，与 skill 的 frontmatter 元数据模式一致 |
| 懒加载触发时机 | agent 首次实例化时 | 扫描时不调 LLM，同模板只提取一次 |
| 四个 md 字段 vs 保留 system_prompt | 新增字段 + 兼容 fallback | 存量数据不破坏；实例化时四个字段优先 |
| style 注入默认关闭 | `inject_style=False` | 避免无关 agent 多余 token 消耗 |
