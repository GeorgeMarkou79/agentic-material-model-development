@echo off
rem Agent loop: dry check of the set-up (no API call, no build, no analysis).
cd /d "%~dp0"
python agent_loop\loop_manager.py --check
pause
