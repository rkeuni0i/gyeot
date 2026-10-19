@echo off
chcp 65001 > nul
cd /d "%~dp0"
set PY=C:\Users\2003g\anaconda3\envs\DS\python.exe
if not exist "%PY%" (
  set PY=python
)
if not exist index\mindguide.db (
  echo 자료 저장소를 만드는 중...
  "%PY%" build_index.py
)
"%PY%" -m streamlit run app.py
