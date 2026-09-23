<#
.SYNOPSIS
  BOO-01 — registra (ou remove) a tarefa agendada que sobe a stack demo no logon.

.DESCRIPTION
  A tarefa roda, no logon do usuário atual, o `infra/scripts/up-at-login.sh` dentro
  da distro WSL do repositório. Esse script espera o Docker Engine responder e chama
  o `up.sh` — o mesmo caminho da subida manual, que é o único que respeita a ordem
  do compose (`depends_on` + health).

  Ela NÃO abre o Docker Desktop (o AutoStart é configuração do dono da máquina): com
  ele desligado, a tarefa espera até 30 min por você abri-lo. Rodar o `up.sh` na mão
  continua valendo a qualquer hora; o `up.sh` tem trava e as duas chamadas não se
  atropelam.

  A distro e o caminho do repositório são deduzidos do local deste script
  (\\wsl.localhost\<distro>\<caminho>); passe -Distro/-RepoPath para sobrescrever.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File register-up-at-login.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File register-up-at-login.ps1 -Unregister
#>
param(
  [string]$Distro,
  [string]$RepoPath,
  [switch]$Unregister,
  [string]$TaskName = "PlugHub - subir a stack demo no logon"
)
$ErrorActionPreference = "Stop"

if ($Unregister) {
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Tarefa removida: $TaskName"
  } else {
    Write-Output "Nenhuma tarefa com o nome '$TaskName' — nada a remover."
  }
  return
}

if (-not $Distro -or -not $RepoPath) {
  # \\wsl.localhost\ubuntu\home\a1\projects\plughub\infra\scripts\windows
  if ($PSScriptRoot -match '^\\\\wsl(?:\.localhost|\$)\\([^\\]+)\\(.+)\\infra\\scripts\\windows$') {
    if (-not $Distro)   { $Distro   = $Matches[1] }
    if (-not $RepoPath) { $RepoPath = '/' + ($Matches[2] -replace '\\', '/') }
  } else {
    throw "Não consegui deduzir distro e caminho de '$PSScriptRoot'. Passe -Distro e -RepoPath."
  }
}

$script = "$RepoPath/infra/scripts/up-at-login.sh"
# `bash <arquivo>` e não `./arquivo`: o git de Windows não preserva o bit +x.
$wslArgs = "-d $Distro -- bash -lc `"bash '$script'`""

# conhost --headless: sem janela de console piscando no logon.
$action   = New-ScheduledTaskAction -Execute "$env:WINDIR\System32\conhost.exe" `
              -Argument "--headless $env:WINDIR\System32\wsl.exe $wslArgs"
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$trigger.Delay = "PT1M"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
              -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
  -Settings $settings -Principal $principal -Force `
  -Description "BOO-01: espera o Docker Engine e roda $script (log em .logs/up-at-login-*.log). Remover: register-up-at-login.ps1 -Unregister" | Out-Null

Write-Output "Tarefa registrada: $TaskName"
Write-Output "  executa : conhost --headless wsl.exe $wslArgs"
Write-Output "  quando  : logon de $env:USERDOMAIN\$env:USERNAME, +1 min"
Write-Output "  remover : powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Unregister"
