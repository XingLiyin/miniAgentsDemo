"""
Agent tool definitions and execution for SubAgent tool-use loop.
Provides: Read, Edit, Write, Bash, AskUserQuestion.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Project root (two levels up from this file: backend/tools/ → project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Allowed base directories for file operations
_ALLOWED_ROOTS = [
    _PROJECT_ROOT / "data" / "projects",
    _PROJECT_ROOT / "skills",
]

# Glob is restricted to the default project KB and skills directory
_GLOB_ALLOWED_ROOTS = [
    _PROJECT_ROOT / "data" / "projects" / "default" / "kb",
    _PROJECT_ROOT / "skills",
]

# Bash command whitelist
_BASH_WHITELIST = {"cat", "ls", "grep", "find", "wc", "head", "tail", "diff"}

# Tools that require user confirmation before execution
_HIGH_RISK_TOOLS = {"Write", "Edit", "Bash", "WriteDataStore", "RunSkillScript"}

# ── Tool JSON schemas (OpenAI function-calling format) ──────────────────────

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "Read",
            "description": "Read the contents of a file. Only files under data/projects/ or skills/ are accessible.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path from project root (e.g. skills/ptn-hld-01-overview/SKILL.md)",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "AskUserQuestion",
            "description": (
                "Pause and ask the user ONE clarifying question with concrete options. "
                "Do NOT bundle multiple questions. MUST provide options to let user choose."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "A single focused question."},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "2-4 concrete choices. Required unless answer is truly free-form (e.g. IP address).",
                    },
                },
                "required": ["question", "options"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ListDataStoreTables",
            "description": "列出所有已导入的结构化数据表及其列信息（来自用户上传的 Excel/CSV/Word 等文件）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "QueryDataStore",
            "description": "对导入的结构化数据表执行 SQL 查询（仅 SELECT）。先用 ListDataStoreTables 了解可用表和列名。",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "SELECT 查询语句，例如 SELECT * FROM 设备清单 LIMIT 10",
                    }
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "WriteDataStore",
            "description": "将 Agent 生成的结构化数据写入 DataStore 表（如统计结果、设计参数、设备规划等）。写入后可通过 QueryDataStore 查询。",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {"type": "string", "description": "目标表名"},
                    "columns": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string", "enum": ["TEXT", "INTEGER", "REAL"]},
                            },
                            "required": ["name", "type"],
                        },
                        "description": "列定义列表",
                    },
                    "rows": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "数据行列表，每行是 {列名: 值} 的对象",
                    },
                    "data_role": {
                        "type": "string",
                        "enum": ["design", "reference"],
                        "description": "数据角色：design=Agent 生成的设计数据（默认），reference=参考数据",
                    },
                },
                "required": ["table_name", "columns", "rows"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "SearchDocBase",
            "description": "在项目文档库中进行语义搜索，返回带评分的匹配段落。比自动注入的文档摘要更精确。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词或短语"},
                    "top_k": {"type": "integer", "description": "返回结果数量（默认5）"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ListDocuments",
            "description": "列出项目中所有已索引的文档及其元数据（标题、分类、格式）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "GetDocSection",
            "description": "读取指定文档中某个章节的完整内容。先用 ListDocuments 或 SearchDocBase 找到文档ID和章节名。",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "文档ID（如 fs:input/设计方案.docx）"},
                    "heading": {"type": "string", "description": "章节标题"},
                },
                "required": ["doc_id", "heading"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Glob",
            "description": "按 glob 模式搜索文件，返回匹配的文件路径列表。仅搜索 data/projects/default/kb/ 目录。用于发现上传的文档和配置文件，如查找某目录下所有 .cfg 或 .xlsx 文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob 模式，例如 **/*.cfg 或 olt/*.xlsx，在 data/projects/default/kb/ 下搜索",
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索起始目录（相对项目根目录），默认搜索所有允许目录",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "RunSkillScript",
            "description": (
                "运行当前 skill 中声明的脚本。只能执行 SKILL.md frontmatter scripts[] 中预定义的脚本，不能执行任意命令。"
                "执行前需要用户确认。输出文件自动保存到 exports 目录。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "script_name": {
                        "type": "string",
                        "description": "脚本名称，必须是当前 skill 的 scripts 列表中的 name 值",
                    },
                    "input_file": {
                        "type": "string",
                        "description": "输入文件路径（相对于项目根目录，如 data/projects/default/uploads/xxx/file.cfg）",
                    },
                    "output_file": {
                        "type": "string",
                        "description": "输出文件名（自动存入 exports 目录下）",
                    },
                    "extra_args": {
                        "type": "object",
                        "description": "可选：覆盖脚本 defaults 中的额外参数（键值对）",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["script_name", "output_file"],
            },
        },
    },
    # Write, Edit, Bash are implemented but not exposed to LLM for now.
    # Re-add to this list when needed.
]


# ── Tool context ─────────────────────────────────────────────────────────────

@dataclass
class ToolContext:
    session_id: str
    step_id: str
    project_id: str
    push_sse: Callable
    skill_meta: dict | None = None  # Full frontmatter from SKILL.md (for RunSkillScript)


# ── Q&A deduplication helper ──────────────────────────────────────────────────

def _question_similarity(q1: str, q2: str) -> float:
    """Character bigram Jaccard similarity. Works well for Chinese text."""
    def _bigrams(s: str) -> set[str]:
        s = s.strip().lower()
        return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else {s}
    bg1, bg2 = _bigrams(q1), _bigrams(q2)
    if not bg1 or not bg2:
        return 0.0
    return len(bg1 & bg2) / len(bg1 | bg2)


# ── Registry ─────────────────────────────────────────────────────────────────

class AgentToolRegistry:
    """Executes agent tool calls with path sandboxing and command whitelisting."""

    async def execute(
        self, name: str, args: dict, ctx: ToolContext, call_id: str | None = None
    ) -> str:
        try:
            if name in _HIGH_RISK_TOOLS and call_id is not None:
                allowed = await self._confirm_tool(name, args, call_id, ctx)
                if not allowed:
                    return f"[Denied] User denied execution of {name}"

            if name == "Read":
                return self._read(args["path"], ctx)
            elif name == "Edit":
                return self._edit(args["path"], args["old_string"], args["new_string"], ctx)
            elif name == "Write":
                return self._write(args["path"], args["content"], ctx)
            elif name == "Bash":
                return self._bash(args["command"], ctx)
            elif name == "AskUserQuestion":
                return await self._ask_user_question(args["question"], ctx, options=args.get("options"))
            elif name == "ListDataStoreTables":
                return self._list_datastore_tables()
            elif name == "QueryDataStore":
                return self._query_datastore(args["sql"])
            elif name == "WriteDataStore":
                return self._write_datastore(
                    args["table_name"], args["columns"], args["rows"],
                    args.get("data_role", "design"),
                )
            elif name == "RunSkillScript":
                return await self._run_skill_script(args, ctx)
            elif name == "Glob":
                return self._glob(args["pattern"], args.get("path", ""), ctx)
            elif name == "SearchDocBase":
                return self._search_docbase(args["query"], args.get("top_k", 5))
            elif name == "ListDocuments":
                return self._list_documents()
            elif name == "GetDocSection":
                return self._get_doc_section(args["doc_id"], args["heading"])
            else:
                return f"[Error] Unknown tool: {name}"
        except PermissionError as exc:
            logger.warning("Tool %s permission denied: %s", name, exc)
            return f"[PermissionError] {exc}"
        except Exception as exc:
            logger.error("Tool %s error: %s", name, exc)
            return f"[Error] {exc}"

    async def _confirm_tool(
        self, name: str, args: dict, call_id: str, ctx: ToolContext
    ) -> bool:
        from backend.tools.tool_confirm_store import create_confirm, pop_result

        key = f"{ctx.session_id}:{call_id}"
        event = create_confirm(key)

        ctx.push_sse(
            ctx.session_id,
            "tool_confirm_request",
            {"step_id": ctx.step_id, "call_id": call_id, "tool": name, "args": args},
        )
        logger.info("Waiting for tool confirm: session=%s call_id=%s tool=%s", ctx.session_id, call_id, name)

        try:
            await asyncio.wait_for(event.wait(), timeout=300)
        except asyncio.TimeoutError:
            pop_result(key)
            logger.warning("Tool confirm timed out, defaulting to deny: call_id=%s", call_id)
            ctx.push_sse(
                ctx.session_id,
                "tool_confirm_resolved",
                {"step_id": ctx.step_id, "call_id": call_id, "allowed": False, "reason": "timeout"},
            )
            return False

        result = pop_result(key)
        logger.info("Tool confirm resolved: call_id=%s allowed=%s", call_id, result)
        return result

    # ── Path helpers ─────────────────────────────────────────────────────────

    def _resolve_safe(self, path: str, ctx: ToolContext) -> Path:
        """
        Resolve path relative to project root and verify it is inside an allowed root.
        Allowed roots: data/projects/  (covers all per-session sub-dirs) and skills/.
        Raises PermissionError if outside.
        """
        resolved = (_PROJECT_ROOT / path).resolve()
        for allowed in _ALLOWED_ROOTS:
            try:
                resolved.relative_to(allowed.resolve())
                return resolved
            except ValueError:
                continue
        raise PermissionError(
            f"Path '{path}' is outside allowed directories. "
            f"Allowed roots: data/projects/ and skills/"
        )

    # ── Tool implementations ─────────────────────────────────────────────────

    def _read(self, path: str, ctx: ToolContext) -> str:
        safe_path = self._resolve_safe(path, ctx)
        if not safe_path.exists():
            return f"[Error] File not found: {path}"
        if safe_path.suffix.lower() in (".xlsx", ".xls"):
            return self._read_excel(safe_path)
        return safe_path.read_text(encoding="utf-8", errors="replace")

    def _read_excel(self, path: Path) -> str:
        try:
            import openpyxl
        except ImportError:
            return "[Error] openpyxl not installed. Run: pip install openpyxl"
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        parts = []
        for sheet in wb.worksheets:
            parts.append(f"### Sheet: {sheet.title}\n")
            rows = list(sheet.iter_rows(values_only=True))
            if not rows:
                continue
            header = [str(c) if c is not None else "" for c in rows[0]]
            parts.append("| " + " | ".join(header) + " |")
            parts.append("| " + " | ".join("---" for _ in header) + " |")
            for row in rows[1:]:
                cells = [str(c) if c is not None else "" for c in row]
                parts.append("| " + " | ".join(cells) + " |")
            parts.append("")
        wb.close()
        return "\n".join(parts)

    def _edit(self, path: str, old_string: str, new_string: str, ctx: ToolContext) -> str:
        safe_path = self._resolve_safe(path, ctx)
        if not safe_path.exists():
            return f"[Error] File not found: {path}"
        content = safe_path.read_text(encoding="utf-8", errors="replace")
        if old_string not in content:
            return f"[Error] old_string not found in {path}"
        safe_path.write_text(content.replace(old_string, new_string, 1), encoding="utf-8")
        return f"[OK] Edited {path}"

    def _write(self, path: str, content: str, ctx: ToolContext) -> str:
        safe_path = self._resolve_safe(path, ctx)
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        safe_path.write_text(content, encoding="utf-8")
        return f"[OK] Written {path} ({len(content)} chars)"

    def _bash(self, command: str, ctx: ToolContext) -> str:
        # Whitelist check: first token of the command must be in the allowed list
        first_token = command.strip().split()[0] if command.strip() else ""
        # Strip leading path separators in case of absolute paths like /usr/bin/grep
        cmd_name = Path(first_token).name
        if cmd_name not in _BASH_WHITELIST:
            raise PermissionError(
                f"Command '{cmd_name}' is not in the allowed list: {sorted(_BASH_WHITELIST)}"
            )
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(_PROJECT_ROOT),
            )
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr]\n{result.stderr}"
            return output or "[No output]"
        except subprocess.TimeoutExpired:
            return "[Error] Command timed out (30s)"

    def _glob(self, pattern: str, path: str, ctx: ToolContext) -> str:
        """Find files matching a glob pattern within allowed directories."""
        if path:
            # Search within a specific subdirectory
            search_root = (_PROJECT_ROOT / path).resolve()
            # Verify it's under a Glob-allowed root
            allowed = False
            for root in _GLOB_ALLOWED_ROOTS:
                try:
                    search_root.relative_to(root.resolve())
                    allowed = True
                    break
                except ValueError:
                    continue
            if not allowed:
                raise PermissionError(
                    f"Path '{path}' is outside Glob-allowed directories. "
                    f"Allowed: data/projects/default/kb/"
                )
            search_dirs = [search_root]
        else:
            search_dirs = [r.resolve() for r in _GLOB_ALLOWED_ROOTS if r.is_dir()]

        matches: list[str] = []
        for search_dir in search_dirs:
            for hit in search_dir.rglob(pattern):
                if hit.is_file():
                    # Return path relative to project root
                    try:
                        rel = hit.relative_to(_PROJECT_ROOT)
                        matches.append(str(rel).replace("\\", "/"))
                    except ValueError:
                        pass
                if len(matches) >= 200:
                    break

        if not matches:
            return "[No matches]"
        matches.sort()
        result = "\n".join(matches)
        if len(matches) >= 200:
            result += "\n[Truncated at 200 results]"
        return result

    async def _ask_user_question(self, question: str, ctx: ToolContext, options: list[str] | None = None) -> str:
        from backend.tools.tool_question_store import create_question, pop_answer
        from backend.core.session import get_session_memory, append_qa_pair

        # Check Q&A memory for a similar prior question
        memory = get_session_memory(ctx.session_id)
        for qa in memory.get("qa_pairs", []):
            if _question_similarity(question, qa["question"]) > 0.6:
                logger.info(
                    "AskUserQuestion: reusing prior answer (sim>0.6) session=%s step=%s q=%r prior_q=%r",
                    ctx.session_id, ctx.step_id, question, qa["question"],
                )
                return qa["answer"]

        key = f"{ctx.session_id}:{ctx.step_id}"
        event = create_question(key)

        # Push SSE event to notify frontend (include options when available)
        payload = {"step_id": ctx.step_id, "question": question}
        if options:
            payload["options"] = options
        ctx.push_sse(ctx.session_id, "tool_question", payload)
        logger.info("AskUserQuestion: session=%s step=%s q=%r options=%r", ctx.session_id, ctx.step_id, question, options)

        # Wait up to 5 minutes for the user's answer
        try:
            await asyncio.wait_for(event.wait(), timeout=300)
        except asyncio.TimeoutError:
            pop_answer(key)
            return "[Timeout] User did not answer within 5 minutes."

        answer = pop_answer(key) or ""
        logger.info("AskUserQuestion answered: session=%s step=%s a=%r", ctx.session_id, ctx.step_id, answer)

        # Persist Q&A pair to session memory for future chapters
        if answer and not answer.startswith("[Timeout]"):
            append_qa_pair(ctx.session_id, ctx.step_id, question, answer)

        return answer

    # ── RunSkillScript tool ─────────────────────────────────────────────

    async def _run_skill_script(self, args: dict, ctx: ToolContext) -> str:
        """
        Execute a script declared in the current skill's frontmatter.
        Only scripts listed in skill_meta["scripts"] are allowed.
        Input files must be under uploads/ or exports/; output goes to exports/.
        """
        from backend.core.project import get_project_dir, DEFAULT_PROJECT_ID

        script_name = args.get("script_name", "")
        input_file = args.get("input_file", "")
        output_file = args.get("output_file", "")
        extra_args = args.get("extra_args", {})

        # 1. Validate: skill_meta must exist and declare scripts
        skill_meta = ctx.skill_meta or {}
        declared_scripts = skill_meta.get("scripts", [])
        if not declared_scripts:
            return "[Error] 当前 skill 未声明任何脚本（frontmatter 中无 scripts 字段）"

        # Find the matching script declaration
        script_def = None
        for s in declared_scripts:
            if s.get("name") == script_name:
                script_def = s
                break
        if script_def is None:
            available = [s.get("name", "") for s in declared_scripts]
            return f"[Error] 脚本 '{script_name}' 未在当前 skill 中声明。可用脚本: {available}"

        # 2. Resolve input file path (must be under data/projects/) — skip if not provided
        input_path = None
        if input_file:
            input_path = (_PROJECT_ROOT / input_file).resolve()
            allowed_data = (_PROJECT_ROOT / "data" / "projects").resolve()
            try:
                input_path.relative_to(allowed_data)
            except ValueError:
                return f"[Error] 输入文件路径 '{input_file}' 不在允许的目录下（data/projects/）"
            if not input_path.exists():
                return f"[Error] 输入文件不存在: {input_file}"

        # 3. Resolve output path (forced into exports/{session_id}/)
        project_id = DEFAULT_PROJECT_ID
        exports_dir = get_project_dir(project_id) / "exports" / ctx.session_id
        exports_dir.mkdir(parents=True, exist_ok=True)
        output_path = (exports_dir / output_file).resolve()
        # Prevent path traversal in output_file
        try:
            output_path.relative_to(exports_dir.resolve())
        except ValueError:
            return f"[Error] 输出文件名 '{output_file}' 包含非法路径"

        # 4. Build command from template
        # Resolve skill directory for the current step
        skill_step_id = skill_meta.get("_step_id", ctx.step_id)
        skill_dir = _PROJECT_ROOT / "skills" / skill_step_id

        def _fwd(p) -> str:
            """Convert path to forward slashes so shlex.split doesn't eat backslashes."""
            return str(p).replace("\\", "/")

        defaults = script_def.get("defaults", {})
        template_vars = {
            "output_file": _fwd(output_path),
            **defaults,
            **extra_args,
        }
        if input_path is not None:
            template_vars["input_file"] = _fwd(input_path)
        # Resolve defaults that are relative paths to skill directory
        for k, v in template_vars.items():
            if isinstance(v, str) and not Path(v).is_absolute():
                candidate = skill_dir / v
                if candidate.exists():
                    template_vars[k] = _fwd(candidate.resolve())

        try:
            cmd = script_def["command"].format(**template_vars)
        except KeyError as exc:
            return f"[Error] 命令模板缺少参数: {exc}。模板: {script_def['command']}"

        # 5. Execute in skill directory — use exec (not shell) to avoid Windows codepage issues
        import shlex, sys as _sys
        logger.info(
            "RunSkillScript: session=%s script=%s cmd=%s cwd=%s",
            ctx.session_id, script_name, cmd, skill_dir,
        )

        try:
            # Split command into argv; on Windows shlex works for simple commands
            try:
                argv = shlex.split(cmd)
            except ValueError:
                argv = cmd.split()

            # Replace leading 'python'/'python3' with the current interpreter
            if argv and argv[0].lower() in ("python", "python3", "py"):
                argv[0] = _sys.executable.replace("\\", "/")

            import os as _os
            proc_env = {**_os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
            completed = await asyncio.wait_for(
                asyncio.to_thread(
                    subprocess.run,
                    argv,
                    cwd=str(skill_dir),
                    capture_output=True,
                    timeout=115,
                    env=proc_env,
                ),
                timeout=120,
            )
        except asyncio.TimeoutError:
            return f"[Error] 脚本执行超时（120秒）: {script_name}"
        except Exception as exc:
            return f"[Error] 无法启动脚本进程: {exc!r}"

        stdout_text = (completed.stdout or b"").decode("utf-8", errors="replace").strip()
        stderr_text = (completed.stderr or b"").decode("utf-8", errors="replace").strip()

        if completed.returncode != 0:
            parts = [f"[Error] 脚本执行失败 (exit {completed.returncode})"]
            if stderr_text:
                parts.append(f"stderr:\n{stderr_text[:1500]}")
            if stdout_text:
                parts.append(f"stdout:\n{stdout_text[:500]}")
            return "\n".join(parts)

        # 6. Return success with output info
        # Give LLM the full relative path so it can pass it as input_file to the next script
        output_rel = str(output_path.relative_to(_PROJECT_ROOT)).replace("\\", "/")
        result_parts = [
            f"脚本 '{script_name}' 执行成功。",
            f"输出文件完整路径（可直接作为下一个脚本的 input_file）: {output_rel}",
        ]
        if stdout_text:
            result_parts.append(f"标准输出:\n{stdout_text[:1500]}")
        return "\n".join(result_parts)

    # ── DataStore tools ──────────────────────────────────────────────────

    def _list_datastore_tables(self) -> str:
        """List all imported structured data tables."""
        import json
        from backend.core.project_data import get_project_data
        pd = get_project_data()
        tables = pd.datastore.list_tables()
        if not tables:
            return "No tables imported yet."
        summary = []
        for t in tables:
            cols = ", ".join(t.get("column_names", []))
            summary.append(f"- {t['table_name']} ({t['row_count']}行): {cols}")
        return "\n".join(summary)

    def _query_datastore(self, sql: str) -> str:
        """Execute a SELECT query on the DataStore."""
        import json
        from backend.core.project_data import get_project_data
        pd = get_project_data()
        try:
            result = pd.datastore.execute_query(sql)
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as exc:
            return f"[Error] {exc}"

    def _write_datastore(
        self, table_name: str, columns: list, rows: list, data_role: str = "design"
    ) -> str:
        """Write agent-generated structured data to DataStore via import_raw."""
        import json
        from nda_datastore.datastore import import_raw
        from backend.core.project_data import get_project_data

        # LLM may pass columns/rows as JSON strings (XML fallback path) — parse them
        if isinstance(columns, str):
            columns = json.loads(columns)
        if isinstance(rows, str):
            rows = json.loads(rows)

        pd = get_project_data()
        headers = [c["name"] if isinstance(c, dict) else c for c in columns]
        row_lists = [
            [row.get(h, "") if isinstance(row, dict) else "" for h in headers]
            for row in rows
        ]
        result = import_raw(
            pd.datastore, table_name, headers, row_lists,
            source_file="agent-generated",
            data_role=data_role or "design",
        )
        return json.dumps(result, ensure_ascii=False, default=str)

    # ── DocBase tools ────────────────────────────────────────────────────

    def _search_docbase(self, query: str, top_k: int = 5) -> str:
        """Structured semantic search in project documents."""
        import json
        from backend.core.project_data import get_project_data
        pd = get_project_data()
        hits = pd.docbase.query(query, top_k=top_k)
        if not hits:
            return "No results found."
        results = []
        for h in hits:
            results.append({
                "source": h.get("source", ""),
                "heading": h.get("heading", ""),
                "text": h.get("text", "")[:500],
                "score": round(h.get("score", 0), 3),
            })
        return json.dumps(results, ensure_ascii=False)

    def _list_documents(self) -> str:
        """List all indexed documents with metadata."""
        import json
        from backend.core.project_data import get_project_data
        pd = get_project_data()
        docs = pd.docbase.list_documents()
        if not docs:
            return "No documents indexed yet."
        return json.dumps(docs, ensure_ascii=False, default=str)

    def _get_doc_section(self, doc_id: str, heading: str) -> str:
        """Read a specific section from a document."""
        from backend.core.project_data import get_project_data
        pd = get_project_data()
        try:
            text = pd.docbase.get_section_text(doc_id, heading)
            return text if text else f"[Not found] Section '{heading}' in document '{doc_id}'"
        except Exception as exc:
            return f"[Error] {exc}"
