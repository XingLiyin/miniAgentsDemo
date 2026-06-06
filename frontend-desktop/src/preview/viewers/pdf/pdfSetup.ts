import * as pdfjsLib from 'pdfjs-dist'
// Vite resolves this to a bundled worker asset URL; the worker version matches
// the main bundle because it's the same npm package (avoids version-mismatch errors).
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

// CMaps are copied to `<base>/cmaps/` by vite-plugin-static-copy (see vite.config.ts).
export const CMAP_URL = `${import.meta.env.BASE_URL}cmaps/`
export const CMAP_PACKED = true

export { pdfjsLib }
