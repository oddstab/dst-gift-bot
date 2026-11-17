<#
.SYNOPSIS
    把禮物機器人註冊成 Windows 工作排程，登入時自動常駐。

.DESCRIPTION
    用 pythonw.exe 執行 dst_gift_bot.py（不會跳黑色視窗），登入後延遲一小段時間啟動，
    讓 Steam 有時間連上。程序死掉會自動重啟，且不設執行時間上限。

.PARAMETER Uninstall
    移除排程，而不是建立。

.PARAMETER TaskName
    排程名稱，預設 "Don't Starve Gift Bot"。

.PARAMETER DelaySeconds
    登入後延遲幾秒才啟動，預設 30 秒。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install-task.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install-task.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [switch]$Uninstall,
    [string]$TaskName = "Don't Starve Gift Bot",
    [int]$DelaySeconds = 30
)

$ErrorActionPreference = 'Stop'

if ($Uninstall) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $existing) {
        Write-Host "找不到排程「$TaskName」，沒有東西要移除。"
        return
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "已移除排程「$TaskName」。"
    return
}

# --- 找 pythonw.exe（python.exe 的無視窗版本）---
$python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $python) {
    throw "PATH 上找不到 python.exe。請先安裝 Python 3.8+ 並勾選 Add to PATH。"
}
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path $pythonw)) {
    throw "在 $python 旁邊找不到 pythonw.exe。請重新安裝 Python（含 py launcher）。"
}

# --- 找機器人本體 ---
$script = Join-Path $PSScriptRoot 'dst_gift_bot.py'
if (-not (Test-Path $script)) {
    throw "找不到 $script。請把這個腳本放在 dst_gift_bot.py 同一個資料夾。"
}

$timer = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$timer.Delay = "PT${DelaySeconds}S"

$action = New-ScheduledTaskAction `
    -Execute $pythonw `
    -Argument ('"{0}"' -f $script) `
    -WorkingDirectory $PSScriptRoot

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $timer `
    -Settings $settings `
    -Description "Don't Starve Together 禮物機器人：不開遊戲，每 6 分鐘送一次心跳。" `
    -Force | Out-Null

Write-Host "已建立排程「$TaskName」"
Write-Host "  執行檔：$pythonw"
Write-Host "  腳本  ：$script"
Write-Host "  觸發  ：登入後 ${DelaySeconds} 秒"
Write-Host ""
Write-Host "常用指令："
Write-Host "  Start-ScheduledTask -TaskName `"$TaskName`"        # 現在就跑"
Write-Host "  Stop-ScheduledTask  -TaskName `"$TaskName`"        # 停止"
Write-Host "  Get-ScheduledTask   -TaskName `"$TaskName`"        # 看狀態"
Write-Host "  Get-Content '$PSScriptRoot\dst_gift_bot.log' -Tail 20"
Write-Host ""
Write-Host "要移除：powershell -ExecutionPolicy Bypass -File .\install-task.ps1 -Uninstall"
