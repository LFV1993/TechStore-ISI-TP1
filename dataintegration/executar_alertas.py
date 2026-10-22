"""Verificação de alertas TechStore: executa a função PostgreSQL verificar_alertas().

Execução manual (origem 'manual'):
    python dataintegration/executar_alertas.py
O mesmo script está preparado para ser chamado por um job do Agendador de Tarefas do Windows
(dataintegration/agendamento/criar_tarefa.ps1, que passa --origem agendador). Esse job não está instalado neste computador.

Cada execução fica registada:
  - na base de dados (tabela alertas_execucoes), sempre que for possível ligar ao PostgreSQL;
  - no ficheiro logs/alertas_job.log, incluindo as falhas de ligação (que não podem ser gravadas na BD).
Códigos de saída: 0 sucesso ou sem dados; 1 configuração, ligação ou sistema não instalado; 2 erro na função.
As credenciais nunca são escritas no registo.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from techstore import alertas, database  # noqa: E402


def registar(ficheiro: Path, mensagem: str) -> None:
    linha = f"{datetime.now().astimezone().isoformat(timespec='seconds')} {mensagem}"
    print(linha)
    ficheiro.parent.mkdir(parents=True, exist_ok=True)
    with open(ficheiro, "a", encoding="utf-8") as f:
        f.write(linha + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verificação de alertas TechStore")
    parser.add_argument("--origem", default="manual", choices=["manual", "agendador"])
    parser.add_argument("--log", default=str(RAIZ / "logs" / "alertas_job.log"), help="ficheiro de registo")
    args = parser.parse_args(argv)
    log = Path(args.log)

    cfg = database.configuracao_bd(RAIZ)
    if cfg is None:
        registar(log, f"[{args.origem}] FALHA: base de dados não configurada (.env)")
        return 1
    engine = database.criar_engine(cfg)
    ok, msg = database.testar_ligacao(engine)
    if not ok:
        registar(log, f"[{args.origem}] FALHA: sem ligação ao PostgreSQL ({msg})")
        return 1
    try:
        with engine.connect() as conn:
            em_falta = alertas.objetos_em_falta(conn)
        if em_falta:
            registar(log, f"[{args.origem}] FALHA: sistema de alertas não instalado (em falta: {', '.join(em_falta)})")
            return 1
        ex = alertas.executar_verificacao(engine, args.origem)
    except Exception as erro:  # noqa: BLE001 - registo claro, sem credenciais
        detalhe = str(getattr(erro, "orig", erro)).splitlines()[0]
        registar(log, f"[{args.origem}] FALHA: {type(erro).__name__}: {detalhe}")
        return 1
    registar(log, f"[{args.origem}] execução {ex['id']}: {ex['estado']} - novos={ex['novos']} "
                  f"atualizados={ex['atualizados']} resolvidos={ex['resolvidos']} ativos={ex['ativos']}"
                  + (f" - {ex['mensagem']}" if ex["mensagem"] else ""))
    return 2 if ex["estado"] == "erro" else 0


if __name__ == "__main__":
    raise SystemExit(main())
