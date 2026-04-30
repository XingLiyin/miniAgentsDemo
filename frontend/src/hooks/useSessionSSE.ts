import { useEffect, useRef, useCallback, useState } from 'react'
import type { Session, Task } from '@/types'
import { now } from '@/lib/time'

// ── Chat item types ───────────────────────────────────────────────────────────

export type ChatItemKind =
  | 'message'
  | 'tool_call'
  | 'task_event'
  | 'session_event'
  | 'waiting_input'
  | 'llm_prompt'
  | 'observer_message'
  | 'observer_tool_call'

export interface ChatImageData {
  media_type: string
  source_type: 'base64' | 'url'
  data: string
}

export interface ChatMessage {
  id: string
  kind: 'message'
  role: 'user' | 'assistant'
  content: string
  reasoning?: string
  images?: ChatImageData[]
  created_at: string
}

export interface ChatToolCall {
  id: string
  kind: 'tool_call'
  tool_name: string
  arguments: Record<string, unknown>
  result: string
  is_error: boolean
  created_at: string
}

export interface ChatTaskEvent {
  id: string
  kind: 'task_event'
  task: Task
  created_at: string
}

export interface ChatWaitingInput {
  id: string
  kind: 'waiting_input'
  prompt: string
  input_type: string
  task_title: string
  command?: string   // bash_exec_confirm only
  created_at: string
}

export interface ChatLLMPrompt {
  id: string
  kind: 'llm_prompt'
  source: string        // 'actor' | 'observer'
  round_label: string
  system_prompt: string
  messages: Array<{ role: string; content: string | object[] }>
  tool_names: string[]
  created_at: string
}

export interface ChatObserverMessage {
  id: string
  kind: 'observer_message'
  round_label: string
  content: string
  reasoning?: string
  created_at: string
}

export interface ChatObserverToolCall {
  id: string
  kind: 'observer_tool_call'
  round_label: string
  tool_name: string
  arguments: Record<string, unknown>
  result: string
  is_error: boolean
  created_at: string
}

export type ChatItem = ChatMessage | ChatToolCall | ChatTaskEvent | ChatWaitingInput | ChatLLMPrompt | ChatObserverMessage | ChatObserverToolCall

export interface SSEState {
  session: Session | null
  tasks: Task[]
  items: ChatItem[]
  waitingInput: ChatWaitingInput | null
  streamingText: string | null
  streamingReasoning: string | null
  streamingImages: ChatImageData[]
  observerStreamingText: string | null
  observerStreamingReasoning: string | null
  connected: boolean
  error: string | null
}

let _idCounter = 0
function uid() {
  return `sse-${Date.now()}-${_idCounter++}`
}

// ── useSessionSSE hook ────────────────────────────────────────────────────────

export function useSessionSSE(sessionId: string | null): SSEState {
  const [state, setState] = useState<SSEState>({
    session: null,
    tasks: [],
    items: [],
    waitingInput: null,
    streamingText: null,
    streamingReasoning: null,
    streamingImages: [],
    observerStreamingText: null,
    observerStreamingReasoning: null,
    connected: false,
    error: null,
  })

  const esRef = useRef<EventSource | null>(null)
  const sessionIdRef = useRef<string | null>(null)

  const close = useCallback(() => {
    if (esRef.current) {
      esRef.current.close()
      esRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!sessionId) {
      close()
      setState({ session: null, tasks: [], items: [], waitingInput: null, streamingText: null, streamingReasoning: null, streamingImages: [], observerStreamingText: null, observerStreamingReasoning: null, connected: false, error: null })
      return
    }

    if (sessionIdRef.current === sessionId && esRef.current) return

    // Close previous connection
    close()
    sessionIdRef.current = sessionId
    setState({ session: null, tasks: [], items: [], waitingInput: null, streamingText: null, streamingReasoning: null, streamingImages: [], observerStreamingText: null, observerStreamingReasoning: null, connected: false, error: null })

    const es = new EventSource(`/api/v1/sessions/${sessionId}/stream`)
    esRef.current = es

    es.onopen = () => {
      setState(s => ({ ...s, connected: true, error: null }))
    }

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        handleEvent(data)
      } catch {
        // ignore parse errors
      }
    }

    es.onerror = () => {
      setState(s => ({ ...s, connected: false, error: '连接断开，正在重连...' }))
    }

    function parseContent(raw: unknown): { text: string; images?: ChatImageData[] } {
      if (Array.isArray(raw)) {
        const parts = raw as Array<Record<string, string>>
        const images = parts
          .filter(p => p.type === 'image')
          .map(p => ({ media_type: p.media_type || '', source_type: (p.source_type || 'base64') as 'base64' | 'url', data: p.data || '' }))
        const text = parts.filter(p => p.type === 'text').map(p => p.text || '').join('\n')
        return { text, images: images.length > 0 ? images : undefined }
      }
      return { text: (raw as string) || '' }
    }

    function eventToItem(evt: Record<string, unknown>, extras?: { actorReasoning?: string; observerReasoning?: string }): ChatItem | null {
      const t = evt.type as string
      if (t === 'message') {
        const { text, images } = parseContent(evt.content)
        return {
          id: uid(),
          kind: 'message',
          role: (evt.role as 'user' | 'assistant') || 'assistant',
          content: text,
          images,
          created_at: (evt.created_at as string) || '',
        }
      }
      if (t === 'text_done') {
        const text = (evt.text as string) || ''
        if (!text) return null
        return {
          id: uid(),
          kind: 'message',
          role: 'assistant',
          content: text,
          reasoning: extras?.actorReasoning,
          created_at: (evt.created_at as string) || '',
        }
      }
      if (t === 'tool_call') {
        return {
          id: uid(),
          kind: 'tool_call',
          tool_name: (evt.tool_name as string) || '',
          arguments: (evt.arguments as Record<string, unknown>) || {},
          result: (evt.result as string) || '',
          is_error: (evt.is_error as boolean) || false,
          created_at: (evt.created_at as string) || '',
        }
      }
      if (t === 'task_created' || t === 'task_updated') {
        const task = evt.task as import('@/types').Task
        return {
          id: uid(),
          kind: 'task_event',
          task,
          created_at: task?.created_at || '',
        }
      }
      if (t === 'llm_prompt') {
        return {
          id: uid(),
          kind: 'llm_prompt',
          source: (evt.source as string) || 'actor',
          round_label: (evt.round_label as string) || '',
          system_prompt: (evt.system_prompt as string) || '',
          messages: (evt.messages as Array<{ role: string; content: string | object[] }>) || [],
          tool_names: (evt.tool_names as string[]) || [],
          created_at: (evt.created_at as string) || '',
        }
      }
      if (t === 'observer_text_done') {
        const text = (evt.text as string) || ''
        if (!text) return null
        return {
          id: uid(),
          kind: 'observer_message',
          round_label: (evt.round_label as string) || '',
          content: text,
          reasoning: extras?.observerReasoning,
          created_at: (evt.created_at as string) || '',
        }
      }
      if (t === 'observer_tool_call') {
        return {
          id: uid(),
          kind: 'observer_tool_call',
          round_label: (evt.round_label as string) || '',
          tool_name: (evt.tool_name as string) || '',
          arguments: (evt.arguments as Record<string, unknown>) || {},
          result: (evt.result as string) || '',
          is_error: (evt.is_error as boolean) || false,
          created_at: (evt.created_at as string) || '',
        }
      }
      return null
    }

    function handleEvent(data: Record<string, unknown>) {
      const type = data.type as string

      if (type === 'ping') return

      if (type === 'history') {
        const events = (data.events as Array<Record<string, unknown>>) || []
        const historyItems: ChatItem[] = []
        let actorReasoning: string | undefined
        let observerReasoning: string | undefined
        for (const evt of events) {
          const evtType = evt.type as string
          if (evtType === 'reasoning_done') {
            actorReasoning = ((evt.text as string) || '') || undefined
            continue
          }
          if (evtType === 'observer_reasoning_done') {
            observerReasoning = ((evt.text as string) || '') || undefined
            continue
          }
          const item = eventToItem(evt, { actorReasoning, observerReasoning })
          if (item) historyItems.push(item)
          if (evtType === 'text_done') actorReasoning = undefined
          if (evtType === 'observer_text_done') observerReasoning = undefined
        }
        setState(s => ({ ...s, items: historyItems }))
        return
      }

      if (type === 'init') {
        const session = data.session as Session
        const tasks = (data.tasks as Task[]) || []
        const messages = (data.messages as Array<Record<string, unknown>>) || []

        // Build initial chat items from history messages
        const items: ChatItem[] = messages.map((m) => {
          const { text, images } = parseContent(m.content)
          return {
            id: uid(),
            kind: 'message' as const,
            role: (m.role as 'user' | 'assistant') || 'assistant',
            content: text,
            images,
            created_at: (m.created_at as string) || '',
          }
        })

        setState({
          session,
          tasks,
          items,
          waitingInput: null,
          streamingText: null,
          streamingReasoning: null,
          streamingImages: [],
          observerStreamingText: null,
          observerStreamingReasoning: null,
          connected: true,
          error: null,
        })
        return
      }

      if (type === 'session_update') {
        setState(s => ({
          ...s,
          session: s.session
            ? { ...s.session, status: data.status as Session['status'], token_used: (data.token_used as number) ?? s.session.token_used }
            : s.session,
          // clear waiting input & streaming when session resumes
          waitingInput: data.status === 'RUNNING' ? null : s.waitingInput,
          streamingText: data.status === 'RUNNING' ? null : s.streamingText,
          streamingReasoning: data.status === 'RUNNING' ? null : s.streamingReasoning,
          observerStreamingText: data.status === 'RUNNING' ? null : s.observerStreamingText,
          observerStreamingReasoning: data.status === 'RUNNING' ? null : s.observerStreamingReasoning,
        }))
        return
      }

      if (type === 'task_created') {
        const task = data.task as Task
        setState(s => {
          const exists = s.tasks.some(t => t.id === task.id)
          return {
            ...s,
            tasks: exists ? s.tasks : [...s.tasks, task],
            items: [...s.items, {
              id: uid(),
              kind: 'task_event' as const,
              task,
              created_at: task.created_at,
            }],
          }
        })
        return
      }

      if (type === 'task_updated') {
        const task = data.task as Task
        setState(s => ({
          ...s,
          tasks: s.tasks.map(t => t.id === task.id ? task : t),
          items: [...s.items, {
            id: uid(),
            kind: 'task_event' as const,
            task,
            created_at: task.updated_at,
          }],
        }))
        return
      }

      if (type === 'message') {
        const { text, images } = parseContent(data.content)
        const item: ChatMessage = {
          id: uid(),
          kind: 'message',
          role: (data.role as 'user' | 'assistant') || 'assistant',
          content: text,
          images,
          created_at: (data.created_at as string) || now(),
        }
        setState(s => ({ ...s, items: [...s.items, item] }))
        return
      }

      if (type === 'tool_call') {
        const item: ChatToolCall = {
          id: uid(),
          kind: 'tool_call',
          tool_name: (data.tool_name as string) || '',
          arguments: (data.arguments as Record<string, unknown>) || {},
          result: (data.result as string) || '',
          is_error: (data.is_error as boolean) || false,
          created_at: (data.created_at as string) || now(),
        }
        setState(s => ({ ...s, items: [...s.items, item] }))
        return
      }

      if (type === 'waiting_input' || type === 'bash_exec_confirm') {
        const item: ChatWaitingInput = {
          id: uid(),
          kind: 'waiting_input',
          prompt: (data.prompt as string) || '',
          input_type: type === 'bash_exec_confirm' ? 'bash_exec_confirm' : ((data.input_type as string) || 'user_input'),
          task_title: (data.task_title as string) || '',
          command: type === 'bash_exec_confirm' ? (data.command as string) || '' : undefined,
          created_at: now(),
        }
        setState(s => ({ ...s, waitingInput: item }))
        return
      }

      if (type === 'text_delta') {
        const delta = (data.delta as string) || ''
        if (delta) {
          setState(s => ({ ...s, streamingText: (s.streamingText ?? '') + delta }))
        }
        return
      }

      if (type === 'reasoning_delta') {
        const delta = (data.delta as string) || ''
        if (delta) {
          setState(s => ({ ...s, streamingReasoning: (s.streamingReasoning ?? '') + delta }))
        }
        return
      }

      if (type === 'image') {
        const img: ChatImageData = {
          media_type: (data.media_type as string) || '',
          source_type: (data.source_type as 'base64' | 'url') || 'base64',
          data: (data.data as string) || '',
        }
        setState(s => ({ ...s, streamingImages: [...s.streamingImages, img] }))
        return
      }

      if (type === 'text_done') {
        const text = (data.text as string) || ''
        setState(s => {
          if (!text && s.streamingImages.length === 0 && !s.streamingReasoning) {
            return { ...s, streamingText: null, streamingReasoning: null }
          }
          const item: ChatMessage = {
            id: uid(),
            kind: 'message',
            role: 'assistant',
            content: text,
            reasoning: s.streamingReasoning || undefined,
            images: s.streamingImages.length > 0 ? s.streamingImages : undefined,
            created_at: now(),
          }
          return { ...s, items: [...s.items, item], streamingText: null, streamingReasoning: null, streamingImages: [] }
        })
        return
      }

      if (type === 'reasoning_done') {
        const reasoning = (data.text as string) || ''
        if (reasoning) {
          setState(s => ({ ...s, streamingReasoning: reasoning }))
        }
        return
      }

      if (type === 'llm_prompt') {
        const item: ChatLLMPrompt = {
          id: uid(),
          kind: 'llm_prompt',
          source: (data.source as string) || 'actor',
          round_label: (data.round_label as string) || '',
          system_prompt: (data.system_prompt as string) || '',
          messages: (data.messages as Array<{ role: string; content: string }>) || [],
          tool_names: (data.tool_names as string[]) || [],
          created_at: now(),
        }
        setState(s => ({ ...s, items: [...s.items, item] }))
        return
      }

      if (type === 'observer_text_delta') {
        const delta = (data.delta as string) || ''
        if (delta) {
          setState(s => ({ ...s, observerStreamingText: (s.observerStreamingText ?? '') + delta }))
        }
        return
      }

      if (type === 'observer_reasoning_delta') {
        const delta = (data.delta as string) || ''
        if (delta) {
          setState(s => ({ ...s, observerStreamingReasoning: (s.observerStreamingReasoning ?? '') + delta }))
        }
        return
      }

      if (type === 'observer_text_done') {
        const text = (data.text as string) || ''
        const round_label = (data.round_label as string) || ''
        setState(s => {
          if (!text && !s.observerStreamingReasoning) {
            return { ...s, observerStreamingText: null, observerStreamingReasoning: null }
          }
          const item: ChatObserverMessage = {
            id: uid(),
            kind: 'observer_message',
            round_label,
            content: text,
            reasoning: s.observerStreamingReasoning || undefined,
            created_at: now(),
          }
          return { ...s, items: [...s.items, item], observerStreamingText: null, observerStreamingReasoning: null }
        })
        return
      }

      if (type === 'observer_reasoning_done') {
        const reasoning = (data.text as string) || ''
        if (reasoning) {
          setState(s => ({ ...s, observerStreamingReasoning: reasoning }))
        }
        return
      }

      if (type === 'observer_tool_call') {
        const item: ChatObserverToolCall = {
          id: uid(),
          kind: 'observer_tool_call',
          round_label: (data.round_label as string) || '',
          tool_name: (data.tool_name as string) || '',
          arguments: (data.arguments as Record<string, unknown>) || {},
          result: (data.result as string) || '',
          is_error: (data.is_error as boolean) || false,
          created_at: (data.created_at as string) || now(),
        }
        setState(s => ({ ...s, items: [...s.items, item] }))
        return
      }

      if (type === 'done') {
        setState(s => ({
          ...s,
          waitingInput: null,
          streamingText: null,
          streamingReasoning: null,
          streamingImages: [],
          observerStreamingText: null,
          observerStreamingReasoning: null,
          session: s.session
            ? { ...s.session, status: (data.final_status as Session['status']) || s.session.status }
            : s.session,
        }))
      }
    }

    return () => {
      close()
    }
  }, [sessionId, close])

  return state
}
