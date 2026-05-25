import { useEffect, useRef, useCallback, useState } from 'react'
import type { Session } from '@/types'
import { now } from '@/lib/utils'

// ── Item types (desktop: no control/daemon/task items) ───────────────────────

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

export interface ChatWaitingInput {
  id: string
  kind: 'waiting_input'
  prompt: string
  input_type: string
  task_title: string
  command?: string
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

export type ChatItem = ChatMessage | ChatToolCall | ChatWaitingInput | ChatObserverMessage

export interface SSEState {
  session: Session | null
  items: ChatItem[]
  waitingInput: ChatWaitingInput | null
  streamingText: string | null
  streamingReasoning: string | null
  streamingImages: ChatImageData[]
  observerStreamingText: string | null
  connected: boolean
  error: string | null
}

const EMPTY: SSEState = {
  session: null, items: [], waitingInput: null,
  streamingText: null, streamingReasoning: null, streamingImages: [],
  observerStreamingText: null, connected: false, error: null,
}

let _idCounter = 0
function uid() { return `sse-${Date.now()}-${_idCounter++}` }

// ── Hook ─────────────────────────────────────────────────────────────────────

export function useSessionSSE(sessionId: string | null): SSEState {
  const [state, setState] = useState<SSEState>(EMPTY)
  const esRef = useRef<EventSource | null>(null)
  const sessionIdRef = useRef<string | null>(null)

  const close = useCallback(() => {
    esRef.current?.close()
    esRef.current = null
  }, [])

  useEffect(() => {
    if (!sessionId) {
      close()
      setState(EMPTY)
      return
    }
    if (sessionIdRef.current === sessionId && esRef.current) return

    close()
    sessionIdRef.current = sessionId
    setState(EMPTY)

    const es = new EventSource(`/api/v1/sessions/${sessionId}/stream`)
    esRef.current = es

    es.onopen = () => setState(s => ({ ...s, connected: true, error: null }))
    es.onerror = () => setState(s => ({ ...s, connected: false, error: '连接断开，正在重连…' }))

    es.onmessage = (event) => {
      try { handle(JSON.parse(event.data as string)) } catch { /* ignore */ }
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
      return { text: String(raw ?? '') }
    }

    function handle(data: Record<string, unknown>) {
      const type = data.type as string
      if (type === 'ping') return

      if (type === 'history') {
        const events = (data.events as Array<Record<string, unknown>>) || []
        const items: ChatItem[] = []
        let actorReasoning: string | undefined
        let observerReasoning: string | undefined
        for (const evt of events) {
          const et = evt.type as string
          if (et === 'reasoning_done') { actorReasoning = (evt.text as string) || undefined; continue }
          if (et === 'observer_reasoning_done') { observerReasoning = (evt.text as string) || undefined; continue }
          const item = evtToItem(evt, actorReasoning, observerReasoning)
          if (item) items.push(item)
          if (et === 'text_done') actorReasoning = undefined
          if (et === 'observer_text_done') observerReasoning = undefined
        }
        setState(s => ({ ...s, items }))
        return
      }

      if (type === 'init') {
        const session = data.session as Session
        const messages = (data.messages as Array<Record<string, unknown>>) || []
        setState(s => {
          if (s.items.length > 0) {
            return { ...s, session, waitingInput: null, streamingText: null, streamingReasoning: null, streamingImages: [], observerStreamingText: null, connected: true, error: null }
          }
          const items: ChatItem[] = messages.map(m => {
            const { text, images } = parseContent(m.content)
            return { id: uid(), kind: 'message' as const, role: (m.role as 'user' | 'assistant') || 'assistant', content: text, images, created_at: (m.created_at as string) || '' }
          })
          return { ...EMPTY, session, items, connected: true }
        })
        return
      }

      if (type === 'token_update') {
        setState(s => ({ ...s, session: s.session ? { ...s.session, input_tokens_used: (data.input_tokens_used as number) ?? s.session.input_tokens_used, output_tokens_used: (data.output_tokens_used as number) ?? s.session.output_tokens_used, context_tokens: (data.context_tokens as number) ?? s.session.context_tokens } : s.session }))
        return
      }

      if (type === 'session_update') {
        setState(s => {
          if (!s.session) return s
          const status = data.status as Session['status']
          const updated: Session = {
            ...s.session,
            status,
            ...(data.llm_provider != null ? { llm_provider: data.llm_provider as string } : {}),
            ...(data.llm_model != null ? { llm_model: data.llm_model as string } : {}),
          }
          return {
            ...s,
            session: updated,
            waitingInput: status === 'RUNNING' ? null : s.waitingInput,
            streamingText: status === 'RUNNING' ? null : s.streamingText,
            streamingReasoning: status === 'RUNNING' ? null : s.streamingReasoning,
            observerStreamingText: status === 'RUNNING' ? null : s.observerStreamingText,
          }
        })
        return
      }

      if (type === 'message') {
        const { text, images } = parseContent(data.content)
        setState(s => ({ ...s, items: [...s.items, { id: uid(), kind: 'message', role: (data.role as 'user' | 'assistant') || 'assistant', content: text, images, created_at: (data.created_at as string) || now() }] }))
        return
      }

      if (type === 'tool_call') {
        setState(s => ({ ...s, items: [...s.items, { id: uid(), kind: 'tool_call', tool_name: (data.tool_name as string) || '', arguments: (data.arguments as Record<string, unknown>) || {}, result: (data.result as string) || '', is_error: (data.is_error as boolean) || false, created_at: (data.created_at as string) || now() }] }))
        return
      }

      if (type === 'waiting_input' || type === 'bash_exec_confirm') {
        const item: ChatWaitingInput = { id: uid(), kind: 'waiting_input', prompt: (data.prompt as string) || '', input_type: type === 'bash_exec_confirm' ? 'bash_exec_confirm' : ((data.input_type as string) || 'user_input'), task_title: (data.task_title as string) || '', command: type === 'bash_exec_confirm' ? (data.command as string) || '' : undefined, created_at: now() }
        setState(s => ({ ...s, waitingInput: item }))
        return
      }

      if (type === 'text_delta') {
        const delta = (data.delta as string) || ''
        if (delta) setState(s => ({ ...s, streamingText: (s.streamingText ?? '') + delta }))
        return
      }

      if (type === 'reasoning_delta') {
        const delta = (data.delta as string) || ''
        if (delta) setState(s => ({ ...s, streamingReasoning: (s.streamingReasoning ?? '') + delta }))
        return
      }

      if (type === 'image') {
        const img: ChatImageData = { media_type: (data.media_type as string) || '', source_type: (data.source_type as 'base64' | 'url') || 'base64', data: (data.data as string) || '' }
        setState(s => ({ ...s, streamingImages: [...s.streamingImages, img] }))
        return
      }

      if (type === 'text_done') {
        const text = (data.text as string) || ''
        setState(s => {
          if (!text && s.streamingImages.length === 0 && !s.streamingReasoning) return { ...s, streamingText: null, streamingReasoning: null }
          const item: ChatMessage = { id: uid(), kind: 'message', role: 'assistant', content: text, reasoning: s.streamingReasoning || undefined, images: s.streamingImages.length > 0 ? s.streamingImages : undefined, created_at: now() }
          return { ...s, items: [...s.items, item], streamingText: null, streamingReasoning: null, streamingImages: [] }
        })
        return
      }

      if (type === 'reasoning_done') {
        const reasoning = (data.text as string) || ''
        if (reasoning) setState(s => ({ ...s, streamingReasoning: reasoning }))
        return
      }

      if (type === 'observer_text_delta') {
        const delta = (data.delta as string) || ''
        if (delta) setState(s => ({ ...s, observerStreamingText: (s.observerStreamingText ?? '') + delta }))
        return
      }

      if (type === 'observer_text_done') {
        const text = (data.text as string) || ''
        const round_label = (data.round_label as string) || ''
        setState(s => {
          if (!text) return { ...s, observerStreamingText: null }
          const item: ChatObserverMessage = { id: uid(), kind: 'observer_message', round_label, content: text, created_at: now() }
          return { ...s, items: [...s.items, item], observerStreamingText: null }
        })
        return
      }

      if (type === 'done') {
        setState(s => ({ ...s, waitingInput: null, streamingText: null, streamingReasoning: null, streamingImages: [], observerStreamingText: null, session: s.session ? { ...s.session, status: (data.final_status as Session['status']) || s.session.status } : s.session }))
      }

      // Silently ignore: control_tool_call, daemon_*, task_*, llm_prompt
    }

    function evtToItem(evt: Record<string, unknown>, actorReasoning?: string, observerReasoning?: string): ChatItem | null {
      const t = evt.type as string
      if (t === 'message') {
        const { text, images } = parseContent(evt.content)
        return { id: uid(), kind: 'message', role: (evt.role as 'user' | 'assistant') || 'assistant', content: text, images, created_at: (evt.created_at as string) || '' }
      }
      if (t === 'text_done') {
        const text = (evt.text as string) || ''
        if (!text) return null
        return { id: uid(), kind: 'message', role: 'assistant', content: text, reasoning: actorReasoning, created_at: (evt.created_at as string) || '' }
      }
      if (t === 'tool_call') {
        return { id: uid(), kind: 'tool_call', tool_name: (evt.tool_name as string) || '', arguments: (evt.arguments as Record<string, unknown>) || {}, result: (evt.result as string) || '', is_error: (evt.is_error as boolean) || false, created_at: (evt.created_at as string) || '' }
      }
      if (t === 'observer_text_done') {
        const text = (evt.text as string) || ''
        if (!text) return null
        return { id: uid(), kind: 'observer_message', round_label: (evt.round_label as string) || '', content: text, reasoning: observerReasoning, created_at: (evt.created_at as string) || '' }
      }
      // control_tool_call, daemon_*, task_*, llm_prompt → skip
      return null
    }

    return () => { close() }
  }, [sessionId, close])

  return state
}
