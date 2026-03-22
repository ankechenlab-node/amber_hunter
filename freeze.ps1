# Amber-Hunter Freeze Trigger (Windows PowerShell)
# Usage: .\freeze.ps1
# Requires: amber-hunter running on localhost:18998

$CONFIG_PATH = "$env:USERPROFILE\.amber-hunter\config.json"
$API_KEY = $null

if (Test-Path $CONFIG_PATH) {
    $cfg = Get-Content $CONFIG_PATH | ConvertFrom-Json
    $API_KEY = $cfg.api_key
}

if (-not $API_KEY) {
    Write-Host "❌ 未找到 API Key，请先在 huper.org/dashboard 生成并写入 config.json"
    exit 1
}

try {
    $uri = "http://localhost:18998/freeze?token=[$API_KEY]"
    $resp = Invoke-WebRequest -Uri $uri -Method GET -ContentType "application/json" -TimeoutSec 5
    $data = $resp.Content | ConvertFrom-Json

    Write-Host "🌙 Amber-Hunter Freeze"
    Write-Host "=========================================="
    Write-Host "Session: $($data.session_key)"
    Write-Host ""
    Write-Host "[预填内容]"
    $content = $data.prefill
    if ($content) {
        if ($content.Length -gt 300) {
            Write-Host $content.Substring(0, 300) "..."
        } else {
            Write-Host $content
        }
    } else {
        Write-Host "（无内容）"
    }
    Write-Host ""
    Write-Host "[最近文件]"
    if ($data.files.Count -gt 0) {
        $data.files | Select-Object -First 5 | ForEach-Object {
            Write-Host "  📄 $($_.path) ($($_.size_kb)KB)"
        }
    } else {
        Write-Host "  （无文件）"
    }
    Write-Host ""
    Write-Host "✅ 前往 https://huper.org 点击「冻结当下」查看详情"
} catch {
    Write-Host "❌ 无法连接 amber-hunter（localhost:18998）"
    Write-Host "   请确认服务已启动：python3 $env:USERPROFILE\.openclaw\skills\amber-hunter\amber_hunter.py"
}
