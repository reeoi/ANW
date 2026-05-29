@echo off
setlocal
chcp 65001 > nul

cd /d "%~dp0"
echo ========================================
echo  ANP Local Studio 一键启动
echo ========================================

set "FORCE_INSTALL="
if /i "%~1"=="--reinstall" set "FORCE_INSTALL=1"

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 没找到 python。请先安装 Python 3.11+ 并加入 PATH。
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/5] 创建虚拟环境 .venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo [错误] 创建虚拟环境失败。
    pause
    exit /b 1
  )
) else (
  echo [1/5] 已存在虚拟环境 .venv
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
  echo [错误] 激活虚拟环境失败。
  pause
  exit /b 1
)

if exist ".venv\.installed" if not defined FORCE_INSTALL (
  echo [2/5] 跳过依赖安装（已就绪，使用 --reinstall 强制重装）
  goto :after_install
)

echo [2/5] 升级 pip 并安装依赖 ...
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
  echo [错误] 依赖安装失败。
  pause
  exit /b 1
)
echo installed > ".venv\.installed"

:after_install

echo [3/5] .env 由 Python config_loader 自动加载，跳过

echo [4/5] 初始化目录和数据库 ...
if not exist data mkdir data
if not exist logs mkdir logs
if not exist logs\screenshots mkdir logs\screenshots
python -c "from config_loader import load_from_environment; from review_queue.db import initialize_database; print('数据库:', initialize_database(load_from_environment()))"
if errorlevel 1 (
  echo [错误] 初始化数据库失败。请检查 config.yaml。
  pause
  exit /b 1
)

set ANP_REVIEW_HOST=127.0.0.1
if "%ANP_REVIEW_PORT%"=="" set ANP_REVIEW_PORT=18000

echo [4.5/5] 释放端口 %ANP_REVIEW_PORT% ...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr "LISTENING" ^| findstr /C:":%ANP_REVIEW_PORT% "') do (
  echo   占用进程 PID=%%P，正在停止 ...
  taskkill /F /PID %%P >nul 2>&1
  if errorlevel 1 (
    echo   [警告] 无法停止 PID %%P（权限不足或已退出）
  ) else (
    echo   已停止 PID %%P
    set "_KILLED=1"
  )
)
if "%_KILLED%"=="1" (
  ping -n 2 127.0.0.1 >nul 2>&1
) else (
  echo   端口 %ANP_REVIEW_PORT% 当前空闲。
)

echo [5/5] 启动 ANP Local Studio (托盘模式) ...
echo.
echo 访问地址: http://%ANP_REVIEW_HOST%:%ANP_REVIEW_PORT%
echo 任务栏右下角将出现 ANP 图标; 双击图标重新打开界面;
echo 右键图标可启停全自动 / 重启服务 / 退出。
echo.

if not exist ".venv\Scripts\pythonw.exe" goto :no_pythonw

start "" /B ".venv\Scripts\pythonw.exe" "tray_app.py" --host %ANP_REVIEW_HOST% --port %ANP_REVIEW_PORT%
echo 已发起托盘启动，正在等待服务可达，最多 15 秒...

set "_OK="
set /a _ATTEMPT=0

:probe_loop
if "%_OK%"=="1" goto :probe_done
if %_ATTEMPT% GEQ 15 goto :probe_done
set /a _ATTEMPT+=1
ping -n 2 127.0.0.1 >nul 2>&1
curl -sf -o nul http://%ANP_REVIEW_HOST%:%ANP_REVIEW_PORT%/api/health 2>nul && set "_OK=1"
goto :probe_loop

:probe_done
if "%_OK%"=="1" goto :probe_ok

echo.
echo [警告] 服务在 15 秒内未可达。请检查日志:
echo     托盘启动日志: logs\tray.log
echo     uvicorn 日志: logs\uvicorn.log
echo     应用日志:    logs\anp.log
echo.
echo 常见原因:
echo  - 端口被其它程序占用 [已尝试在第 4.5 步释放]
echo  - 依赖未安装完整 [用 start_anp.bat --reinstall 重装]
echo  - 防火墙拦截了本机 127.0.0.1 连接
pause
goto :end

:probe_ok
echo [OK] ANP 已在托盘后台启动；浏览器应自动打开。
echo 如果浏览器没打开，请手动访问: http://%ANP_REVIEW_HOST%:%ANP_REVIEW_PORT%
goto :end

:no_pythonw
echo [警告] 未找到 .venv\Scripts\pythonw.exe，回退到前台模式。
python -m review_queue.human_review --host %ANP_REVIEW_HOST% --port %ANP_REVIEW_PORT%

:end
endlocal
