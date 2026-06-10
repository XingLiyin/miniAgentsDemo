// MUST be the first import: pdfSetup sets `globalThis.pdfjsLib` via side effect,
// which pdfjs-dist/web/pdf_viewer.mjs destructures at module load time. Placing
// it at the application entry guarantees the global is installed before any
// later import path reaches pdf_viewer.mjs (a second-line defence; PdfViewer.tsx
// also imports pdfSetup before pdf_viewer).
import './preview/viewers/pdf/pdfSetup'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'
import { LanguageProvider } from './i18n'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <LanguageProvider>
      <App />
    </LanguageProvider>
  </StrictMode>,
)
