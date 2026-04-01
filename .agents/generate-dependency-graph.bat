@echo off
echo Regenerating FAITH dependency graph from epic.yaml...
where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    py -3 "%~dp0generate_dependency_graph.py"
) else (
    python "%~dp0generate_dependency_graph.py"
)
if %ERRORLEVEL% EQU 0 (
    echo Done.
    if exist "%~dp0dependency-graph.svg" (
        echo Opening dependency-graph.svg...
        start "" "%~dp0dependency-graph.svg"
    )
) else (
    echo Failed to generate graph. Check Python is installed and on PATH.
    pause
)
