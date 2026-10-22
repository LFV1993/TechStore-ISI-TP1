"""Extração: leitura das três fontes de dados (CSV, JSON e XML).

Os valores são lidos como texto, sem conversões automáticas, para que a validação
e a conversão de tipos fiquem concentradas na fase de transformação.
"""
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

COLUNAS_VENDAS = ["id_venda", "data_venda", "codigo_produto", "quantidade", "preco_unitario"]
COLUNAS_PRODUTOS = ["codigo_produto", "nome_produto", "codigo_categoria"]
COLUNAS_CATEGORIAS = ["codigo_categoria", "nome_categoria"]


def ler_vendas(caminho: Path) -> pd.DataFrame:
    """Lê o CSV de vendas (separador ';', UTF-8). Campos vazios ficam como texto vazio."""
    df = pd.read_csv(caminho, sep=";", dtype=str, keep_default_na=False, encoding="utf-8")
    _verificar_colunas(df, COLUNAS_VENDAS, caminho)
    return df[COLUNAS_VENDAS]


def ler_produtos(caminho: Path) -> pd.DataFrame:
    """Lê o JSON de produtos: um array de objetos na raiz."""
    with open(caminho, encoding="utf-8") as f:
        dados = json.load(f)
    if not isinstance(dados, list):
        raise ValueError(f"{caminho}: era esperado um array JSON na raiz")
    df = pd.DataFrame(dados, dtype=str)
    _verificar_colunas(df, COLUNAS_PRODUTOS, caminho)
    return df[COLUNAS_PRODUTOS]


def ler_categorias(caminho: Path) -> pd.DataFrame:
    """Lê o XML de categorias: <categorias><categoria><codigo_categoria/><nome_categoria/></categoria>..."""
    raiz = ET.parse(caminho).getroot()
    registos = [{col: (elem.findtext(col) or "").strip() for col in COLUNAS_CATEGORIAS}
                for elem in raiz.findall("categoria")]
    df = pd.DataFrame(registos, columns=COLUNAS_CATEGORIAS, dtype=str)
    return df


def _verificar_colunas(df: pd.DataFrame, esperadas: list[str], origem: Path) -> None:
    em_falta = [c for c in esperadas if c not in df.columns]
    if em_falta:
        raise ValueError(f"{origem}: colunas em falta {em_falta}")
