import type {
  ParseKind, ParseProgress, ParseRequestMsg, ParseResultData, WorkerOutMsg,
} from './protocol'

export interface WorkerLike {
  postMessage(msg: unknown, transfer?: Transferable[]): void
  onmessage: ((ev: MessageEvent<WorkerOutMsg>) => void) | null
  terminate(): void
}

interface Pending {
  resolve: (v: unknown) => void
  reject: (e: Error) => void
  onProgress?: (p: ParseProgress) => void
}

function defaultFactory(): WorkerLike {
  return new Worker(new URL('./fileParser.worker.ts', import.meta.url), { type: 'module' }) as unknown as WorkerLike
}

let _factory: () => WorkerLike = defaultFactory
let _worker: WorkerLike | null = null
let _seq = 0
const _pending = new Map<string, Pending>()

// Test seam: swap the worker factory (pass null to restore the default and drop the worker).
export function __setWorkerFactory(f: (() => WorkerLike) | null): void {
  _factory = f ?? defaultFactory
  if (_worker) { _worker.terminate(); _worker = null }
  _pending.clear()
}

function ensureWorker(): WorkerLike {
  if (_worker) return _worker
  const w = _factory()
  w.onmessage = (ev) => {
    const msg = ev.data
    const p = _pending.get(msg.id)
    if (!p) return
    if (msg.type === 'progress') { p.onProgress?.(msg.progress); return }
    _pending.delete(msg.id)
    if (msg.type === 'result') p.resolve(msg.data)
    else if (msg.type === 'error') p.reject(new Error(msg.error))
  }
  _worker = w
  return w
}

export function parseInWorker<K extends ParseKind>(
  kind: K,
  buffer: ArrayBuffer,
  opts?: { onProgress?: (p: ParseProgress) => void; signal?: AbortSignal },
): Promise<ParseResultData[K]> {
  return new Promise<ParseResultData[K]>((resolve, reject) => {
    if (opts?.signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'))
      return
    }
    const id = `p${++_seq}`
    const w = ensureWorker()
    _pending.set(id, {
      resolve: resolve as (v: unknown) => void,
      reject,
      onProgress: opts?.onProgress,
    })
    opts?.signal?.addEventListener('abort', () => {
      if (_pending.delete(id)) reject(new DOMException('Aborted', 'AbortError'))
    }, { once: true })
    const req: ParseRequestMsg = { type: 'parse', id, kind, buffer, options: {} }
    w.postMessage(req, [buffer])
  })
}
