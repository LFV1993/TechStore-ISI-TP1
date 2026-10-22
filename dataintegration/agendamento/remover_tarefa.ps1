# TechStore - remove a tarefa agendada de verificação de alertas (não altera a base de dados).
#   powershell -ExecutionPolicy Bypass -File dataintegration\agendamento\remover_tarefa.ps1

$Nome = "TechStore - Verificar alertas"
if (Get-ScheduledTask -TaskName $Nome -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $Nome -Confirm:$false
    Write-Output "Tarefa '$Nome' removida."
} else {
    Write-Output "A tarefa '$Nome' não existe."
}
