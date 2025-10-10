@echo off
REM ================================================================
REM == Check-in/out Management System Server Starter
REM ================================================================

ECHO.
ECHO  入退室管理システムのサーバーを起動します...
ECHO.

REM このバッチファイルがある場所に移動 (これによりどこから実行してもOKになる)
cd /d %~dp0

REM 仮想環境を有効化
ECHO  [1/2] 仮想環境を有効化しています...
call ..\作成者用_untouchable\venv\Scripts\activate

REM Pythonランチャー(py)を使い、新しいウィンドウを最小化(/min)してサーバーを起動
ECHO  [2/2] サーバーを新しいウィンドウで起動しています...
start "Check-in Server" /min py ..\作成者用_untouchable\py\app.py

ECHO.
ECHO  起動しました。タスクバーに最小化されているウィンドウがサーバー本体です。
ECHO  (このウィンドウは閉じて構いません)
ECHO.

REM 5秒待ってからこのウィンドウを自動で閉じる
timeout /t 1 > nul