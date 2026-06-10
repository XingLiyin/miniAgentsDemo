# IPMaster Desktop 文件渲染能力升级 — 设计文档

- **日期**: 2026-06-02
- **目标版本线**: 0.2.x（从当前 0.1.x 基线起跳）
- **状态**: 设计已确认，待写实施计划

---

## 1. 背景与目标

IPMaster Desktop 当前的文件预览能力集中在单个组件
[`frontend-desktop/src/components/FilePreviewModal.tsx`](../../../frontend-desktop/src/components/FilePreviewModal.tsx)：
由 `WorkspacePanel` 点击文件触发弹窗，前端在浏览器主线程里用 mammoth / xlsx / react-markdown
做转换并渲染。

现状的主要短板：

- **格式缺口**：PDF、PPTX 完全打不开（落到 "不支持" 兜底）。
- **DOCX 丢格式**：mammoth 转 HTML 会丢失排版、图片、样式。
- **代码语言窄**：react-syntax-highlighter 手动注册了 14 种语言，扩展成本高。
- **无统一工具栏**：除关闭外几乎没有交互（不能搜索 / 缩放 / 翻页 / 复制 / 下载）。
- **大文件卡顿**：所有解析都在主线程内存里跑，几十 MB 的文件会卡死 UI；无进度、无缓存。

参考兄弟项目 `D:\20_code\NetworkIntegrationDesign`（VS Code 扩展，下称 **NID**），其渲染方案显著成熟：
pdfjs-dist（PDF）、docx-preview（DOCX 高保真）、完整的 PPTX OOXML 解析器、highlight.js、
统一 toolbar 组件。本次升级**借鉴并移植 NID 的 JS 解析逻辑**。

### 目标
把 `FilePreviewModal` 从"一堆并列 viewer"重构成 **预览宿主 + 工具栏插槽 + Worker 解析管线** 的平台，
补齐 PDF/PPTX、升级 DOCX/代码高亮，并把重解析移出主线程。

---

## 2. 架构决策

**前端解析 + Web Worker 卸载 + 移植 NID 的 JS 解析器（jszip / @xmldom/xmldom）。**

- **不改动 Python 后端**，不增加 PyInstaller 打包负担。后端继续只做文件字节流搬运：
  - `GET /api/v1/workspace/file`（文本 JSON，限 1MB）
  - `GET /api/v1/workspace/file/raw`（二进制流，限 50MB）
- 理由：应用本就是 Electron 前端打包；后端引入 python-pptx / pymupdf 等重库会让 PyInstaller 包显著变大
  并引入打包坑。NID 的解析器是 TS/JS，可几乎原样移植到 Worker，零后端改动。

### Worker / 主线程的职责划分（重要细节）

不是所有解析都能进 Worker —— **能否进 Worker 取决于该库是否依赖 DOM**：

| 格式 | 库 | 跑在哪 | 原因 |
|------|----|--------|------|
| PPTX | NID OOXML 解析器（jszip + @xmldom/xmldom） | **通用解析 Worker** | 纯数据转换，无 DOM 依赖 |
| XLSX/CSV | SheetJS (xlsx) | **通用解析 Worker** | 纯数据转换 |
| DOCX（高保真） | docx-preview | **主线程** | docx-preview 直接渲染进 DOM 节点，无法在 Worker 运行 |
| PDF | pdfjs-dist | **pdfjs 自带的专用 Worker** | pdfjs 自己管理 worker，不走通用管线 |
| Markdown / 代码 / 文本 / 图片 | react-markdown / highlight.js / 原生 | 主线程 | 体量小，无需卸载 |

DOCX 高保真用 docx-preview 必须在主线程（它是渲染器不是纯解析器）。缓解手段：docx-preview 支持
增量渲染 + 进度回调；保留快速路径的可能性（后续如需可加 mammoth-in-worker 的"纯文本快速预览"模式，
本次不做）。

---

## 3. 模块设计（第 1 期平台）

### 3.1 通用解析 Worker 管线

**文件**（计划）：`frontend-desktop/src/preview/worker/fileParser.worker.ts` + `parseClient.ts`

Worker 接收 ArrayBuffer，按 kind 分发到对应解析器，上报进度并返回结构化数据：

```ts
type ParseKind = 'pptx' | 'xlsx'   // DOCX/PDF 不走此管线（见 2 节）

interface ParseProgress { phase: string; loaded?: number; total?: number }
interface ParseRequest  { id: string; kind: ParseKind; buffer: ArrayBuffer; options?: unknown }
interface ParseResult   { id: string; kind: ParseKind; data: unknown }   // data 形状按 kind 定义
interface ParseError    { id: string; error: string }
```

主线程客户端（隐藏 postMessage 细节）：

```ts
function parseInWorker(
  kind: ParseKind,
  buffer: ArrayBuffer,
  opts?: { onProgress?: (p: ParseProgress) => void; signal?: AbortSignal }
): Promise<unknown>   // 返回该 kind 的 data
```

- 单例 Worker，按 `id` 关联请求/响应。
- 支持 `AbortSignal`（弹窗关闭即取消，避免无效解析占用 CPU）。
- 解析器以 `kind → parser` 注册，便于后续扩展。

### 3.2 统一预览外壳

`FilePreviewModal` 重构为宿主，分四区：**Header / Toolbar / Content / Status(进度·错误)**。

- **Viewer 注册表**：`fileType → { component, defaultCapabilities }`。
- `fileType()` 扩展枚举：
  `'image' | 'markdown' | 'docx' | 'excel' | 'code' | 'text' | 'pdf' | 'pptx' | 'binary'`。

### 3.3 工具栏能力注册（Toolbar capability registry）

工具栏只渲染"当前 viewer 声明支持"的控件。viewer 通过 context hook 上报能力，宿主据此渲染：

```ts
interface ViewerCapabilities {
  search?:   { run(q: string): void; next(): void; prev(): void; clear(): void; count?: number }
  zoom?:     { in(): void; out(): void; reset(): void; fit(): void; scale: number }
  pages?:    { count: number; current: number; goto(n: number): void }
  toc?:      { items: { id: string; label: string; level?: number }[]; goto(id: string): void }
  download?: { url: string; filename: string }   // 宿主默认用 raw url 兜底，viewer 可覆盖
  copy?:     () => string                          // 返回要复制的文本
}

// viewer 内部调用，向宿主登记/更新自己的能力
function usePreviewToolbar(): (caps: ViewerCapabilities) => void
```

工具栏根据 `caps` 中存在的键渲染对应按钮组。`download` 对所有可由 raw url 取得的文件默认可用。

### 3.4 第 1 期顺带完成的现有 viewer 升级

迁移现有 viewer 到新外壳时顺手做（因为正在改这些组件）：

- **代码高亮换 highlight.js**：放弃手动注册 14 种语言，改用 highlight.js（自动覆盖全语言）。
  与 Markdown 代码块统一走 `rehype-highlight`（基于 lowlight/highlight.js），消除两套高亮逻辑。
- **图片缩放/平移**：ImageViewer 支持滚轮缩放、拖拽平移、适应窗口（接 `zoom` 能力）。

---

## 4. 实施分期

每期结束都是**可独立发布**的状态。版本映射见第 5 节。

### 第 1 期 · 地基平台 → `0.2.0`
- Web Worker 通用解析管线（结构化结果 + 进度 + 取消）。
- 统一预览外壳 + 工具栏能力注册（搜索/缩放/翻页/TOC/下载/复制插槽）。
- 迁移现有 viewer（image / text / code / markdown / 现有 docx·xlsx）到新外壳。
- 顺带：代码高亮换 highlight.js；图片缩放/平移。
- **交付**：界面有工具栏、图片可缩放、代码全语言高亮、解析不卡——可见提升，并验证 Worker+外壳 API。

### 第 2 期 · 重型格式 → `0.2.1`
都跑在第 1 期的管线 + 外壳上。

- **PDF**（pdfjs-dist）：分页渲染，接翻页/缩放/搜索（文本层）。**第一个 vertical slice**——pdfjs 自带 worker、最自包含。
- **DOCX 高保真**（docx-preview，主线程增量渲染 + 进度）：保留排版/图片/样式，替换现有 mammoth 路径。
- **PPTX**（移植 NID OOXML 解析器，跑在通用 Worker）：theme/master/layout 链解析 → 幻灯片 HTML 卡片，TOC = 幻灯片列表。工作量最大，放在管线/外壳验证稳之后。
- **交付**：PDF/PPTX 从打不开到能看，DOCX 不再丢格式。

### 第 3 期 · 增强打磨 → `0.2.2`
- **Markdown 增强**：代码块高亮 + 复制按钮（**不含 KaTeX / Mermaid**）。
- **Excel 增强**：排序 / 筛选 / 冻结表头（**不含可编辑**）。
- **虚拟滚动**：超大文本 / 大表格不卡。
- **工具栏收尾**：下载/复制在所有类型上行为一致。
- **交付**：体验全面对齐并超过 NID。

---

## 5. 版本策略（方案 A）

整条渲染升级线归到 **0.2.x**，分期滚动发布：

| 版本 | 内容 |
|------|------|
| `0.2.0` | 第 1 期 · 地基平台 |
| `0.2.1` | 第 2 期 · 重型格式 |
| `0.2.2` | 第 3 期 · 增强打磨 |

下一个大主题再升 `0.3.0`。语义化依据：0.x 阶段新增向后兼容功能升 minor，本次作为一条特性线用 patch 滚动发布，
用户认知清晰。

---

## 6. 新增依赖（frontend-desktop）

- `pdfjs-dist` — PDF 渲染
- `docx-preview` — DOCX 高保真
- `highlight.js` + `rehype-highlight`（含 lowlight）— 统一代码高亮
- `jszip` + `@xmldom/xmldom` — PPTX OOXML 解析（移植 NID 所需）

**移除/弃用**：`react-syntax-highlighter`（被 highlight.js 取代，迁移完成后移除）。
保留：`mammoth`（如需快速文本预览回退）、`xlsx`、`react-markdown` + `remark-gfm`。

从 NID 移植的代码：PPTX 解析逻辑（`pptxViewerPanel.ts` 的 shape/theme 提取部分，剥离 VS Code 依赖，
改为纯数据输出供 Worker 调用）；toolbar 控件以 React 重新实现（不直接搬 vanilla JS）。

---

## 7. 错误处理

- 沿用现有 `fetchOrThrow`（把 FastAPI `detail` 提取进错误消息，避免 mammoth/xlsx 把错误体当二进制）。
- Worker 解析失败 → `ParseError` 回主线程 → 外壳 Status 区显示友好错误。
- 超限文件（>1MB 文本 / >50MB 二进制）：后端 413，前端展示明确提示。
- 解析中关闭弹窗 → `AbortSignal` 取消，释放 Worker。

---

## 8. 测试策略

- **单元测试**：Worker 客户端（`parseInWorker` 的请求/响应/取消）、PPTX/XLSX 解析器（用小样本文件断言结构化输出）、工具栏能力注册逻辑。
- **手动验证**：可视化 viewer（PDF/DOCX/PPTX/图片缩放）在真实应用内逐格式打开验证（含大文件不卡、进度可见）。
- 准备一组样本文件（各格式 + 一个大文件）作为回归基线。

---

## 9. 计入遗留问题（本次不做）

- **KaTeX** 数学公式渲染
- **Mermaid** 图表渲染
- **Excel 可编辑**（in-place 编辑 + 回写磁盘）
- （潜在）解析结果的后端/磁盘缓存 —— 当前走 Worker 不做缓存，若后续大文件重复打开成为痛点再评估
- （潜在）DOCX mammoth-in-worker 快速文本预览模式

---

## 10. 关键文件参考

**IPMaster（改造目标）**
- `frontend-desktop/src/components/FilePreviewModal.tsx` — 重构为预览宿主
- `frontend-desktop/src/components/WorkspacePanel.tsx` — 触发入口
- `frontend-desktop/src/components/ChatPanel.tsx` — 聊天内图片附件（本次不重点改）
- `app/api/v1/routes/workspace.py` — 后端文件接口（不改）

**NID（借鉴来源，`D:\20_code\NetworkIntegrationDesign`）**
- `src/.../pptxViewerPanel.ts` — PPTX OOXML 解析（移植核心）
- `src/.../documentReader.ts` — pdfjs / docx-preview 初始化参考
- `src/.../docxEditorPanel.ts` — mammoth 配置 + TOC 提取参考
- `src/.../toolbarWidgets.ts` — 工具栏控件参考（React 重写）
- `src/.../agentChatHtml.ts` — marked + highlight.js 集成参考
