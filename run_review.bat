@echo off
chcp 65001 > nul
cd /d "%~dp0"
rem 학기별 정기 점검(점검 시기가 아니면 바로 끝남). 작업 스케줄러에 등록해 두면 앱을 열지 않아도 점검된다.
if not exist logs mkdir logs
set PY=C:\Users\2003g\anaconda3\envs\DS\python.exe
if not exist "%PY%" (
  set PY=python
)
"%PY%" doc_agent\periodic_review.py >> logs\periodic_review.log 2>&1
