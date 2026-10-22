"""Exportação para CSV e preparação da carga no PostgreSQL."""
import os
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from techstore import database, load, pipeline


@pytest.fixture(scope="module")
def res():
    return pipeline.executar()


def test_csv_ida_e_volta_preserva_valores_e_tipos(res, tmp_path):
    load.exportar_csv(res.tabelas(), tmp_path)
    lidos = load.ler_csv(tmp_path)
    original, relido = res.vendas_validas, lidos["vendas_validas"]
    assert len(relido) == len(original)
    assert relido["valor_total"].tolist() == original["valor_total"].tolist()      # Decimal exato
    assert relido["data_venda"].tolist() == original["data_venda"].tolist()
    assert relido["quantidade"].dtype == "int64"
    assert len(lidos["vendas_rejeitadas"]) == len(res.vendas_rejeitadas)


def test_registos_para_bd_tem_tipos_nativos(res):
    reg = load.preparar_registos(res.produtos, res.categorias, res.tabelas())
    assert len(reg["vendas"]) == len(res.vendas_validas)
    venda = reg["vendas"][0]
    assert isinstance(venda["data_venda"], date)
    assert type(venda["quantidade"]) is int and isinstance(venda["valor_total"], Decimal)
    assert {p["codigo_produto"] for p in reg["produtos"]} >= {v["codigo_produto"] for v in reg["vendas"]}
    assert set(reg) == set(load.ORDEM_CARGA)


def test_sem_configuracao_nao_ha_ligacao(tmp_path, monkeypatch):
    for v in database.VARIAVEIS:
        monkeypatch.delenv(v, raising=False)
    assert database.configuracao_bd(tmp_path) is None


def test_ordem_de_carga_respeita_as_chaves_estrangeiras():
    ordem = load.ORDEM_CARGA
    assert ordem.index("categorias") < ordem.index("produtos") < ordem.index("vendas")
    assert set(ordem) == set(database.TABELAS)


# ---------------------------------------------------------------- integração com PostgreSQL
# Só corre com TECHSTORE_TESTAR_BD=1 e apenas na base de dados "techstore".
# Substitui o conteúdo das tabelas do TechStore pelo resultado do ETL (o mesmo estado que deixa
# "python etl.py --bd"); não cria nem apaga objetos.

@pytest.fixture(scope="module")
def engine():
    if os.getenv("TECHSTORE_TESTAR_BD") != "1":
        pytest.skip("teste de integração: requer PostgreSQL configurado e TECHSTORE_TESTAR_BD=1")
    cfg = database.configuracao_bd(pipeline.RAIZ)
    if cfg is None:
        pytest.skip("base de dados não configurada (.env)")
    eng = database.criar_engine(cfg)
    with eng.connect() as conn:
        if conn.execute(text("SELECT current_database()")).scalar_one() != "techstore":
            pytest.skip("por segurança, o teste só corre na base de dados techstore")
    if database.verificar_esquema(eng):
        pytest.skip("schema incompleto: executar dataintegration/sql/schema.sql")
    return eng


def test_carga_repetida_nao_duplica(res, engine):
    registos = load.preparar_registos(res.produtos, res.categorias, res.tabelas())
    primeira = load.carregar_bd(engine, registos)
    segunda = load.carregar_bd(engine, registos)
    assert primeira == segunda
    assert segunda == {"categorias": len(res.categorias), "produtos": len(res.produtos),
                       "vendas": len(res.vendas_validas), "vendas_rejeitadas": len(res.vendas_rejeitadas),
                       "resumo_qualidade": len(res.qualidade)}


def test_totais_e_integridade_na_bd(res, engine):
    with engine.connect() as conn:
        q = lambda sql: conn.execute(text(sql)).one()
        unidades, faturacao, ids = q("SELECT SUM(quantidade), SUM(valor_total), COUNT(DISTINCT id_venda) FROM vendas")
        assert unidades == res.contagens["unidades_vendidas"]
        assert faturacao == res.contagens["faturacao_total"]
        assert ids == len(res.vendas_validas)
        assert q("SELECT COUNT(*) FROM vendas WHERE valor_total <> quantidade * preco_unitario")[0] == 0
        assert q("SELECT COUNT(*) FROM vendas v LEFT JOIN produtos p USING (codigo_produto) "
                 "WHERE p.codigo_produto IS NULL")[0] == 0
        for vista in ("vw_faturacao_mensal", "vw_vendas_produto", "vw_vendas_categoria"):
            assert q(f"SELECT SUM(faturacao) FROM {vista}")[0] == faturacao
        assert q("SELECT COUNT(*) FROM vw_vendas_detalhe")[0] == len(res.vendas_validas)


def test_erro_na_carga_reverte_a_transacao(res, engine):
    registos = load.preparar_registos(res.produtos, res.categorias, res.tabelas())
    antes = load.carregar_bd(engine, registos)
    invalidos = {**registos, "vendas": registos["vendas"] + [{**registos["vendas"][0], "id_venda": "TESTE-ROLLBACK",
                                                               "quantidade": 0}]}  # viola CHECK (quantidade > 0)
    with pytest.raises(Exception):
        load.carregar_bd(engine, invalidos)
    with engine.connect() as conn:
        depois = {t: conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar_one() for t in load.ORDEM_CARGA}
        teste = conn.execute(text("SELECT COUNT(*) FROM vendas WHERE id_venda = 'TESTE-ROLLBACK'")).scalar_one()
    assert depois == antes and teste == 0


def test_leitura_da_bd_igual_ao_etl(res, engine):
    lidos = load.ler_bd(engine)
    vendas = lidos["vendas_validas"]
    assert sorted(vendas["id_venda"]) == sorted(res.vendas_validas["id_venda"])
    assert sum(vendas["valor_total"], Decimal("0")) == res.contagens["faturacao_total"]
    assert len(lidos["vendas_rejeitadas"]) == len(res.vendas_rejeitadas)
