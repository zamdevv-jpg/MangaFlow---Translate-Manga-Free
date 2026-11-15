@echo off
setlocal
cd /d "%~dp0"

rem Jika server sudah berjalan, cukup buka browser.
powershell -NoProfile -WindowStyle Hidden -Command "try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if not errorlevel 1 (
  echo MangaFlow sudah berjalan di http://127.0.0.1:8765
  start "" http://127.0.0.1:8765
  exit /b 0
)

where python >nul 2>nul
if errorlevel 1 (
  echo Python tidak ditemukan. Pasang Python 3.11 atau lebih baru dari python.org.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Membuat virtual environment...
  python -m venv .venv
  if errorlevel 1 goto :error
)

call ".venv\Scripts\activate.bat"
echo Memeriksa dependensi...
python -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto :error

echo.
echo MangaFlow berjalan di http://127.0.0.1:8765
echo Tutup jendela ini untuk menghentikan server.
echo.

rem Tunggu server siap, lalu buka browser.
start "" powershell -NoProfile -WindowStyle Hidden -Command "for ($i=0; $i -lt 60; $i++) { try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep -Seconds 1 } }; Start-Process 'http://127.0.0.1:8765'"

python app.py
goto :eof

:error
echo.
echo Gagal menyiapkan MangaFlow. Periksa pesan di atas.
pause
exit /b 1
