"""Carregamento: exportação para CSV (data/output/) e carga no PostgreSQL.

A carga na base de dados é uma substituição completa dentro de uma transação
(TRUNCATE seguido de INSERT), pelo que pode ser repetida sem duplicar dados.
"""
from decimal import Decimal
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

FICHEIROS = {
    "vendas_validas": "vendas_validas.csv",
    "vendas_rejeitadas": "vendas_rejeitadas.csv",
    "qualidade": "qualidade_dados.csv",
    "por_mes": "agregado_mensal.csv",
    "por_produto": "agregado_produto.csv",
    "por_categoria": "agregado_categoria.csv",
}


# ---------------------------------------------------------------- CSV

def exportar_csv(tabelas: dict[str, pd.DataFrame], pasta: Path) -> list[Path]:
    """Grava as tabelas de resultados em CSV (separador ';', UTF-8, ponto decimal)."""
    pasta.mkdir(parents=True, exist_ok=True)
    criados = []
    for nome, ficheiro in FICHEIROS.items():
        destino = pasta / ficheiro
        tabelas[nome].to_csv(destino, sep=";", index=False, encoding="utf-8", date_format="%Y-%m-%d")
        criados.append(destino)
    return criados


def ler_csv(pasta: Path) -> dict[str, pd.DataFrame]:
    """Lê os resultados exportados, repondo os tipos (datas, inteiros e Decimal)."""
    def ler(nome, **kw):
        return pd.read_csv(pasta / FICHEIROS[nome], sep=";", encoding="utf-8", keep_default_na=False, **kw)

    vendas = ler("vendas_validas", dtype=str)
    vendas["data_venda"] = pd.to_datetime(vendas["data_venda"], format="%Y-%m-%d")
    for c in ["ano", "mes", "trimestre", "quantidade"]:
        vendas[c] = vendas[c].astype("int64")
    vendas["codigo_corrigido"] = vendas["codigo_corrigido"] == "True"
    for c in ["preco_unitario", "valor_total"]:
        vendas[c] = vendas[c].map(Decimal)
    qualidade = ler("qualidade", dtype=str)
    qualidade["ordem"] = qualidade["ordem"].astype("int64")
    qualidade["valor"] = qualidade["valor"].map(Decimal)
    return {"vendas_validas": vendas, "vendas_rejeitadas": ler("vendas_rejeitadas", dtype=str),
            "qualidade": qualidade}


# ---------------------------------------------------------------- PostgreSQL

def preparar_registos(produtos: pd.DataFrame, categorias: pd.DataFrame,
                      tabelas: dict[str, pd.DataFrame]) -> dict[str, list[dict]]:
    """Converte os resultados em listas de registos prontas a inserir (tipos Python nativos)."""
    vendas = tabelas["vendas_validas"].copy()
    vendas["data_venda"] = vendas["data_venda"].dt.date
    for c in ["ano", "mes", "trimestre", "quantidade"]:
        vendas[c] = vendas[c].map(int)
    vendas["codigo_corrigido"] = vendas["codigo_corrigido"].map(bool)
    colunas_vendas = ["id_venda", "data_venda", "ano", "mes", "ano_mes", "trimestre", "dia_semana",
                      "codigo_produto", "codigo_original", "codigo_corrigido", "quantidade",
                      "preco_unitario", "valor_total"]
    qualidade = tabelas["qualidade"][["ordem", "indicador", "descricao", "valor"]].copy()
    qualidade["ordem"] = qualidade["ordem"].map(int)
    return {
        "categorias": categorias.to_dict("records"),
        "produtos": produtos.to_dict("records"),
        "vendas": vendas[colunas_vendas].to_dict("records"),
        "vendas_rejeitadas": tabelas["vendas_rejeitadas"].to_dict("records"),
        "resumo_qualidade": qualidade.to_dict("records"),
    }


ORDEM_CARGA = ["categorias", "produtos", "vendas", "vendas_rejeitadas", "resumo_qualidade"]


def carregar_bd(engine: Engine, registos: dict[str, list[dict]]) -> dict[str, int]:
    """Substitui o conteúdo das tabelas numa única transação. Devolve o número de linhas por tabela.

    Em caso de erro, a transação é revertida e as tabelas ficam como estavam.
    O TRUNCATE inclui todas as tabelas ligadas por chaves estrangeiras no mesmo comando, pelo que
    não precisa de CASCADE. Não usa RESTART IDENTITY, que exigiria ser dono da sequência de
    vendas_rejeitadas.id (o identificador interno continua a numeração; a ordem mantém-se).
    """
    with engine.begin() as conn:
        return substituir_dados(conn, registos)


def substituir_dados(conn: Connection, registos: dict[str, list[dict]]) -> dict[str, int]:
    """TRUNCATE + INSERT das tabelas do TechStore na transação da ligação recebida.

    Não toca nas tabelas de alertas. Usado por carregar_bd() e pelos testes (em transações revertidas).
    """
    conn.execute(text("TRUNCATE " + ", ".join(reversed(ORDEM_CARGA))))
    for tabela in ORDEM_CARGA:
        linhas = registos[tabela]
        if linhas:
            colunas = list(linhas[0])
            sql = text(f"INSERT INTO {tabela} ({', '.join(colunas)}) "
                       f"VALUES ({', '.join(':' + c for c in colunas)})")
            conn.execute(sql, linhas)
    return {t: conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar_one() for t in ORDEM_CARGA}


def ler_bd(engine: Engine) -> dict[str, pd.DataFrame]:
    """Lê da base de dados as mesmas tabelas que ler_csv(), com os mesmos tipos."""
    with engine.connect() as conn:
        vendas = pd.read_sql(text("SELECT * FROM vw_vendas_detalhe ORDER BY data_venda, id_venda"), conn)
        rejeitadas = pd.read_sql(text("SELECT id_venda, data_venda, codigo_produto, codigo_normalizado, quantidade, "
                                      "preco_unitario, etapa, tipo_rejeicao, motivo_rejeicao "
                                      "FROM vendas_rejeitadas ORDER BY id"), conn)
        qualidade = pd.read_sql(text("SELECT ordem, indicador, descricao, valor FROM resumo_qualidade ORDER BY ordem"),
                                conn)
    vendas["data_venda"] = pd.to_datetime(vendas["data_venda"])
    for c in ["ano", "mes", "trimestre", "quantidade"]:
        vendas[c] = vendas[c].astype("int64")
    for c in ["preco_unitario", "valor_total"]:
        vendas[c] = vendas[c].map(lambda v: Decimal(str(v)))
    qualidade["valor"] = qualidade["valor"].map(lambda v: Decimal(str(v)))
    return {"vendas_validas": vendas, "vendas_rejeitadas": rejeitadas.fillna(""), "qualidade": qualidade}
