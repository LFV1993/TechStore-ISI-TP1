"""Testes unitários com pequenos conjuntos de dados, independentes dos ficheiros originais."""
from decimal import Decimal

import pandas as pd
import pytest

from techstore import transform as t

COLS = ["id_venda", "data_venda", "codigo_produto", "quantidade", "preco_unitario"]


def vendas(*linhas):
    return pd.DataFrame(list(linhas), columns=COLS, dtype=str)


PRODUTOS = pd.DataFrame([["PRD-0001", "Rato", "CAT-01"], ["PRD-0002", "Teclado", "CAT-01"],
                         ["PRD-0003", "Cabo", "CAT-99"]],
                        columns=["codigo_produto", "nome_produto", "codigo_categoria"], dtype=str)
CATEGORIAS = pd.DataFrame([["CAT-01", "Periféricos"]], columns=["codigo_categoria", "nome_categoria"], dtype=str)


# ---------------------------------------------------------------- duplicados

def test_remove_apenas_duplicados_exatos():
    df = vendas(["V1", "2026-01-01", "PRD-0001", "1", "9.90"],
                ["V1", "2026-01-01", "PRD-0001", "1", "9.90"],   # cópia exata
                ["V1", "2026-01-01", "PRD-0001", "2", "9.90"])   # difere na quantidade
    unicas, dup = t.remover_duplicados(df)
    assert len(unicas) == 2 and len(dup) == 1
    assert unicas["quantidade"].tolist() == ["1", "2"]


# ---------------------------------------------------------------- Regex

@pytest.mark.parametrize("entrada,esperado", [
    ("prd-0002", "PRD-0002"), ("PRD0003", "PRD-0003"), ("prd0005", "PRD-0005"),
    (" PRD-0004 ", "PRD-0004"), ("PRD-0001", "PRD-0001"),
    ("PRD-12", "PRD-12"), ("ABC-0001", "ABC-0001"), ("PRD--0001", "PRD--0001"),
])
def test_normalizar_codigo(entrada, esperado):
    assert t.normalizar_codigo(entrada) == esperado


@pytest.mark.parametrize("codigo,valido", [("PRD-0001", True), ("PRD-12", False), ("prd-0001", False),
                                           ("PRD-00011", False), ("XPRD-0001", False)])
def test_regex_codigo_valido(codigo, valido):
    assert bool(t.REGEX_CODIGO_VALIDO.fullmatch(codigo)) is valido


# ---------------------------------------------------------------- datas, quantidades e preços

@pytest.mark.parametrize("texto,valida", [
    ("2026-02-28", True), ("2024-02-29", True), ("2026-02-29", False), ("2026-02-30", False),
    ("2026-04-31", False), ("2026-13-01", False), ("2026-1-5", False), ("05-01-2026", False), ("", False),
])
def test_validar_data(texto, valida):
    assert (t.validar_data(texto) is not None) is valida


@pytest.mark.parametrize("texto,esperado", [("3", 3), ("0", None), ("-2", None), ("2.5", None), ("abc", None)])
def test_validar_quantidade(texto, esperado):
    assert t.validar_quantidade(texto) == esperado


@pytest.mark.parametrize("texto,esperado", [
    ("12.90", Decimal("12.90")), ("12.9", Decimal("12.90")), ("7", Decimal("7.00")),
    ("0", None), ("0.00", None), ("-1.00", None), ("abc", None), ("1,50", None), ("1.999", None),
])
def test_validar_preco(texto, esperado):
    assert t.validar_preco(texto) == esperado


def test_primeiro_problema_define_o_motivo():
    # quantidade vazia e preço negativo: prevalece o campo vazio (primeira regra)
    r = t.validar_registo(dict(zip(COLS, ["V1", "2026-01-01", "PRD-0001", "", "-1.00"])))
    assert r["tipo_rejeicao"] == "campo_vazio"
    assert r["motivo_rejeicao"] == "Campo obrigatório vazio: quantidade"


def test_validar_vendas_preserva_valores_e_motivos():
    df = vendas(["V1", "2026-01-01", "prd-0001", "2", "9.90"],
                ["V2", "2026-02-30", "PRD-0001", "1", "9.90"],
                ["V3", "2026-01-02", "PRD-0001", "0", "9.90"],
                ["V4", "2026-01-03", "PRD-0001", "1", "-9.90"],
                ["V5", "2026-01-04", "XYZ", "1", "9.90"])
    validas, rejeitadas = t.validar_vendas(df)
    assert validas["id_venda"].tolist() == ["V1"]
    assert validas.loc[0, "codigo_produto"] == "PRD-0001" and bool(validas.loc[0, "codigo_corrigido"])
    assert validas.loc[0, "preco_unitario"] == Decimal("9.90")
    assert dict(zip(rejeitadas["id_venda"], rejeitadas["tipo_rejeicao"])) == {
        "V2": "data_invalida", "V3": "quantidade_invalida", "V4": "preco_invalido", "V5": "codigo_invalido"}
    # os valores originais ficam guardados tal como foram lidos
    assert rejeitadas.set_index("id_venda").loc["V4", "preco_unitario"] == "-9.90"
    assert (rejeitadas["motivo_rejeicao"].str.len() > 0).all()


# ---------------------------------------------------------------- integração

def _validas(*codigos):
    df = vendas(*[[f"V{i}", "2026-01-01", c, "1", "1.00"] for i, c in enumerate(codigos, 1)])
    return t.validar_vendas(df)[0]


def test_integracao_identifica_produtos_e_categorias_inexistentes():
    integradas, rejeitadas = t.integrar(_validas("PRD-0001", "PRD-0002", "PRD-0777", "PRD-0003"),
                                        PRODUTOS, CATEGORIAS)
    assert integradas["id_venda"].tolist() == ["V1", "V2"]
    assert integradas["nome_categoria"].tolist() == ["Periféricos", "Periféricos"]
    assert dict(zip(rejeitadas["id_venda"], rejeitadas["tipo_rejeicao"])) == {
        "V3": "produto_inexistente", "V4": "categoria_inexistente"}
    assert rejeitadas.set_index("id_venda").loc["V3", "motivo_rejeicao"] == "Produto inexistente: PRD-0777"


def test_join_nao_multiplica_vendas():
    integradas, rejeitadas = t.integrar(_validas("PRD-0001", "PRD-0001", "PRD-0002"), PRODUTOS, CATEGORIAS)
    assert len(integradas) + len(rejeitadas) == 3
    assert integradas["id_venda"].is_unique


def test_catalogo_com_codigo_repetido_e_recusado():
    repetido = pd.concat([PRODUTOS, PRODUTOS.iloc[[0]]], ignore_index=True)
    with pytest.raises(pd.errors.MergeError):
        t.integrar(_validas("PRD-0001"), repetido, CATEGORIAS)


# ---------------------------------------------------------------- cálculos e agregações

def test_agregacao_por_categoria_desempata_pelo_codigo():
    df = pd.DataFrame({"id_venda": ["V1", "V2"], "codigo_categoria": ["CAT-02", "CAT-01"],
                       "nome_categoria": ["B", "A"], "quantidade": [1, 1],
                       "valor_total": [Decimal("10.00"), Decimal("10.00")]})
    assert t.agregar_por_categoria(df)["codigo_categoria"].tolist() == ["CAT-01", "CAT-02"]
    assert t.agregar_por_categoria(df.iloc[::-1])["codigo_categoria"].tolist() == ["CAT-01", "CAT-02"]


def test_valor_total_em_decimal_sem_erros_de_virgula_flutuante():
    assert 3 * 0.10 != 0.30                                   # o problema que se quer evitar
    assert t.calcular_valor_total(3, Decimal("0.10")) == Decimal("0.30")
    assert t.calcular_valor_total(7, Decimal("19.99")) == Decimal("139.93")
    assert t.calcular_valor_total(2, Decimal("12.25")).as_tuple().exponent == -2


def test_campos_temporais_e_agregacoes():
    df = vendas(["V1", "2026-01-31", "PRD-0001", "2", "1.10"],
                ["V2", "2026-02-01", "PRD-0001", "1", "1.10"],
                ["V3", "2026-02-15", "PRD-0002", "3", "0.10"])
    integradas, _ = t.integrar(t.validar_vendas(df)[0], PRODUTOS, CATEGORIAS)
    final = t.acrescentar_campos(integradas)
    assert final["ano_mes"].tolist() == ["2026-01", "2026-02", "2026-02"]
    assert final["dia_semana"].tolist() == ["Sábado", "Domingo", "Domingo"]

    mes = t.agregar_por_mes(final).set_index("ano_mes")
    assert mes.loc["2026-02", "n_vendas"] == 2 and mes.loc["2026-02", "unidades"] == 4
    assert mes.loc["2026-02", "faturacao"] == Decimal("1.40")
    prod = t.agregar_por_produto(final)
    assert prod["codigo_produto"].tolist() == ["PRD-0001", "PRD-0002"]  # 3 unidades cada; desempate pela faturação
    total = t.somar_decimal(final["valor_total"])
    for agregado in (t.agregar_por_mes(final), prod, t.agregar_por_categoria(final)):
        assert t.somar_decimal(agregado["faturacao"]) == total
        assert agregado["unidades"].sum() == final["quantidade"].sum()
