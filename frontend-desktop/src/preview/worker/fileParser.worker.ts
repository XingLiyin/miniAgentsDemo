import type { ParseRequestMsg, WorkerOutMsg } from './protocol'
import { parseXlsx, type XlsxParseOptions } from './parsers/xlsx'
import { parsePptx } from './parsers/pptx'

// Minimal local view of the dedicated-worker global scope. Avoids pulling in the
// WebWorker lib (which conflicts with the DOM lib used app-wide).
const ctx = self as unknown as {
  onmessage: ((ev: MessageEvent<ParseRequestMsg>) => void) | null
  postMessage(msg: WorkerOutMsg): void
}

ctx.onmessage = async (ev) => {
  const msg = ev.data
  if (msg?.type !== 'parse') return
  try {
    post({ type: 'progress', id: msg.id, progress: { phase: 'parsing' } })
    const data = await dispatch(msg)
    post({ type: 'result', id: msg.id, kind: msg.kind, data })
  } catch (e) {
    post({ type: 'error', id: msg.id, error: e instanceof Error ? e.message : String(e) })
  }
}

function post(m: WorkerOutMsg) { ctx.postMessage(m) }

async function dispatch(msg: ParseRequestMsg): Promise<unknown> {
  switch (msg.kind) {
    case 'xlsx': return parseXlsx(msg.buffer, (msg.options ?? {}) as XlsxParseOptions)
    case 'pptx': {
      // Stream each slide back as a progress message the moment it's parsed,
      // so the viewer can render the first page within ~1s instead of waiting
      // for the entire deck. The final 'result' message confirms completion.
      const result = await parsePptx(
        msg.buffer,
        (slide, idx, total) => {
          post({
            type: 'progress',
            id: msg.id,
            progress: { phase: 'pptx-slide', loaded: idx + 1, total, slide, slideIdx: idx },
          })
        },
        (diag) => {
          // Forward diagnostic messages from the parser to the main thread,
          // where they get console.logged. The worker's own console output
          // is filtered out by default in Electron's DevTools.
          post({
            type: 'progress',
            id: msg.id,
            progress: { phase: 'pptx-diag', diag },
          })
        },
      )
      // themeFonts is a Map<string,string>; protocol declares it as
      // Record<string,string>. Convert before postMessage so consumers
      // get the documented shape.
      return {
        slides: result.slides,
        themeFonts: Object.fromEntries(result.themeFonts),
      }
    }
    default: {
      // Compile-time exhaustiveness: adding a ParseKind without a case here is a TS error.
      const _never: never = msg.kind
      throw new Error(`Unsupported parse kind: ${String(_never)}`)
    }
  }
}
