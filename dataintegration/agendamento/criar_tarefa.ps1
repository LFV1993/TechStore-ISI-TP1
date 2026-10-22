# TechStore - cria a tarefa agendada que executa a verificação de alertas.
#
# ESTADO: preparado, NÃO instalado. Este script não foi executado neste computador; criar a tarefa
# é uma alteração persistente ao Windows e deve ser feita apenas com autorização de quem o administra.
#
# Utilização (PowerShell do próprio utilizador, sem privilégios de administrador), na raiz do projeto:
#   powershell -ExecutionPolicy Bypass -File dataintegration\agendamento\criar_tarefa.ps1 -ApenasMostrar
#   powershell -ExecutionPolicy Bypass -File dataintegration\agendamento\criar_tarefa.ps1
#   powershell -ExecutionPolicy Bypass -File dataintegration\agendamento\criar_tarefa.ps1 -GatilhoTesteMinutos 3
#   powershell -ExecutionPolicy Bypass -File dataintegration\agendamento\criar_tarefa.ps1 -Substituir
#
# A tarefa:
#   - executa diariamente às 08:00 (hora local do Windows) executar_alertas.py com --origem agendador;
#   - usa o pythonw.exe do ambiente virtual .venv (não abre janela); as credenciais são lidas pelo
#     próprio script a partir do ficheiro .env do projeto e não ficam na definição da tarefa;
#   - corre com o utilizador atual, sem privilégios elevados e apenas com a sessão iniciada
#     (não guarda a password do Windows);
#   - se o computador estiver desligado às 08:00, corre assim que possível (StartWhenAvailable);
#   - também corre com o portátil a funcionar a bateria.
#
# Parâmetros:
#   -ApenasMostrar          mostra a configuração e não cria nada.
#   -GatilhoTesteMinutos N  acrescenta um gatilho único daqui a N minutos (para comprovar a execução automática).
#   -Substituir             necessário para substituir uma tarefa já existente com o mesmo nome
#                           (por exemplo, para retirar o gatilho de teste). Sem ele, o script não altera nada.

param(
    [string]$Hora = "08:00",
    [int]$GatilhoTesteMinutos = 0,
    [switch]$Substituir,
    [switch]$ApenasMostrar
)

$ErrorActionPreference = "Stop"
$Nome = "TechStore - Verificar alertas"
$Raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Python = Join-Path $Raiz ".venv\Scripts\pythonw.exe"
$Script = Join-Path $Raiz "dataintegration\executar_alertas.py"
$Utilizador = "$env:USERDOMAIN\$env:USERNAME"
$Argumentos = "`"$Script`" --origem agendador"

if (-not (Test-Path $Python)) { throw "Ambiente virtual não encontrado: $Python" }
if (-not (Test-Path $Script)) { throw "Script não encontrado: $Script" }
if (-not (Test-Path (Join-Path $Raiz ".env"))) { throw "Ficheiro .env não encontrado na raiz do projeto." }

$teste = $null
if ($GatilhoTesteMinutos -gt 0) { $teste = (Get-Date).AddMinutes($GatilhoTesteMinutos) }

$existente = Get-ScheduledTask -TaskName $Nome -ErrorAction SilentlyContinue

Write-Output "Tarefa:              $Nome"
Write-Output "Utilizador:          $Utilizador (sessão iniciada, sem privilégios elevados, sem password guardada)"
Write-Output "Programa:            $Python"
Write-Output "Argumentos:          $Argumentos"
Write-Output "Pasta de trabalho:   $Raiz"
Write-Output "Credenciais:         lidas de $(Join-Path $Raiz '.env') pelo script (não ficam na tarefa)"
Write-Output "Gatilho diário:      $Hora"
Write-Output ("Gatilho de teste:    " + $(if ($teste) { $teste.ToString("yyyy-MM-dd HH:mm:ss") } else { "nenhum" }))
Write-Output "Definições:          executar assim que possível se falhar a hora; permitir a bateria;"
Write-Output "                     limite de 15 minutos; não iniciar uma segunda instância em simultâneo"
Write-Output ("Tarefa já existe:    " + $(if ($existente) { "sim" } else { "não" }))

if ($ApenasMostrar) { Write-Output "Modo -ApenasMostrar: nada foi criado."; return }

if ($existente -and -not $Substituir) {
    throw "A tarefa '$Nome' já existe. Nada foi alterado. Use -Substituir para a substituir."
}

$acao = New-ScheduledTaskAction -Execute $Python -Argument $Argumentos -WorkingDirectory $Raiz
$gatilhos = @(New-ScheduledTaskTrigger -Daily -At $Hora)
if ($teste) { $gatilhos += New-ScheduledTaskTrigger -Once -At $teste }

$definicoes = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

$principal = New-ScheduledTaskPrincipal -UserId $Utilizador -LogonType Interactive -RunLevel Limited

if ($existente) { Unregister-ScheduledTask -TaskName $Nome -Confirm:$false }
Register-ScheduledTask -TaskName $Nome -Action $acao -Trigger $gatilhos -Settings $definicoes -Principal $principal `
    -Description "TechStore: executa verificar_alertas() no PostgreSQL (executar_alertas.py --origem agendador)." |
    Out-Null

Write-Output "Tarefa criada."
Get-ScheduledTask -TaskName $Nome | Get-ScheduledTaskInfo | Select-Object NextRunTime, LastRunTime, LastTaskResult | Format-List
