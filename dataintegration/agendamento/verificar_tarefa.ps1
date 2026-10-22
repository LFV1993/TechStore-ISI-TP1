# TechStore - mostra o estado da tarefa agendada e as últimas linhas do registo do job (só leitura).
# Se a tarefa não tiver sido criada (agendamento preparado mas não instalado), indica que não existe.
#   powershell -ExecutionPolicy Bypass -File dataintegration\agendamento\verificar_tarefa.ps1
# LastTaskResult = 0 indica sucesso; 1 falha de configuração/ligação; 2 erro na função verificar_alertas().
# O histórico completo está na tabela alertas_execucoes (origem = 'agendador').

$Nome = "TechStore - Verificar alertas"
$Raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

$tarefa = Get-ScheduledTask -TaskName $Nome -ErrorAction SilentlyContinue
if (-not $tarefa) { Write-Output "A tarefa '$Nome' não existe."; exit 1 }

$tarefa | Select-Object TaskName, State | Format-List
$tarefa.Triggers | ForEach-Object { "Gatilho: " + $_.StartBoundary + "  " + $_.CimClass.CimClassName }
$tarefa | Get-ScheduledTaskInfo | Select-Object LastRunTime, LastTaskResult, NextRunTime, NumberOfMissedRuns | Format-List

$log = Join-Path $Raiz "logs\alertas_job.log"
if (Test-Path $log) { "Últimas linhas de logs\alertas_job.log:"; Get-Content $log -Tail 10 }
else { "Ainda não existe logs\alertas_job.log." }
