"""Transformação: limpeza, validação, integração, cálculos e agregações.

Decisões principais:
- Duplicados: linhas exatamente iguais em todas as colunas originais (fica a primeira ocorrência).
- Códigos de produto: normalizados com Regex (maiúsculas e hífen em falta) e validados com ^PRD-[0-9]{4}$.
- Validação: as regras são aplicadas por ordem e o primeiro problema encontrado define o motivo de rejeição.
- Valores monetários: Decimal, com arredondamento explícito a 2 casas (ROUND_HALF_UP).
- Joins: validate="many_to_one" garante que cada venda corresponde, no máximo, a um produto e a uma categoria.
"""
import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd

REGEX_CODIGO_VALIDO = re.compile(r"^PRD-[0-9]{4}$")
REGEX_CODIGO_RECUPERAVEL = re.compile(r"^PRD-?([0-9]{4})$")
REGEX_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
REGEX_QUANTIDADE = re.compile(r"^-?[0-9]+$")
REGEX_PRECO = re.compile(r"^-?[0-9]+(\.[0-9]{1,2})?$")
CENTIMO = Decimal("0.01")

COLUNAS_OBRIGATORIAS = ["id_venda", "data_venda", "codigo_produto", "quantidade", "preco_unitario"]

# Tipo de rejeição (identificador estável) -> descrição apresentada nos relatórios
TIPOS_REJEICAO = {
    "campo_vazio": "Campo obrigatório vazio",
    "codigo_invalido": "Código de produto inválido",
    "quantidade_invalida": "Quantidade inválida",
    "preco_invalido": "Preço inválido",
    "data_invalida": "Data inválida",
    "produto_inexistente": "Produto inexistente",
    "categoria_inexistente": "Categoria inexistente",
}

COLUNAS_REJEITADAS = ["id_venda", "data_venda", "codigo_produto", "codigo_normalizado", "quantidade",
                      "preco_unitario", "etapa", "tipo_rejeicao", "motivo_rejeicao"]

DIAS_SEMANA = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]


# ---------------------------------------------------------------- limpeza

def remover_duplicados(vendas: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separa as linhas únicas dos duplicados exatos (mantém a primeira ocorrência)."""
    duplicado = vendas.duplicated(keep="first")
    return vendas[~duplicado].reset_index(drop=True), vendas[duplicado].reset_index(drop=True)


def normalizar_codigo(codigo: str) -> str:
    """Remove espaços, passa a maiúsculas e acrescenta o hífen em códigos como PRD0003.

    Códigos que não seguem o padrão recuperável são devolvidos em maiúsculas, sem outras alterações,
    e serão rejeitados na validação.
    """
    texto = codigo.strip().upper()
    m = REGEX_CODIGO_RECUPERAVEL.fullmatch(texto)
    return f"PRD-{m.group(1)}" if m else texto


def validar_data(texto: str) -> datetime | None:
    """Devolve a data se o texto estiver no formato AAAA-MM-DD e corresponder a um dia real do calendário."""
    if not REGEX_DATA.fullmatch(texto):
        return None
    try:
        return datetime.strptime(texto, "%Y-%m-%d")
    except ValueError:  # por exemplo 2026-02-30
        return None


def validar_quantidade(texto: str) -> int | None:
    """Quantidade válida: número inteiro estritamente positivo."""
    if not REGEX_QUANTIDADE.fullmatch(texto):
        return None
    valor = int(texto)
    return valor if valor > 0 else None


def validar_preco(texto: str) -> Decimal | None:
    """Preço válido: número positivo com, no máximo, duas casas decimais (ponto decimal)."""
    if not REGEX_PRECO.fullmatch(texto):
        return None
    valor = Decimal(texto)
    return valor.quantize(CENTIMO) if valor > 0 else None


def validar_registo(registo: dict) -> dict:
    """Valida uma venda. Devolve os valores convertidos ou o tipo e o motivo de rejeição."""
    valores = {c: (registo[c] or "").strip() for c in COLUNAS_OBRIGATORIAS}
    codigo = normalizar_codigo(valores["codigo_produto"]) if valores["codigo_produto"] else ""
    resultado = {"codigo_normalizado": codigo, "tipo_rejeicao": None, "motivo_rejeicao": None}

    def rejeitar(tipo: str, detalhe: str) -> dict:
        resultado.update(tipo_rejeicao=tipo, motivo_rejeicao=f"{TIPOS_REJEICAO[tipo]}: {detalhe}")
        return resultado

    vazios = [c for c in COLUNAS_OBRIGATORIAS if not valores[c]]
    if vazios:
        return rejeitar("campo_vazio", ", ".join(vazios))
    if not REGEX_CODIGO_VALIDO.fullmatch(codigo):
        return rejeitar("codigo_invalido", valores["codigo_produto"])
    quantidade = validar_quantidade(valores["quantidade"])
    if quantidade is None:
        return rejeitar("quantidade_invalida", valores["quantidade"])
    preco = validar_preco(valores["preco_unitario"])
    if preco is None:
        return rejeitar("preco_invalido", valores["preco_unitario"])
    data = validar_data(valores["data_venda"])
    if data is None:
        return rejeitar("data_invalida", valores["data_venda"])
    resultado.update(data=data, quantidade_int=quantidade, preco=preco)
    return resultado


def validar_vendas(vendas: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Aplica as regras de validação. Devolve (vendas válidas com tipos convertidos, vendas rejeitadas)."""
    validas, rejeitadas = [], []
    for registo in vendas.to_dict("records"):
        r = validar_registo(registo)
        if r["tipo_rejeicao"]:
            rejeitadas.append({**registo, "codigo_normalizado": r["codigo_normalizado"], "etapa": "validação",
                               "tipo_rejeicao": r["tipo_rejeicao"], "motivo_rejeicao": r["motivo_rejeicao"]})
        else:
            validas.append({
                "id_venda": registo["id_venda"].strip(),
                "data_venda": r["data"],
                "codigo_original": registo["codigo_produto"],
                "codigo_produto": r["codigo_normalizado"],
                "codigo_corrigido": registo["codigo_produto"] != r["codigo_normalizado"],
                "quantidade": r["quantidade_int"],
                "preco_unitario": r["preco"],
            })
    df_validas = pd.DataFrame(validas, columns=["id_venda", "data_venda", "codigo_original", "codigo_produto",
                                                "codigo_corrigido", "quantidade", "preco_unitario"])
    df_validas["data_venda"] = pd.to_datetime(df_validas["data_venda"])
    df_validas["quantidade"] = df_validas["quantidade"].astype("int64")
    return df_validas, pd.DataFrame(rejeitadas, columns=COLUNAS_REJEITADAS)


# ---------------------------------------------------------------- integração

def integrar(validas: pd.DataFrame, produtos: pd.DataFrame,
             categorias: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Junta vendas, produtos e categorias. Devolve (vendas integradas, vendas sem produto ou categoria)."""
    # many_to_one: falha se um código de produto ou de categoria aparecer repetido no catálogo
    com_produto = validas.merge(produtos, on="codigo_produto", how="left", validate="many_to_one", indicator=True)
    sem_produto = com_produto[com_produto["_merge"] == "left_only"]
    com_produto = com_produto[com_produto["_merge"] == "both"].drop(columns="_merge")

    com_categoria = com_produto.merge(categorias, on="codigo_categoria", how="left", validate="many_to_one",
                                      indicator=True)
    sem_categoria = com_categoria[com_categoria["_merge"] == "left_only"]
    integradas = com_categoria[com_categoria["_merge"] == "both"].drop(columns="_merge").reset_index(drop=True)

    rejeitadas = pd.concat([_rejeicao_integracao(sem_produto, "produto_inexistente", "codigo_produto"),
                            _rejeicao_integracao(sem_categoria, "categoria_inexistente", "codigo_categoria")],
                           ignore_index=True)
    if len(integradas) + len(rejeitadas) != len(validas):
        raise AssertionError("os joins alteraram o número de vendas")
    return integradas, rejeitadas


def _rejeicao_integracao(df: pd.DataFrame, tipo: str, coluna_chave: str) -> pd.DataFrame:
    """Converte vendas sem correspondência para o formato das rejeitadas, com os valores em texto."""
    if df.empty:
        return pd.DataFrame(columns=COLUNAS_REJEITADAS)
    return pd.DataFrame({
        "id_venda": df["id_venda"],
        "data_venda": df["data_venda"].dt.strftime("%Y-%m-%d"),
        "codigo_produto": df["codigo_original"],
        "codigo_normalizado": df["codigo_produto"],
        "quantidade": df["quantidade"].astype(str),
        "preco_unitario": df["preco_unitario"].astype(str),
        "etapa": "integração",
        "tipo_rejeicao": tipo,
        "motivo_rejeicao": [f"{TIPOS_REJEICAO[tipo]}: {v}" for v in df[coluna_chave]],
    }, columns=COLUNAS_REJEITADAS)


# ---------------------------------------------------------------- cálculos

def calcular_valor_total(quantidade: int, preco: Decimal) -> Decimal:
    """valor_total = quantidade * preco_unitario, em Decimal e arredondado ao cêntimo."""
    return (Decimal(quantidade) * preco).quantize(CENTIMO, rounding=ROUND_HALF_UP)


def acrescentar_campos(vendas: pd.DataFrame) -> pd.DataFrame:
    """Acrescenta valor_total e campos auxiliares para análise temporal."""
    df = vendas.copy()
    df["valor_total"] = [calcular_valor_total(q, p) for q, p in zip(df["quantidade"], df["preco_unitario"])]
    df["ano"] = df["data_venda"].dt.year.astype("int64")
    df["mes"] = df["data_venda"].dt.month.astype("int64")
    df["ano_mes"] = df["data_venda"].dt.strftime("%Y-%m")
    df["trimestre"] = df["data_venda"].dt.quarter.astype("int64")
    df["dia_semana"] = [DIAS_SEMANA[d] for d in df["data_venda"].dt.dayofweek]
    colunas = ["id_venda", "data_venda", "ano", "mes", "ano_mes", "trimestre", "dia_semana",
               "codigo_produto", "codigo_original", "codigo_corrigido", "nome_produto",
               "codigo_categoria", "nome_categoria", "quantidade", "preco_unitario", "valor_total"]
    return df[colunas].sort_values(["data_venda", "id_venda"]).reset_index(drop=True)


# ---------------------------------------------------------------- agregações

def somar_decimal(valores) -> Decimal:
    return sum(valores, Decimal("0"))


def agregar(vendas: pd.DataFrame, chaves: list[str]) -> pd.DataFrame:
    """Número de vendas, unidades e faturação por grupo."""
    colunas = chaves + ["n_vendas", "unidades", "faturacao"]
    if vendas.empty:
        return pd.DataFrame(columns=colunas)
    g = vendas.groupby(chaves, sort=True, dropna=False)
    resultado = pd.DataFrame({
        "n_vendas": g["id_venda"].count(),
        "unidades": g["quantidade"].sum(),
        "faturacao": g["valor_total"].agg(somar_decimal),
    }).reset_index()
    return resultado[colunas]


def agregar_por_mes(vendas: pd.DataFrame) -> pd.DataFrame:
    return agregar(vendas, ["ano_mes"])


def agregar_por_categoria(vendas: pd.DataFrame) -> pd.DataFrame:
    """Ordenado por faturação (desc.) e, em caso de empate, por código, para um resultado determinístico."""
    df = agregar(vendas, ["codigo_categoria", "nome_categoria"])
    if df.empty:
        return df
    df["_fat"] = df["faturacao"].map(float)
    return (df.sort_values(["_fat", "codigo_categoria"], ascending=[False, True])
              .drop(columns="_fat").reset_index(drop=True))


def agregar_por_produto(vendas: pd.DataFrame) -> pd.DataFrame:
    """Ordenado por unidades vendidas (desc.), depois faturação (desc.) e código, para um desempate estável."""
    df = agregar(vendas, ["codigo_produto", "nome_produto", "nome_categoria"])
    if df.empty:
        return df
    df["_fat"] = df["faturacao"].map(float)
    return (df.sort_values(["unidades", "_fat", "codigo_produto"], ascending=[False, False, True])
              .drop(columns="_fat").reset_index(drop=True))


def top_produtos(vendas: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    return agregar_por_produto(vendas).head(n)
