"""PyInstaller 打包入口 — 生产模式下由 exe 直接调用。"""
import sys
import os

# ── 冻结模式（PyInstaller）初始化 ────────────────────────────────────────────
if getattr(sys, "frozen", False):
    _meipass = sys._MEIPASS  # 只读资源目录
    _exe_dir = os.path.dirname(sys.executable)  # exe 所在目录（可写）

    # console=False 时 PyInstaller bootloader 将 sys.stdout/stderr 设为 None，
    # 导致 uvicorn 日志格式化器调用 .isatty() 崩溃。
    # 尝试恢复到实际的文件描述符（Electron 管道），否则 fallback 到 devnull。
    import io as _io
    def _fix_stream(fd: int):
        try:
            return _io.TextIOWrapper(
                _io.FileIO(fd, closefd=False),
                encoding="utf-8", errors="replace", line_buffering=True,
            )
        except Exception:
            return open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = _fix_stream(1)
    if sys.stderr is None:
        sys.stderr = _fix_stream(2)

    # Windows: 将 GTK3 DLL 加入 PATH，供 cairosvg 使用
    _gtk3_bin = os.path.join(_meipass, "gtk3_bin")
    if os.path.isdir(_gtk3_bin):
        os.environ["PATH"] = _gtk3_bin + os.pathsep + os.environ.get("PATH", "")

    # Linux: 确保打包进来的 .so 文件可被 cffi/cairosvg 的 dlopen() 找到。
    # PyInstaller bootloader 已在 C 层设置过 LD_LIBRARY_PATH，这里再显式追加
    # 一次，保证在任何 shell 环境下都生效（对已加载的库无副作用）。
    if sys.platform == "linux":
        os.environ["LD_LIBRARY_PATH"] = (
            _meipass + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
        )

    # 加载 .env：优先使用 IPMASTER_COWORK_ENV_FILE（由 Electron 设置为 AppData 路径），
    # 回退到 exe 同级目录的 .env
    from dotenv import load_dotenv
    _env_file = os.environ.get("IPMASTER_COWORK_ENV_FILE") or os.path.join(_exe_dir, ".env")
    load_dotenv(_env_file)

    # 冻结模式下强制用绝对路径——.env 里的相对路径无法正确解析，直接覆盖
    # 用户若需自定义，必须填绝对路径（绝对路径会通过下面的逻辑保留）
    def _resolve(key: str, frozen_abs: str) -> None:
        val = os.environ.get(key, "")
        if not val or not os.path.isabs(val):
            os.environ[key] = frozen_abs

    _resolve("IPMASTER_COWORK_DATA_DIR",   os.path.join(_exe_dir, "data"))
    _resolve("IPMASTER_COWORK_SKILLS_DIR", os.path.join(_exe_dir, "resources", "skills"))
    _resolve("IPMASTER_COWORK_AGENTS_DIR", os.path.join(_exe_dir, "resources", "agents"))
else:
    from dotenv import load_dotenv
    load_dotenv()
    # 本地开发：读 GTK3_BIN 并加入 PATH，供 cairosvg 在 Windows 上找到 libcairo-2.dll
    if sys.platform == "win32":
        _gtk3_bin = os.environ.get("GTK3_BIN", "")
        if _gtk3_bin and os.path.isdir(_gtk3_bin):
            os.environ["PATH"] = _gtk3_bin + os.pathsep + os.environ.get("PATH", "")

# ── 延迟导入（确保 PATH / 环境变量已就绪再导入 cairosvg 等）───────────────────
from app.main import create_app  # noqa: E402
import uvicorn  # noqa: E402


def main() -> None:
    port = int(os.environ.get("IPMASTER_COWORK_BACKEND_PORT", 15926))
    application = create_app()

    # 挂载前端静态文件
    if getattr(sys, "frozen", False):
        frontend_dist = os.path.join(sys._MEIPASS, "frontend_dist")
    else:
        frontend_dist = os.path.join(os.path.dirname(__file__), "frontend", "dist")

    if os.path.isdir(frontend_dist):
        from starlette.staticfiles import StaticFiles
        from starlette.exceptions import HTTPException as _StarletteHTTPException

        class _SPAFiles(StaticFiles):
            """BrowserRouter SPA 支持：找不到文件时回退到 index.html。"""
            async def get_response(self, path: str, scope):
                try:
                    return await super().get_response(path, scope)
                except _StarletteHTTPException as exc:
                    if exc.status_code == 404:
                        return await super().get_response("index.html", scope)
                    raise

        # 挂载到 "/" 必须在所有 API 路由注册之后
        application.mount("/", _SPAFiles(directory=frontend_dist, html=True), name="frontend")

    print(f"[IPMaster-Cowork] Starting on http://0.0.0.0:{port}")
    uvicorn.run(application, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
