"""Testes do pipeline completo: dados sintéticos com composição conhecida e dados reais do projeto."""
from decimal import Decimal

import pandas as pd
import pytest

from techstore import pipeline, transform

COLS = ["id_venda", "data_venda", "codigo_produto", "quantidade", "preco_unitario"]


@pytest.fixture(scope="module")
def real():
    return pipeline.executar()


# ---------------------------------------------------------------- dados sintéticos

def test_pipeline_com_dados_sinteticos():
    validas = [["V1", "2026-01-05", "PRD-0001", "2", "10.00"],
               ["V2", "2026-01-06", "prd0002", "1", "5.50"],      # recuperável
               ["V3", "2026-02-01", "PRD-0001", "1", "10.00"]]
    duplicado = [validas[0]]
    invalidas = [["V4", "2026-02-30", "PRD-0001", "1", "10.00"],   # data impossível
                 ["V5", "2026-02-02", "PRD-0001", "1", ""]]        # campo vazio
    inexistente = [["V6", "2026-02-03", "PRD-0404", "1", "1.00"]]
    linhas = validas + duplicado + invalidas + inexistente
    produtos = pd.DataFrame([["PRD-0001", "A", "CAT-01"], ["PRD-0002", "B", "CAT-01"]],
                            columns=["codigo_produto", "nome_produto", "codigo_categoria"], dtype=str)
    categorias = pd.DataFrame([["CAT-01", "X"]], columns=["codigo_categoria", "nome_categoria"], dtype=str)

    res = pipeline.transformar(pd.DataFrame(linhas, columns=COLS, dtype=str), produtos, categorias)
    c = res.contagens
    assert c["duplicados_removidos"] == len(duplicado)
    assert c["rejeitadas_validacao"] == len(invalidas)
    assert c["rejeitadas_integracao"] == len(inexistente)
    assert c["vendas_validas"] == len(validas)
    assert c["codigos_normalizados"] == 1
    assert c["faturacao_total"] == Decimal("20.00") + Decimal("5.50") + Decimal("10.00")


# ---------------------------------------------------------------- dados reais

def test_reconciliacao_das_contagens(real):
    c = real.contagens
    assert c["vendas_lidas"] == c["duplicados_removidos"] + c["rejeitadas_total"] + c["vendas_validas"]
    assert c["rejeitadas_total"] == c["rejeitadas_validacao"] + c["rejeitadas_integracao"]
    assert c["vendas_apos_deduplicacao"] == c["vendas_lidas"] - c["duplicados_removidos"]


def test_duplicados_correspondem_a_linhas_repetidas_no_ficheiro(real):
    linhas = [";".join(r) for r in real.vendas_lidas.itertuples(index=False)]
    assert real.contagens["duplicados_removidos"] == len(linhas) - len(set(linhas))


def test_nenhuma_venda_perdida_ou_repetida(real):
    ids_finais = set(real.vendas_validas["id_venda"])
    ids_rejeitados = set(real.vendas_rejeitadas["id_venda"])
    assert real.vendas_validas["id_venda"].is_unique
    assert not ids_finais & ids_rejeitados
    assert ids_finais | ids_rejeitados == set(real.vendas_lidas["id_venda"])


def test_rejeitadas_tem_sempre_motivo(real):
    rej = real.vendas_rejeitadas
    assert rej["tipo_rejeicao"].isin(transform.TIPOS_REJEICAO).all()
    assert (rej["motivo_rejeicao"].str.len() > 0).all()


def test_produtos_inexistentes_nao_existem_no_catalogo(real):
    inexistentes = real.vendas_rejeitadas.query("tipo_rejeicao == 'produto_inexistente'")["codigo_normalizado"]
    assert len(inexistentes) > 0
    assert not set(inexistentes) & set(real.produtos["codigo_produto"])


def test_vendas_validas_cumprem_as_regras(real):
    v = real.vendas_validas
    assert v["codigo_produto"].str.fullmatch(r"PRD-[0-9]{4}").all()
    assert (v["quantidade"] > 0).all()
    assert all(p > 0 for p in v["preco_unitario"])
    assert v["codigo_produto"].isin(real.produtos["codigo_produto"]).all()
    assert v["nome_categoria"].notna().all()


def test_faturacao_recalculada_independentemente(real):
    v = real.vendas_validas
    # recálculo em cêntimos inteiros, sem Decimal, como verificação independente
    cent = sum(q * int(round(float(p) * 100)) for q, p in zip(v["quantidade"], v["preco_unitario"]))
    assert transform.somar_decimal(v["valor_total"]) == Decimal(cent) / 100


def test_agregacoes_coerentes_com_as_vendas(real):
    total = real.contagens["faturacao_total"]
    for agregado in (real.por_mes, real.por_produto, real.por_categoria):
        assert transform.somar_decimal(agregado["faturacao"]) == total
        assert agregado["unidades"].sum() == real.contagens["unidades_vendidas"]
        assert agregado["n_vendas"].sum() == real.contagens["vendas_validas"]


def test_regressao_valores_de_referencia_documentados(real):
    """Regressão face aos valores documentados no relatório (calculados antes, por outra implementação)."""
    c = real.contagens
    assert (c["vendas_lidas"], c["duplicados_removidos"], c["rejeitadas_total"], c["vendas_validas"]) == (526, 8, 18, 500)
    assert sorted(real.vendas_rejeitadas.query("tipo_rejeicao == 'produto_inexistente'")["codigo_normalizado"]) == \
        ["PRD-0031", "PRD-0045", "PRD-0100", "PRD-0500", "PRD-9999"]
    assert c["faturacao_total"] == Decimal("69981.01")
