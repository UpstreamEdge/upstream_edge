@echo off
setlocal

echo Running Ruff lint...
python -m ruff check upstream_edge tests
if errorlevel 1 goto :fail

echo.
echo Checking Ruff formatting...
python -m ruff format --check upstream_edge tests
if errorlevel 1 goto :fail

echo.
echo Running Pyright...
python -m pyright
if errorlevel 1 goto :fail

echo.
echo Running pytest...
python -m pytest tests -q
if errorlevel 1 goto :fail

echo.
echo Running doctests...
python -m pytest --doctest-modules upstream_edge -q
if errorlevel 1 goto :fail

echo.
echo All checks passed.
exit /b 0

:fail
echo.
echo Checks failed.
exit /b 1
