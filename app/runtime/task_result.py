"""共享的任务结果渲染：把一个终结 task 的 outputs/process_report/error 拼成
memory 内容（纯文本或含图片的 multimodal list）。

供 task_manager（tracker/sibling 投递、tool_result 聚合）和 agent_loop
（tracker 起始 user message 注入）复用，避免跨模块引用私有符号。
"""
from __future__ import annotations


def task_result_content(prefix: str, outputs: "str | list", process_report: "str | None", error: "str | None") -> "str | list":
    """Build memory content for a finished/failed task, preserving images when outputs is multimodal."""
    if isinstance(outputs, list):
        images = [p for p in outputs if p.get("type") == "image"]
        output_text = next((p.get("text", "") for p in outputs if p.get("type") == "text"), "")
    else:
        images = []
        output_text = outputs or ""
    text_parts = [prefix]
    if output_text:
        text_parts.append(f"# Output\n\n{output_text}")
    if process_report:
        text_parts.append(f"# Process Report\n\n{process_report}")
    if error:
        text_parts.append(f"Error: {error}")
    text = "\n".join(text_parts)
    if images:
        return [*images, {"type": "text", "text": text}]
    return text
