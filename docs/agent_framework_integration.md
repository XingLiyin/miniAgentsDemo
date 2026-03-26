# Microsoft Agent Framework 集成分析

> 分析 [microsoft/agent-framework](https://github.com/microsoft/agent-framework) 可用组件，评估替换 miniAgents 现有模块的方案。

---

## Agent Framework 组件概览

**安装方式：** `pip install agent-framework-core --pre`（各 provider 单独安装）
**语言：** Python 3.10+ / .NET C#
**License：** MIT

### 核心包结构（Python）

| 模块 | 组件 | 说明 |
|---|---|---|
| `_agents.py` | `Agent`, `RawAgent`, `BaseAgent`, `SupportsAgentRun` | Agent 抽象，支持 middleware + telemetry |
| `_clients.py` | `BaseChatClient`, `BaseEmbeddingClient` | LLM 客户端基类，统一 `get_response()` API |
| `_types.py` | `Message`, `Content`, `ChatResponse`, `UsageDetails` | 统一消息类型系统 |
| `_tools.py` | `FunctionTool`, `@tool`, `FunctionInvocationLayer` | 自动 schema 生成，Pydantic 校验 |
| `_sessions.py` | `AgentSession`, `SessionContext`, `BaseHistoryProvider`, `InMemoryHistoryProvider` | 会话管理 + 历史记录 |
| `_middleware.py` | `AgentMiddleware`, `FunctionMiddleware`, `ChatMiddleware` | 三层中间件拦截器 |
| `_mcp.py` | `MCPStdioTool`, `MCPStreamableHTTPTool`, `MCPWebsocketTool` | MCP 协议工具服务器集成 |
| `_skills.py` | `Skill`, `SkillResource`, `SkillScript`, `SkillsProvider` | 三阶段渐进式能力加载 |
| `_compaction.py` | `TruncationStrategy`, `SummarizationStrategy`, `TokenBudgetComposedStrategy` | 长上下文压缩策略 |
| `_workflows/` | `WorkflowBuilder`, `Executor`, `@handler`, `WorkflowCheckpoint` | Pregel 图工作流引擎 |
| `_settings.py` | `load_settings()`, `SecretString` | 环境变量配置加载，API Key 遮蔽 |

### LLM Provider 包

| 包 | 类 |
|---|---|
| `agent-framework-openai` | `OpenAIChatClient` |
| `agent-framework-anthropic` | Anthropic chat client |
| `agent-framework-bedrock` | AWS Bedrock client |
| `agent-framework-ollama` | 本地 Ollama |
| `agent-framework-azure-ai` | `AzureAIChatClient` / `FoundryAgent` |

### 编排模式包（`agent-framework-orchestrations`）

| Builder | 模式 |
|---|---|
| `SequentialBuilder` | 顺序执行 |
| `ConcurrentBuilder` | 并发扇出 |
| `HandoffBuilder` | 自主路由 handoff |
| `GroupChatBuilder` | Orchestrator 指挥多 agent |
| `MagenticBuilder` | Magentic One 模式 |

### 持久化包

| 包 | 后端 |
|---|---|
| `agent-framework-redis` | `RedisHistoryProvider` |
| `agent-framework-azure-cosmos` | `CosmosHistoryProvider` |
| `agent-framework-mem0` | `Mem0ContextProvider` |

---

## 替换方案分析

### 一、直接替换（高价值，可落地）

| Agent Framework 组件 | 替换 miniAgents 的什么 | 收益 | 工作量 |
|---|---|---|---|
| `agent-framework-openai` / `agent-framework-anthropic`（`BaseChatClient`） | `app/llm/` 整个层（BaseAdapter + LLMClient + 两个 Adapter） | 官方维护、支持 streaming/structured output/compaction/middleware | 高 — 接口不兼容，需重写 agent_loop & task_executor |
| `FunctionTool` + `@tool` 装饰器 | `app/tools/definition.py` ToolDefinition + handler | 自动从 Python type hints 生成 JSON Schema，零手写 schema | 中 — ToolGateway 需适配新接口 |
| `MCPStdioTool` / `MCPStreamableHTTPTool` | ToolProvider protocol 新实现 | 原生支持 MCP 服务器，直接接入外部工具生态 | 低 — 实现新的 ToolProvider 即可 |
| `load_settings()` + `SecretString` | `app/config/settings.py` 的 `_load_settings()` | API Key 不再出现在 repr/log 里 | 极低 — 10 行改动 |
| 消息压缩策略（`TruncationStrategy` / `SummarizationStrategy` / `TokenBudgetComposedStrategy`） | AgentLoop 里 `default_summary_threshold` 相关的手动逻辑 | 真正可运行的长上下文管理，不是空设置项 | 中 — 接入 AgentLoop |

### 二、部分借鉴（架构参考，按需取舍）

| Agent Framework 组件 | 对应 miniAgents 问题 | 建议 |
|---|---|---|
| `BaseHistoryProvider` + `InMemoryHistoryProvider` | `MemoryService` + `MemoryStore` 文件存储 | 如需对接 Redis/CosmosDB，用它替换；当前 file store 够用则跳过 |
| `AgentMiddleware` / `ChatMiddleware` / `FunctionMiddleware` | 目前无 middleware 层 | 中期加入，用于 logging / retry / rate-limit，不用全部替换 |
| `SkillsProvider` + `Skill` | `app/skills/` 文件系统方案 | 他们的是代码驱动的，我们是 YAML+Markdown 驱动的。**不替换**，我们的更声明式 |
| `WorkflowBuilder` + Pregel engine | `AgentLoop` 的 Observe→Plan→Execute 循环 | 太重，不适合现阶段；当未来需要复杂 multi-agent graph 时迁移 |
| `FileCheckpointStorage` | `app/storage/file/` 各 Store | 接口有参考价值，但我们已有自己的 atomic write 实现，无需替换 |

### 三、净新增（miniAgents 目前没有）

| 组件 | 功能 | 是否引入 |
|---|---|---|
| `MCPStdioTool` / `MCPStreamableHTTPTool` | 接入 MCP 生态（外部工具服务器）| 建议引入 |
| `A2A protocol`（`agent-framework-a2a`） | 跨平台 agent 通信 | 未来 multi-agent 场景引入 |
| `DevUI`（`agent-framework-devui`） | FastAPI + React 本地调试界面，带 OTel trace 展示 | 可选，开发期调试用 |

---

## 推荐执行顺序

```
阶段 1（低风险，立刻可做）
  ├─ SecretString 替换 settings.py 里的 api_key 字段
  └─ MCPStdioTool 作为新 ToolProvider 接入 ToolRegistry

阶段 2（中风险，需测试）
  ├─ FunctionTool + @tool 替换 ToolDefinition
  └─ 接入 TruncationStrategy / SummarizationStrategy 到 AgentLoop

阶段 3（高风险，接口改动大）
  └─ BaseChatClient 替换 BaseAdapter + LLMClient
     （需同步改 agent_loop.py, task_executor.py, deps.py）

暂不做
  ├─ WorkflowBuilder（当前架构够用）
  └─ SkillsProvider（我们的 YAML 方案更声明式）
```

---

## miniAgents 当前模块与 Agent Framework 对照

| miniAgents 模块 | Agent Framework 对应 | 操作 |
|---|---|---|
| `app/llm/llm_base.py` — `BaseAdapter` + `LLMClient` | `BaseChatClient` | 阶段 3 替换 |
| `app/llm/openai_adapter.py` | `OpenAIChatClient` | 阶段 3 替换 |
| `app/llm/anthropic_adapter.py` | Anthropic chat client | 阶段 3 替换 |
| `app/tools/definition.py` — `ToolDefinition` | `FunctionTool` + `@tool` | 阶段 2 替换 |
| `app/tools/provider.py` — `ToolProvider` | `MCPTool` 系列 | 阶段 1 扩展 |
| `app/skills/` | `Skill` + `SkillsProvider` | 保留（风格不同） |
| `app/config/settings.py` | `load_settings()` + `SecretString` | 阶段 1 替换 |
| `app/runtime/agent_loop.py` | `Agent` + `WorkflowBuilder` | 暂保留 |
| `app/domain/services/memory_service.py` | `BaseHistoryProvider` | 可选替换 |
| 无 | `AgentMiddleware` 三层中间件 | 中期新增 |
| 无 | `TruncationStrategy` / `SummarizationStrategy` | 阶段 2 新增 |
