@echo off
chcp 65001 > nul
cd /d "%~dp0"

:: Python 설치 확인
python --version > nul 2>&1
if errorlevel 1 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo https://www.python.org/downloads/ 에서 설치 후 다시 실행하세요.
    pause
    exit /b 1
)

:: 가상환경 없으면 생성
if not exist ".venv\Scripts\python.exe" (
    echo [설치] 가상환경 생성 중...
    python -m venv .venv
)

:: 패키지 설치
echo [설치] 패키지 확인 중...
.venv\Scripts\pip install -q -r requirements.txt

:: .env 파일 확인
if not exist ".env" (
    echo.
    echo [경고] .env 파일이 없습니다.
    echo 아래 내용으로 .env 파일을 만들어 API 키를 입력하세요.
    echo.
    echo MOLIT_API_KEY=여기에_국토부_API_키
    echo KAPT_API_KEY=여기에_KAPT_API_키
    echo TELEGRAM_BOT_TOKEN=
    echo TELEGRAM_CHAT_ID=
    echo.
    pause
    exit /b 1
)

:: 실행
echo [실행] APT Radar 시작...
start "" .venv\Scripts\pythonw.exe app.py
