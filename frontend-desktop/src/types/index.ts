export type SessionStatus =
  | 'QUEUED' | 'RUNNING' | 'WAITING_INPUT'
  | 'SUCCEEDED' | 'FAILED' | 'CANCELED' | 'INTERRUPTED' | 'PAUSED_HITL'

export const TERMINAL_STATUSES: SessionStatus[] = ['SUCCEEDED', 'FAILED', 'CANCELED', 'INTERRUPTED']

export interface Session {
  id: string
  user_prompt: string
  goal: string
  status: SessionStatus
  template_id: string | null
  root_agent_id: string | null
  token_budget: number
  input_tokens_used: number
  output_tokens_used: number
  context_tokens: number
  failure_counter: number
  llm_provider: string | null
  llm_model: string | null
  working_dir: string
  created_at: string
  updated_at: string
}

export interface CreateSessionRequest {
  user_prompt: string
  template_id?: string | null
  token_budget?: number
  llm_provider?: string | null
  llm_model?: string | null
  working_dir?: string | null
}

export type LLMStyle = 'openai' | 'anthropic'

export interface ModelConfig {
  name: string
  context_limit: number
}

export interface LLMProvider {
  name: string
  style: LLMStyle
  base_url: string
  models: ModelConfig[]
  default_model: string
  timeout_sec: number
}

export interface RegisterLLMRequest {
  name: string
  style: LLMStyle
  api_key: string
  base_url?: string
  models?: { name: string; context_limit?: number | null }[]
  default_model?: string
  timeout_sec?: number
}

export interface PendingSession {
  workingDir: string
  provider: string
  model: string
}

export interface WorkspaceEntry {
  name: string
  path: string
  is_dir: boolean
  size: number | null
}

export interface WorkspaceListing {
  root: string
  path: string
  parent: string
  entries: WorkspaceEntry[]
}
