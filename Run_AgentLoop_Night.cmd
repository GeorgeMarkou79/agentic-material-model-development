@echo off
rem Agent loop: one night (stops at $20, 15 iterations, 07:30, all pass or the manager decision). See agent_loop\README.md.
cd /d "%~dp0"
python agent_loop\loop_manager.py
pause
