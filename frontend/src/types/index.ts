// ─── Session ────────────────────────────────────────────────────────────────

export type SessionStatus =
  | 'QUEUED'
  | 'RUNNING'
  | 'WAITING_INPUT'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELED'
  | 'PAUSED_HITL'

export const TERMINAL_STATUSES: SessionStatus[] = ['SUCCEEDED', 'FAILED', 'CANCELED']

export interface Session {
  id: string
  user_prompt: string
  status: SessionStatus
  template_id: string | null
  root_agent_id: string | null
  token_budget: number
  token_used: number
  root_max_turns: number
  failure_counter: number
  created_at: string
  updated_at: string
}

export interface InitialTaskConfig {
  title?: string | null
  use_subagent?: boolean
  subagent_template?: string | null
}

export interface CreateSessionRequest {
  user_prompt: string
  template_id?: string | null
  token_budget?: number
  root_max_turns?: number
  llm_name?: string | null
  llm_model?: string | null
  working_dir?: string | null
  initial_task?: InitialTaskConfig | null
}

// ─── Task ────────────────────────────────────────────────────────────────────

export type TaskStatus = 'PENDING' | 'ACTIVE' | 'FINISHED' | 'FAILED' | 'CANCELED'

export interface Task {
  id: string
  session_id: string
  creator_agent_id: string
  assigned_agent_id: string
  title: string
  status: TaskStatus
  description: string
  user_prompt: string
  settings: Record<string, unknown>
  result: string | null
  outputs: Record<string, unknown>
  error: string | null
  created_at: string
  updated_at: string
}

// ─── Agent ───────────────────────────────────────────────────────────────────

export type AgentStatus = 'IDLE' | 'RUNNING' | 'FINISHED' | 'FAILED'

export interface Agent {
  id: string
  session_id: string
  template_id: string | null
  name: string
  status: AgentStatus
  system_prompt: string
  tool_list: string[]
  skill_list: string[]
  loop_guard: { turns_used: number; max_turns: number }
  llm_name: string
}

// ─── AgentTemplate ───────────────────────────────────────────────────────────

export interface AgentTemplate {
  id: string
  name: string
  description: string
  version: string
  system_prompt: string
  tool_list: string[]
  skill_list: string[]
  tool_list_ready: boolean
  inject_style: boolean
  has_spawn_permission: boolean
  summary_threshold: number
  short_window_size: number
  created_at: string
  updated_at: string
}

// ─── LLM Provider ────────────────────────────────────────────────────────────

export type LLMStyle = 'openai' | 'anthropic'

export interface LLMProvider {
  name: string
  style: LLMStyle
  base_url: string
  models: string[]
  default_model: string
  timeout_sec: number
  max_tokens: number
}

export interface RegisterLLMRequest {
  name: string
  style: LLMStyle
  api_key: string
  base_url?: string
  models?: string[]
  default_model?: string
  timeout_sec?: number
  max_tokens?: number
}

// ─── Memory ──────────────────────────────────────────────────────────────────

export type MessageRole = 'user' | 'assistant' | 'tool'

export interface MemoryMessage {
  id: string
  session_id: string
  role: MessageRole
  content: string
  task_id: string | null
  created_at: string
}

export interface MemorySummary {
  session_id: string
  content: string
  message_count: number
  created_at: string
}

// ─── ToolCall ────────────────────────────────────────────────────────────────

export type ToolCallStatus = 'RUNNING' | 'SUCCEEDED' | 'FAILED'

export interface ToolCall {
  id: string
  session_id: string
  task_id: string | null
  agent_id: string
  tool_name: string
  status: ToolCallStatus
  arguments: Record<string, unknown>
  result: string | null
  error: string | null
  started_at: string
  finished_at: string | null
}

// ─── MCP Server ──────────────────────────────────────────────────────────────

export type MCPServerType = 'stdio' | 'http'
export type MCPServerStatus = 'CONNECTED' | 'DISCONNECTED' | 'ERROR'

export interface MCPServer {
  name: string
  type: MCPServerType
  status: MCPServerStatus
  tool_count: number
  tools?: MCPTool[]
  created_at?: string
}

export interface MCPTool {
  name: string
  description: string
}

export interface RegisterMCPStdioRequest {
  name: string
  command: string
  args?: string[]
  env?: Record<string, string>
}

export interface RegisterMCPHttpRequest {
  name: string
  url: string
  timeout?: number
}

// ─── Remote Skill Source ─────────────────────────────────────────────────────

export interface RemoteSkillSource {
  source_name: string
  mcp_type: 'http' | 'stdio'
  mcp_tool_list_skills: string
  mcp_tool_load_skill_md: string
  mcp_tool_get_skill_files: string
  mcp_tool_load_skill_reference: string
  mcp_tool_exec_skill_script: string
  // http
  mcp_url?: string | null
  mcp_timeout?: number | null
  // stdio
  mcp_command?: string | null
  mcp_args?: string[] | null
  mcp_env?: Record<string, string> | null
}

export interface RegisterSkillSourceHttpRequest {
  source_name: string
  mcp_url: string
  mcp_timeout?: number
  mcp_tool_list_skills?: string
  mcp_tool_load_skill_md?: string
  mcp_tool_get_skill_files?: string
  mcp_tool_load_skill_reference?: string
  mcp_tool_exec_skill_script?: string
}

export interface RegisterSkillSourceStdioRequest {
  source_name: string
  mcp_command: string
  mcp_args?: string[]
  mcp_env?: Record<string, string>
  mcp_tool_list_skills?: string
  mcp_tool_load_skill_md?: string
  mcp_tool_get_skill_files?: string
  mcp_tool_load_skill_reference?: string
  mcp_tool_exec_skill_script?: string
}

// ─── API Error ───────────────────────────────────────────────────────────────

export interface ApiError {
  code: string
  message: string
}
