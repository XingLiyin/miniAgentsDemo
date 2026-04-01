@echo off
chcp 65001 >nul
echo.
echo ==> 启动 miniAgents
echo     后端: http://localhost:8000/docs
echo     前端: http://localhost:5173
echo.

:: 后端：新窗口运行
start "miniAgents Backend" cmd /k "uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"

:: 前端：安装依赖后启动（新窗口）
if not exist "frontend\node_modules" (
    echo  [前端] 首次运行，安装依赖...
    cd frontend && npm install && cd ..
)
start "miniAgents Frontend" cmd /k "cd frontend && npm run dev"

echo 两个窗口已打开，关闭窗口即可停止对应服务。
