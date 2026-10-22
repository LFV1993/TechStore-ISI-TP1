"""Sistema de alertas: chamada das funções SQL e leitura dos resultados.

As regras de negócio estão exclusivamente na função PostgreSQL verificar_alertas() (dataintegration/sql/alertas.sql).
Este módulo apenas a executa, lê os alertas e as execuções e permite reconhecer um alerta.
As funções recebem uma ligação (Connection); quem chama decide a transação.
"""
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

TABELAS = ["alertas", "alertas_execucoes"]
FUNCOES = ["verificar_alertas", "reconhecer_alerta"]

TIPOS = {
    "qualidade_dados": "A1 - Qualidade dos dados",
    "queda_faturacao": "A2 - Queda de faturação",
    "produto_sem_vendas": "A3 - Produtos sem vendas",
    "venda_atipica": "A4 - Vendas atípicas",
    "concentracao_faturacao": "A5 - Concentração de faturação",
}
SEVERIDADES = {"critico": "Crítico", "aviso": "Aviso", "info": "Informativo"}
ESTADOS = {"ativo": "Ativo", "reconhecido": "Reconhecido", "resolvido": "Resolvido"}


def objetos_em_falta(conn: Connection) -> list[str]:
    """Tabelas e funções do sistema de alertas que ainda não existem (lista vazia se estiver instalado)."""
    tabelas = {r[0] for r in conn.execute(text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"))}
    funcoes = {r[0] for r in conn.execute(text(
        "SELECT proname FROM pg_proc WHERE pronamespace = 'public'::regnamespace"))}
    return [t for t in TABELAS if t not in tabelas] + [f + "()" for f in FUNCOES if f not in funcoes]


def verificar(conn: Connection, origem: str = "manual", **parametros) -> dict:
    """Executa verificar_alertas() e devolve o registo da execução.

    Os parâmetros opcionais (por exemplo data_referencia ou limite_queda) correspondem aos
    parâmetros p_* da função SQL; sem eles são usados os valores por omissão definidos na função.
    """
    args = ", ".join(["p_origem => :origem"] + [f"p_{k} => :{k}" for k in parametros])
    id_execucao = conn.execute(text(f"SELECT verificar_alertas({args})"),
                               {"origem": origem, **parametros}).scalar_one()
    return dict(conn.execute(text("SELECT * FROM alertas_execucoes WHERE id = :id"),
                             {"id": id_execucao}).mappings().one())


def executar_verificacao(engine: Engine, origem: str) -> dict:
    """Executa a verificação numa transação própria, confirmada no fim."""
    with engine.begin() as conn:
        return verificar(conn, origem)


def reconhecer(conn: Connection, id_alerta: int) -> bool:
    """Marca um alerta ativo como reconhecido. Devolve False se não existir ou não estiver ativo."""
    return bool(conn.execute(text("SELECT reconhecer_alerta(:id)"), {"id": int(id_alerta)}).scalar_one())


def ler_alertas(conn: Connection) -> pd.DataFrame:
    df = pd.read_sql(text(
        "SELECT id, tipo, severidade, chave, titulo, descricao, detalhes, estado, primeira_detecao, "
        "ultima_detecao, n_detecoes, reconhecido_em, resolvido_em FROM alertas ORDER BY id"), conn)
    return df


def ler_execucoes(conn: Connection, limite: int = 50) -> pd.DataFrame:
    return pd.read_sql(text("SELECT * FROM alertas_execucoes ORDER BY id DESC LIMIT :n"), conn,
                       params={"n": int(limite)})
