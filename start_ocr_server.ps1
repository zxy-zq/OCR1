param(
    [int]$Port = 8000,
    [int]$Workers = 1,
    [int]$MaxConcurrency = 8,
    [string]$OrtThreads = "auto",
    [string]$DetLimitSideLen = "auto",
    [string]$LogLevel = "info",
    [switch]$NoPreload
)

if ($NoPreload) {
    $env:OCR_PRELOAD = "false"
} else {
    $env:OCR_PRELOAD = "true"
}

$env:OCR_MAX_CONCURRENCY = [string]$MaxConcurrency

if ($OrtThreads -ne "auto") {
    $env:OCR_ORT_INTRA_THREADS = $OrtThreads
    $env:OCR_ORT_INTER_THREADS = "1"
}

if ($DetLimitSideLen -ne "auto") {
    $env:OCR_DET_LIMIT_SIDE_LEN = $DetLimitSideLen
}

.\.venv\Scripts\python.exe -m uvicorn ocr_server:app `
    --host 0.0.0.0 `
    --port $Port `
    --workers $Workers `
    --log-level $LogLevel
