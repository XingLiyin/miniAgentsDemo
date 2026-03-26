"""LLM 用法示例（文档入口）。"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from app.examples.llm_usage_example import main


if __name__ == '__main__':
    main()
