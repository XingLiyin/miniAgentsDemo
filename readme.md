# NetLIVE-CoWork

A backend service for running **multi-agent LLM workflows** — featuring a task scheduler, multi-layer agent trees, tool execution, memory management, and a built-in Human-in-the-Loop mechanism.

Built as an architectural learning project; designed to be readable, extensible, and self-hostable.

---

## Highlights

- **Agent Loop** — Reasoner → Actor → Observer three-phase execution per task
- **Dynamic agent tree** — agents can spawn sub-agents at runtime, forming an arbitrarily deep execution tree
- **LIFO task queue** — depth-first task scheduling with DAG dependency tracking
- **Dual-phase tool authorization** — Actor and Observer phases each have independent tool allowlists
- **File-defined agents** — define agent identity, tools, and behavior with plain Markdown files (`SOUL.md`, `ROLE.md`, `TOOLS.md`)
- **Skills** — reusable workflows defined in `SKILL.md`, loaded on demand; skill scripts receive a `SKILL_DIR` env var for locating skill-relative files
- **Memory compaction** — rolling summary compression keeps context windows manageable
- **Blackboard** — parent→child result passing via topic-keyed file append log
- **Human-in-the-Loop** — agents can pause mid-execution and wait for user input
- **LLM-agnostic** — supports OpenAI-compatible and Anthropic APIs via a unified adapter layer

---

## Architecture

```
API Layer (FastAPI)
    └─ SessionManager (SM)  ──  creates sessions, root agents, and initial tasks
         └─ TaskManager (TM)  ──  scheduling, retries, DAG resolution, session finalization
              └─ LifecycleManager (LM)  ──  agent tree: spawn, reuse, recycle
                   └─ AgentLoop  ──  per-task execution
                        ├─ Reasoner   ── context assembly (memory + blackboard + tools)
                        ├─ Actor      ── LLM inference + tool use (multi-round)
                        └─ Observer   ── task assessment via control tools
```

**Storage (Phase 1):** file-based JSON/JSONL under `data/`. PostgreSQL + pgvector planned for Phase 2.

---

## Requirements

- Python 3.11–3.12
- An OpenAI-compatible or Anthropic API key

---

## Quickstart

```bash
# 1. Clone
git clone https://github.com/your-username/NetLIVE-CoWork.git
cd NetLIVE-CoWork

# 2. Install dependencies (uv recommended)
uv sync
# or: pip install -e .

# 3. Configure environment
cp .env.example .env
# Edit .env — set your LLM provider and API key (see Configuration below)

# 4. Start the server
uvicorn app.main:app --reload
```

The API is now available at `http://localhost:8000`. Interactive docs: `http://localhost:8000/docs`.

---

## Configuration

All settings are read from `.env` (prefix: `NETLIVE_COWORK_`).

| Variable | Default | Description |
|---|---|---|
| `NETLIVE_COWORK_DEFAULT_LLM_PROVIDER` | `ms-openai` | LLM provider name (must be registered) |
| `NETLIVE_COWORK_DEFAULT_LLM_MODEL` | `Qwen/Qwen3.5-27B` | Model identifier |
| `NETLIVE_COWORK_DATA_DIR` | `data` | Runtime data directory |
| `NETLIVE_COWORK_AGENTS_DIR` | `resources/agents` | Agent definition files root |
| `NETLIVE_COWORK_SKILLS_DIR` | `resources/skills` | Skill definition files root |
| `NETLIVE_COWORK_DEFAULT_TOKEN_BUDGET` | `200000` | Hard token limit per session |
| `NETLIVE_COWORK_MAX_CONCURRENT_AGENTS` | `5` | Max simultaneous agents per session |
| `NETLIVE_COWORK_MAX_SPAWN_DEPTH` | `1` | Max sub-agent nesting depth |
| `NETLIVE_COWORK_BASH_EXEC_TIMEOUT_MS` | `30000` | Bash tool execution timeout |
| `NETLIVE_COWORK_LOG_LEVEL` | `INFO` | Logging level |

### Registering an LLM Provider

```bash
curl -X POST http://localhost:8000/api/v1/llms \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-openai",
    "style": "openai",
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-...",
    "model": "gpt-4o",
    "timeout_sec": 120
  }'
```

---

## Usage

### Start a session

```bash
curl -X POST http://localhost:8000/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "user_prompt": "Summarize the latest news about LLM agents.",
    "template_id": "default"
  }'
```

Response includes a `session_id`. The session starts asynchronously.

### Stream events (SSE)

```bash
curl -N http://localhost:8000/api/v1/sessions/{session_id}/stream
```

Events include `text_delta` (streaming LLM output), `tool_call`, `task_created`, `task_updated`, `waiting_input`, and `done`.

### Send a follow-up message

```bash
curl -X POST http://localhost:8000/api/v1/sessions/{session_id}/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "Now write a one-paragraph summary based on the above."}'
```

### Respond to a Human-in-the-Loop pause

When the agent calls `request_human_input`, the session enters `WAITING_INPUT` and an SSE `waiting_input` event is emitted. Respond with:

```bash
curl -X POST http://localhost:8000/api/v1/sessions/{session_id}/input \
  -H "Content-Type: application/json" \
  -d '{"content": "Yes, proceed with option A."}'
```

---

## Defining Agents

Agents are defined as Markdown files inside a directory under `resources/agents/{agent-name}/`.

### SOUL.md — Actor identity (required)

```markdown
---
name: my-agent
version: 1.0.0
description: A focused research assistant.
tools:
  required:
    - bash_exec
    - http_request
    - submit_task
  forbidden: []
mcp_servers: []
---

You are a research assistant. Your job is to gather information and produce clear, factual summaries.

- Always cite your sources.
- If the task is complex, delegate sub-tasks via `submit_task`.
```

### ROLE.md — Observer identity (optional)

```markdown
---
tools:
  required:
    - submit_task_assessment
  forbidden: []
---

Evaluate whether the research task has been completed correctly and completely.
Only call `submit_task_assessment` once you have reviewed the Actor's output.
```

The Agent is auto-discovered on startup. Use the template name (`my-agent`) when creating sessions.

### SOUL.md Frontmatter Reference

| Field | Type | Description |
|---|---|---|
| `name` | string | Template name (matches directory name) |
| `version` | semver | Used for cache invalidation |
| `description` | string | Short description |
| `tools.required` | list | Tools available in Actor phase |
| `tools.forbidden` | list | Tools explicitly blocked |
| `mcp_servers` | list | MCP server names to subscribe |
| `subagents` | list | Sub-agent template names (for prompt injection) |
| `has_spawn_permission` | bool | Allow spawning sub-agents (default: false) |

---

## Defining Skills

Skills are reusable instruction sets loaded on demand. Place a `SKILL.md` in `resources/skills/{skill-name}/`:

```markdown
---
name: summarize-document
description: Summarize a document into bullet points
triggers:
  - summarize
  - summarization
version: 1.0.0
---

## Instructions

1. Read the document using `read_file`.
2. Identify the 5 most important points.
3. Return them as a bulleted list, one point per line.
```

An agent can invoke a skill by calling `submit_task` with `skill_name: "summarize-document"`.

---

## Built-in Tools

| Tool | Description |
|---|---|
| `bash_exec` | Run shell commands (blacklisted commands blocked; timeout enforced) |
| `http_request` | Make HTTP requests (SSRF protection included) |
| `read_file` / `write_file` / `glob` | File system operations (scoped to `working_dir`) |
| `exec_skill_script` | Execute a script in the skill directory; `SKILL_DIR` env var is injected so scripts can locate skill-relative files via `os.environ["SKILL_DIR"]` |
| `load_skill_reference` | Load a skill's reference document into context |

### Control Tools (agent-internal)

| Tool | Description |
|---|---|
| `submit_plan(tasks)` | Decompose current task into sub-tasks; parent suspends until all complete |
| `submit_task(...)` | Spawn a single sub-task (current task suspends) |
| `submit_task_assessment(...)` | Observer declares the task outcome: `success`, `failed`, `active`, or `needs_user_input` |
| `replan(reason)` | Cancel all pending tasks and start fresh with a new plan |
| `request_human_input(prompt)` | Pause session and wait for user response |
| `update_task_metadata(...)` | Write task title, description, and session goal |

---

## Project Structure

```
NetLIVE-CoWork/
├── app/
│   ├── api/v1/          # FastAPI routes and request/response schemas
│   ├── agent_template/  # Agent definition loader and in-memory registry
│   ├── common/          # Utilities: ID generation, SSE bus, error types
│   ├── config/          # Settings (pydantic-settings, NETLIVE_COWORK_ prefix)
│   ├── domain/
│   │   ├── models/      # Dataclasses: Session, Task, Agent, Memory, ...
│   │   ├── services/    # Domain services: CRUD + state machine transitions
│   │   ├── events/      # EventBus + event type constants
│   │   └── state_machine.py
│   ├── llm/             # LLM adapters: OpenAI, Anthropic, Mock
│   ├── orchestrator/    # SessionManager, TaskManager, LifecycleManager, TaskQueue
│   ├── runtime/         # AgentLoop, Actor, Observer, Reasoner, PromptBuilder
│   ├── skills/          # Skill loader, registry, executor
│   ├── storage/file/    # File-based persistence (JSON/JSONL)
│   └── tools/           # ToolRegistry, builtin tools, control tools, MCP providers
├── resources/
│   ├── agents/          # Agent definition directories (SOUL.md, ROLE.md, ...)
│   └── skills/          # Skill definition directories (SKILL.md)
├── data/                # Runtime data (sessions, tasks, memories) — git-ignored
└── tests/
```

---

## Roadmap

- [ ] PostgreSQL + pgvector backend (Phase 2)
- [ ] Persistent Blackboard cursors (survives process restart)
- [ ] Parallel task scheduling (`pop_batch` + concurrent dispatch)
- [ ] Formal HITL approval entity with timeout and modify support
- [ ] `failure_threshold` → automatic HITL escalation
- [ ] WebSocket support for bidirectional streaming
- [ ] Multi-root agent collaboration
- [ ] `auto-dream` sub-agent for background context maintenance

---

## Design Document

See [`docs/NetLIVE-CoWork_设计文档.md`](docs/NetLIVE-CoWork_设计文档.md) for the full architecture reference: domain models, state machines, scheduling flow, tool execution, memory management, and more.

---

## License

MIT
