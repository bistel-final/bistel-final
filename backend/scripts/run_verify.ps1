# run_verify.ps1
#
# `.env`에서 POSTGRES_HOST/PORT/USER/DB/PASSWORD를 읽어
# verify_detection_recalculation.py를 한 번에 실행하는 래퍼.
#
# 왜 필요한가: verify_detection_recalculation.py는 팀 공용 `.env`(NEO4J_*,
# N8N_WEBHOOK_URL 등 이 검증과 무관한 값까지 전부 필수로 요구하는
# app.common.config)를 거치지 않고 커맨드라인 인자로 접속 정보를 받는다.
# 그래서 원래는 host/port/user를 손으로 치고 비밀번호를 따로 복사해 넣어야
# 하는데, 이 스크립트는 그 값들을 `.env`에서 대신 읽어 넘겨준다.
#
# 비밀번호는 화면에 출력하지 않는다(01-project-rules.md 1절 금지 5).
#
# 사용법 (backend 폴더에서, venv 활성화한 상태로):
#   .\scripts\run_verify.ps1
#
# 만약 "실행할 수 없습니다(디지털 서명되지 않음)" 같은 오류가 나면 대신 이렇게:
#   powershell -ExecutionPolicy Bypass -File scripts\run_verify.ps1

$ErrorActionPreference = "Stop"

# backend/scripts/run_verify.ps1 기준: 상위 2단계가 저장소 루트(.env 위치),
# 상위 1단계가 backend(requirements.txt 위치)다.
$repoRoot = Join-Path $PSScriptRoot "..\.."
$backendRoot = Join-Path $PSScriptRoot ".."
$envPath = Join-Path $repoRoot ".env"
$requirementsPath = Join-Path $backendRoot "requirements.txt"
$scriptPath = Join-Path $PSScriptRoot "verify_detection_recalculation.py"

if (-not (Test-Path $envPath)) {
    Write-Error ".env 파일을 찾을 수 없습니다: $envPath"
    exit 1
}

function Get-EnvValue {
    param([string]$Name)

    $line = Get-Content $envPath | Where-Object { $_ -match "^$Name=" } | Select-Object -First 1
    if (-not $line) { return $null }

    # "KEY=value          # 주석" 형태에서 값만 뽑고 인라인 주석·공백을 제거한다.
    $value = $line -replace "^$Name=", ""
    $value = $value -replace "\s*#.*$", ""
    return $value.Trim()
}

$dbHost = Get-EnvValue -Name "POSTGRES_HOST"
$dbPort = Get-EnvValue -Name "POSTGRES_PORT"
$dbUser = Get-EnvValue -Name "POSTGRES_USER"
$dbName = Get-EnvValue -Name "POSTGRES_DB"
$dbPassword = Get-EnvValue -Name "POSTGRES_PASSWORD"

$missing = @()
if (-not $dbHost) { $missing += "POSTGRES_HOST" }
if (-not $dbPort) { $missing += "POSTGRES_PORT" }
if (-not $dbUser) { $missing += "POSTGRES_USER" }
if (-not $dbName) { $missing += "POSTGRES_DB" }
if (-not $dbPassword) { $missing += "POSTGRES_PASSWORD" }

if ($missing.Count -gt 0) {
    Write-Error (".env에서 다음 값을 읽지 못했습니다: " + ($missing -join ", "))
    exit 1
}

Write-Host "[setup] pip install -r requirements.txt"
pip install -r $requirementsPath
if ($LASTEXITCODE -ne 0) {
    Write-Error "pip install 실패 (종료 코드 $LASTEXITCODE). 위 출력을 확인하세요."
    exit $LASTEXITCODE
}

# 비밀번호는 이 프로세스의 환경변수로만 넘긴다 — Write-Host로 출력하지 않는다.
$env:FDC_TEST_DB_PASSWORD = $dbPassword

Write-Host "[run] host=$dbHost port=$dbPort db=$dbName user=$dbUser"
python $scriptPath --host $dbHost --port $dbPort --db $dbName --user $dbUser
$exitCode = $LASTEXITCODE

# 다음 명령에서 실수로 남아있지 않도록 정리한다.
Remove-Item Env:\FDC_TEST_DB_PASSWORD -ErrorAction SilentlyContinue

exit $exitCode
