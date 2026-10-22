"""Testes do sistema de alertas.

Parte 1 (sem base de dados): o job executar_alertas.py regista as falhas e devolve o código de saída certo.
Parte 2 (PostgreSQL, só com TECHSTORE_TESTAR_BD=1 e o sistema instalado): cada teste corre dentro de uma
transação que é sempre revertida no fim. Os dados são substituídos por cenários controlados apenas dentro
dessa transação, por isso os dados reais do TechStore e os alertas reais não ficam alterados.
"""
import importlib.util
import os
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import exc, text

from techstore import alertas, database, load, pipeline

RAIZ = Path(__file__).resolve().parents[2]
DIAS = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]


def importar_job():
    spec = importlib.util.spec_from_file_location("executar_alertas", RAIZ / "dataintegration" / "executar_alertas.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# ================================================================= parte 1: job sem base de dados

def test_job_sem_configuracao_regista_falha(tmp_path, monkeypatch):
    job = importar_job()
    monkeypatch.setattr(job.database, "configuracao_bd", lambda raiz: None)
    log = tmp_path / "job.log"
    assert job.main(["--log", str(log)]) == 1
    assert "FALHA: base de dados não configurada" in log.read_text(encoding="utf-8")


def test_job_sem_ligacao_regista_falha_sem_expor_password(tmp_path, monkeypatch):
    job = importar_job()
    falsa = {"DB_HOST": "127.0.0.1", "DB_PORT": "5499", "DB_NAME": "techstore", "DB_USER": "techstore_app",
             "DB_PASSWORD": "Password-Falsa-Teste-987"}
    monkeypatch.setattr(job.database, "configuracao_bd", lambda raiz: falsa)
    log = tmp_path / "job.log"
    assert job.main(["--origem", "manual", "--log", str(log)]) == 1
    conteudo = log.read_text(encoding="utf-8")
    assert "[manual] FALHA: sem ligação ao PostgreSQL" in conteudo
    assert falsa["DB_PASSWORD"] not in conteudo


# ================================================================= parte 2: PostgreSQL

REF = date(2026, 4, 1)   # data de referência: janeiro, fevereiro e março são meses completos
CATEGORIAS = [("CAT-01", "Categoria A"), ("CAT-02", "Categoria B"), ("CAT-03", "Categoria C")]
PRODUTOS = [("PRD-0001", "Produto A", "CAT-01"), ("PRD-0002", "Produto B", "CAT-02"),
            ("PRD-0003", "Produto C", "CAT-03")]


def venda(id_venda, dia, produto, quantidade=1, preco="10.00"):
    d = date.fromisoformat(dia)
    p = Decimal(preco)
    return {"id_venda": id_venda, "data_venda": d, "ano": d.year, "mes": d.month, "ano_mes": d.strftime("%Y-%m"),
            "trimestre": (d.month - 1) // 3 + 1, "dia_semana": DIAS[d.weekday()], "codigo_produto": produto,
            "codigo_original": produto, "codigo_corrigido": False, "quantidade": quantidade, "preco_unitario": p,
            "valor_total": (quantidade * p).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)}


def vendas_base():
    """Cenário neutro: 3 meses completos, 3 categorias com a mesma faturação e todos os produtos vendidos.

    Todas as regras podem ser avaliadas e nenhuma se verifica.
    """
    return [venda(f"T{m}{d:02d}{p[-1]}", f"2026-{m:02d}-{d:02d}", p)
            for m in (1, 2, 3) for d in (5, 15, 25) for p in ("PRD-0001", "PRD-0002", "PRD-0003")]


def montar(vendas, produtos=PRODUTOS, categorias=CATEGORIAS, rejeitadas=0, duplicados=0, lidas=None):
    lidas = len(vendas) + rejeitadas + duplicados if lidas is None else lidas
    return {
        "categorias": [{"codigo_categoria": c, "nome_categoria": n} for c, n in categorias],
        "produtos": [{"codigo_produto": c, "nome_produto": n, "codigo_categoria": k} for c, n, k in produtos],
        "vendas": vendas,
        "vendas_rejeitadas": [{"id_venda": f"R{i}", "data_venda": "2026-01-10", "codigo_produto": "PRD-0001",
                               "codigo_normalizado": "PRD-0001", "quantidade": "0", "preco_unitario": "10.00",
                               "etapa": "validação", "tipo_rejeicao": "quantidade_invalida",
                               "motivo_rejeicao": "Quantidade inválida: 0"} for i in range(rejeitadas)],
        "resumo_qualidade": [{"ordem": 1, "indicador": "vendas_lidas", "descricao": "Vendas lidas", "valor": Decimal(lidas)},
                             {"ordem": 2, "indicador": "duplicados_removidos", "descricao": "Duplicados",
                              "valor": Decimal(duplicados)}],
    }


def cenario(conn, vendas, **kw):
    load.substituir_dados(conn, montar(vendas, **kw))


def verificar(conn, **kw):
    kw.setdefault("data_referencia", REF)
    return alertas.verificar(conn, "teste", **kw)


def abertos(conn, tipo=None):
    sql = "SELECT * FROM alertas WHERE estado <> 'resolvido'" + (" AND tipo = :tipo" if tipo else "") + " ORDER BY chave"
    return [dict(r) for r in conn.execute(text(sql), {"tipo": tipo}).mappings()]


def por_chave(conn, chave):
    return [dict(r) for r in conn.execute(text("SELECT * FROM alertas WHERE chave = :c ORDER BY id"),
                                          {"c": chave}).mappings()]


@pytest.fixture(scope="module")
def engine():
    if os.getenv("TECHSTORE_TESTAR_BD") != "1":
        pytest.skip("teste de integração: requer PostgreSQL configurado e TECHSTORE_TESTAR_BD=1")
    cfg = database.configuracao_bd(RAIZ)
    if cfg is None:
        pytest.skip("base de dados não configurada (.env)")
    eng = database.criar_engine(cfg)
    with eng.connect() as c:
        if c.execute(text("SELECT current_database()")).scalar_one() != "techstore":
            pytest.skip("por segurança, os testes só correm na base de dados techstore")
        em_falta = alertas.objetos_em_falta(c)
    if em_falta:
        pytest.skip(f"sistema de alertas não instalado (em falta: {', '.join(em_falta)})")
    return eng


@pytest.fixture
def conn(engine):
    """Ligação numa transação revertida no fim. Começa por resolver (só nesta transação) os alertas reais."""
    c = engine.connect()
    t = c.begin()
    try:
        cenario(c, vendas_base())
        limpeza = verificar(c)
        assert limpeza["estado"] == "sucesso" and limpeza["ativos"] == 0, limpeza
        yield c
    finally:
        t.rollback()
        c.close()


# ---------------------------------------------------------------- cenário neutro

def test_cenario_neutro_avalia_todas_as_regras_sem_alertas(conn):
    ex = verificar(conn)
    assert (ex["estado"], ex["novos"], ex["ativos"]) == ("sucesso", 0, 0)
    for tipo in alertas.TIPOS:
        assert tipo in ex["mensagem"]


# ---------------------------------------------------------------- A1 - qualidade dos dados

def test_a1_taxa_de_rejeicao_acima_do_limite(conn):
    cenario(conn, vendas_base(), rejeitadas=4, lidas=100)          # 4% > 3%
    verificar(conn)
    [a] = abertos(conn, "qualidade_dados")
    assert a["chave"] == "A1:qualidade_dados" and a["severidade"] == "aviso"
    d = a["detalhes"]
    assert (d["vendas_lidas"], d["rejeitadas"], d["taxa_rejeicao_pct"], d["limite_pct"]) == (100, 4, 4.0, 3.0)
    assert d["principais_motivos"] == [{"tipo_rejeicao": "quantidade_invalida", "registos": 4}]


def test_a1_limite_exato_e_duplicados_nao_contam(conn):
    cenario(conn, vendas_base(), rejeitadas=3, duplicados=40, lidas=100)   # exatamente 3%; duplicados à parte
    verificar(conn)
    assert abertos(conn, "qualidade_dados") == []


def test_a1_critico_acima_do_dobro_do_limite(conn):
    cenario(conn, vendas_base(), rejeitadas=7, lidas=100)
    verificar(conn)
    assert abertos(conn, "qualidade_dados")[0]["severidade"] == "critico"


# ---------------------------------------------------------------- A2 - queda de faturação

def _com_fevereiro_a(preco):
    return [dict(v, **venda(v["id_venda"], v["data_venda"].isoformat(), v["codigo_produto"], preco=preco))
            if v["data_venda"].month == 2 else v for v in vendas_base()]


def test_a2_queda_acima_de_30_por_cento(conn):
    cenario(conn, _com_fevereiro_a("6.99"))                         # 90,00 -> 62,91 (-30,1%)
    verificar(conn)
    [a] = abertos(conn, "queda_faturacao")
    assert a["chave"] == "A2:2026-01>2026-02"
    d = a["detalhes"]
    assert (d["faturacao_anterior"], d["faturacao_atual"], d["diferenca"]) == (90.0, 62.91, -27.09)
    assert d["variacao_pct"] == -30.1


def test_a2_queda_de_exatamente_30_por_cento_nao_gera_alerta(conn):
    cenario(conn, _com_fevereiro_a("7.00"))                         # 90,00 -> 63,00 (-30,00%)
    verificar(conn)
    assert abertos(conn, "queda_faturacao") == []


def test_a2_mes_incompleto_nao_e_comparado(conn):
    vendas = [dict(v, **venda(v["id_venda"], v["data_venda"].isoformat(), v["codigo_produto"], preco="1.00"))
              if v["data_venda"].month == 3 else v for v in vendas_base()]
    cenario(conn, vendas)                                           # março com -90%
    verificar(conn, data_referencia=date(2026, 3, 31))              # março ainda não terminou
    assert abertos(conn, "queda_faturacao") == []
    verificar(conn, data_referencia=REF)                            # março já completo
    assert [a["chave"] for a in abertos(conn, "queda_faturacao")] == ["A2:2026-02>2026-03"]


def test_a2_sem_meses_suficientes_nao_avalia_nem_resolve(conn):
    cenario(conn, _com_fevereiro_a("6.99"))
    verificar(conn)
    assert len(abertos(conn, "queda_faturacao")) == 1
    ex = verificar(conn, data_referencia=date(2026, 2, 1))          # só janeiro completo: não há pares
    assert "queda_faturacao" not in ex["mensagem"]
    assert len(abertos(conn, "queda_faturacao")) == 1               # o alerta mantém-se


# ---------------------------------------------------------------- A3 - produtos sem vendas

def test_a3_produtos_sem_vendas_na_janela_de_60_dias(conn):
    produtos = PRODUTOS + [("PRD-0004", "Produto D", "CAT-01"), ("PRD-0005", "Produto E", "CAT-01"),
                           ("PRD-0006", "Produto F", "CAT-01")]
    vendas = vendas_base() + [venda("X1", "2026-01-24", "PRD-0004", preco="1.00"),   # véspera da janela
                              venda("X2", "2026-01-25", "PRD-0006", preco="1.00")]   # primeiro dia da janela
    cenario(conn, vendas, produtos=produtos)                        # última venda 25/03: janela 25/01 a 25/03
    verificar(conn)
    a3 = {a["chave"]: a["detalhes"] for a in abertos(conn, "produto_sem_vendas")}
    assert set(a3) == {"A3:PRD-0004", "A3:PRD-0005"}
    assert (a3["A3:PRD-0004"]["periodo_inicio"], a3["A3:PRD-0004"]["periodo_fim"]) == ("2026-01-25", "2026-03-25")
    assert a3["A3:PRD-0004"]["ultima_venda"] == "2026-01-24"
    assert a3["A3:PRD-0005"]["ultima_venda"] is None


def test_a3_sem_dados_que_cubram_a_janela_nao_avalia(conn):
    produtos = PRODUTOS + [("PRD-0005", "Produto E", "CAT-01")]
    so_marco = [v for v in vendas_base() if v["data_venda"].month == 3]
    cenario(conn, so_marco, produtos=produtos)                     # só 21 dias de dados
    ex = verificar(conn)
    assert "produto_sem_vendas" not in ex["mensagem"]
    assert abertos(conn, "produto_sem_vendas") == []


# ---------------------------------------------------------------- A4 - vendas atípicas

def test_a4_venda_acima_do_limite_iqr_da_categoria(conn):
    vendas = vendas_base() + [venda("X9", "2026-02-10", "PRD-0001", preco="100.00")]
    cenario(conn, vendas)
    verificar(conn)
    [a] = abertos(conn, "venda_atipica")
    assert a["chave"] == "A4:X9" and a["severidade"] == "info"
    d = a["detalhes"]
    assert (d["q1"], d["q3"], d["limite"], d["vendas_na_categoria"]) == (10.0, 10.0, 10.0, 10)


def test_a4_valor_igual_ao_limite_e_categorias_pequenas_nao_geram_alerta(conn):
    produtos = PRODUTOS + [("PRD-0007", "Produto G", "CAT-04")]
    categorias = CATEGORIAS + [("CAT-04", "Categoria D")]
    pequenas = [venda(f"Y{i}", f"2026-02-{10 + i:02d}", "PRD-0007", preco="1.00") for i in range(4)]
    pequenas.append(venda("Y9", "2026-02-20", "PRD-0007", preco="500.00"))   # categoria com 5 vendas (< 8)
    cenario(conn, vendas_base() + pequenas, produtos=produtos, categorias=categorias)
    verificar(conn)
    assert abertos(conn, "venda_atipica") == []                     # 10,00 = limite e CAT-04 não avaliada


# ---------------------------------------------------------------- A5 - concentração de faturação

def _com_marco_cat01_a(preco):
    return [dict(v, **venda(v["id_venda"], v["data_venda"].isoformat(), v["codigo_produto"], preco=preco))
            if v["data_venda"].month == 3 and v["codigo_produto"] == "PRD-0001" else v for v in vendas_base()]


def test_a5_categoria_acima_de_50_por_cento_num_mes_completo(conn):
    cenario(conn, _com_marco_cat01_a("20.01"))                      # 60,03 de 120,03 = 50,01%
    verificar(conn)
    [a] = abertos(conn, "concentracao_faturacao")
    assert a["chave"] == "A5:2026-03:CAT-01"
    assert (a["detalhes"]["quota_pct"], a["detalhes"]["faturacao_categoria"]) == (50.01, 60.03)


def test_a5_limite_exato_e_mes_incompleto(conn):
    cenario(conn, _com_marco_cat01_a("20.00"))                      # exatamente 50%
    verificar(conn)
    assert abertos(conn, "concentracao_faturacao") == []
    cenario(conn, _com_marco_cat01_a("30.00"))                      # 75%, mas março incompleto
    verificar(conn, data_referencia=date(2026, 3, 31))
    assert abertos(conn, "concentracao_faturacao") == []


# ---------------------------------------------------------------- ciclo de vida

def test_repeticao_nao_duplica_e_atualiza(conn):
    cenario(conn, vendas_base(), rejeitadas=4, lidas=100)
    primeira = verificar(conn)
    segunda = verificar(conn)
    assert (primeira["novos"], segunda["novos"], segunda["atualizados"]) == (1, 0, 1)
    [a] = por_chave(conn, "A1:qualidade_dados")[-1:]
    assert a["n_detecoes"] == 2 and a["ultima_detecao"] >= a["primeira_detecao"]
    assert len([x for x in por_chave(conn, "A1:qualidade_dados") if x["estado"] != "resolvido"]) == 1


def test_resolucao_automatica_e_reabertura_como_novo_episodio(conn):
    cenario(conn, vendas_base(), rejeitadas=4, lidas=100)
    verificar(conn)
    cenario(conn, vendas_base(), rejeitadas=1, lidas=100)
    ex = verificar(conn)
    assert ex["resolvidos"] == 1
    resolvido = por_chave(conn, "A1:qualidade_dados")[-1]
    assert resolvido["estado"] == "resolvido" and resolvido["resolvido_em"] is not None
    cenario(conn, vendas_base(), rejeitadas=5, lidas=100)
    verificar(conn)
    *historico, novo = por_chave(conn, "A1:qualidade_dados")
    assert historico[-1]["id"] == resolvido["id"] and historico[-1]["estado"] == "resolvido"
    assert novo["id"] != resolvido["id"] and novo["estado"] == "ativo" and novo["n_detecoes"] == 1


def test_reconhecer_mantem_em_aberto_ate_a_regra_deixar_de_se_verificar(conn):
    cenario(conn, vendas_base(), rejeitadas=4, lidas=100)
    verificar(conn)
    [a] = abertos(conn, "qualidade_dados")
    assert alertas.reconhecer(conn, a["id"]) is True
    assert alertas.reconhecer(conn, a["id"]) is False                # já não está ativo
    verificar(conn)                                                  # a regra continua a verificar-se
    [b] = abertos(conn, "qualidade_dados")
    assert (b["id"], b["estado"], b["n_detecoes"]) == (a["id"], "reconhecido", 2)
    cenario(conn, vendas_base(), rejeitadas=0, lidas=100)
    verificar(conn)
    assert por_chave(conn, "A1:qualidade_dados")[-1]["estado"] == "resolvido"


def test_sem_vendas_nao_resolve_alertas(conn):
    cenario(conn, vendas_base(), rejeitadas=4, lidas=100)
    verificar(conn)
    cenario(conn, [], rejeitadas=0, lidas=0)
    ex = verificar(conn)
    assert ex["estado"] == "sem_dados" and ex["resolvidos"] == 0
    assert len(abertos(conn, "qualidade_dados")) == 1


# ---------------------------------------------------------------- falhas e rollback

def _estado_alertas(conn):
    return conn.execute(text("SELECT id, estado, n_detecoes, ultima_detecao, resolvido_em FROM alertas "
                             "ORDER BY id")).all()


def test_erro_reverte_alteracoes_mas_fica_registado(conn):
    cenario(conn, vendas_base(), rejeitadas=4, lidas=100)
    verificar(conn)
    antes = _estado_alertas(conn)
    cenario(conn, vendas_base(), rejeitadas=0, lidas=100)            # A1 seria resolvido
    conn.execute(text("SET LOCAL techstore.simular_erro_alertas = 'sim'"))
    ex = verificar(conn)
    conn.execute(text("SET LOCAL techstore.simular_erro_alertas = ''"))
    assert ex["estado"] == "erro" and "Erro simulado" in ex["mensagem"] and ex["fim"] is not None
    assert _estado_alertas(conn) == antes                             # resolução desfeita
    assert verificar(conn)["estado"] == "sucesso"                     # a execução seguinte funciona


def test_parametros_invalidos_ficam_registados_como_erro(conn):
    ex = verificar(conn, dias_sem_vendas=0)
    assert ex["estado"] == "erro" and "Parâmetros inválidos" in ex["mensagem"]


def test_funcao_nao_altera_as_tabelas_do_techstore(conn):
    sql = ("SELECT md5(string_agg(t::text, '|' ORDER BY t::text)) FROM {} t")
    tabelas = ["categorias", "produtos", "vendas", "vendas_rejeitadas", "resumo_qualidade"]
    cenario(conn, _com_fevereiro_a("6.99"), rejeitadas=4, lidas=100)
    antes = [conn.execute(text(sql.format(t))).scalar_one() for t in tabelas]
    verificar(conn)
    assert [conn.execute(text(sql.format(t))).scalar_one() for t in tabelas] == antes


# ---------------------------------------------------------------- permissões mínimas

@pytest.mark.parametrize("instrucao", [
    "INSERT INTO alertas (tipo, severidade, chave, titulo, descricao, detalhes, primeira_detecao, ultima_detecao) "
    "VALUES ('venda_atipica', 'info', 'X', 'X', 'X', '{}', now(), now())",
    "UPDATE alertas SET estado = 'resolvido', resolvido_em = now()",
    "DELETE FROM alertas",
    "TRUNCATE alertas_execucoes",
    "INSERT INTO alertas_execucoes (inicio, origem, estado) VALUES (now(), 'manual', 'sucesso')",
])
def test_aplicacao_nao_altera_alertas_diretamente(conn, instrucao):
    with pytest.raises(exc.ProgrammingError, match="permission denied"):
        with conn.begin_nested():
            conn.execute(text(instrucao))


# ---------------------------------------------------------------- nova carga do ETL e dados reais

def test_nova_carga_do_etl_com_dados_reais(conn):
    """Carrega o resultado real do ETL (só nesta transação) e compara com um cálculo independente em pandas."""
    res = pipeline.executar()
    registos = load.preparar_registos(res.produtos, res.categorias, res.tabelas())
    load.substituir_dados(conn, registos)
    hoje = date.today()
    verificar(conn, data_referencia=hoje)

    v = res.vendas_validas.assign(vt=res.vendas_validas["valor_total"].map(float))
    c = res.contagens
    esperado = {"qualidade_dados": int(c["rejeitadas_total"] / c["vendas_lidas"] > 0.03)}
    meses = v.groupby("ano_mes")["vt"].sum()
    completos = [m for m in meses.index if (pd.Period(m, "M") + 1).start_time.date() <= hoje]
    esperado["queda_faturacao"] = sum(1 for a, b in zip(completos, completos[1:])
                                      if pd.Period(b, "M") == pd.Period(a, "M") + 1
                                      and round(meses[b], 2) < round(meses[a], 2) * 0.70)
    fim = v["data_venda"].max().normalize()
    inicio = fim - pd.Timedelta(days=59)
    esperado["produto_sem_vendas"] = len(set(res.produtos["codigo_produto"])
                                         - set(v.loc[v["data_venda"] >= inicio, "codigo_produto"]))
    q = v.groupby("codigo_categoria")["vt"].quantile([0.25, 0.75]).unstack()
    limite = (q[0.75] + 1.5 * (q[0.75] - q[0.25]))[v.groupby("codigo_categoria").size() >= 8]
    esperado["venda_atipica"] = int((v["vt"] > v["codigo_categoria"].map(limite)).sum())
    mc = v[v["ano_mes"].isin(completos)].groupby(["ano_mes", "codigo_categoria"])["vt"].sum()
    esperado["concentracao_faturacao"] = int((mc / mc.groupby(level=0).transform("sum") > 0.5).sum())

    obtido = {t: len(abertos(conn, t)) for t in alertas.TIPOS}
    assert obtido == esperado

    load.substituir_dados(conn, registos)                            # nova carga com os mesmos dados
    ex = verificar(conn, data_referencia=hoje)
    assert (ex["novos"], ex["resolvidos"], ex["atualizados"]) == (0, 0, sum(esperado.values()))


# ---------------------------------------------------------------- leitura pelo dashboard

def test_leitura_dos_alertas_e_execucoes(conn):
    cenario(conn, vendas_base(), rejeitadas=4, lidas=100)
    ex = verificar(conn)
    tabela = alertas.ler_alertas(conn)
    execucoes = alertas.ler_execucoes(conn, 5)
    assert {"id", "tipo", "severidade", "titulo", "descricao", "detalhes", "estado", "primeira_detecao",
            "ultima_detecao", "n_detecoes", "reconhecido_em", "resolvido_em"} <= set(tabela.columns)
    aberto = tabela[(tabela["estado"] == "ativo") & (tabela["tipo"] == "qualidade_dados")].iloc[0]
    assert isinstance(aberto["detalhes"], dict) and aberto["detalhes"]["rejeitadas"] == 4
    assert execucoes.iloc[0]["id"] == ex["id"] and isinstance(execucoes.iloc[0]["inicio"], (pd.Timestamp, datetime))


def test_pagina_centro_de_alertas_abre_sem_erros(engine):
    """Só leitura: abre a página com os alertas reais já existentes na base de dados."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(RAIZ / "src" / "dashboard" / "app.py"), default_timeout=90)
    at.run()
    at.switch_page("alertas.py").run()
    assert not at.exception
    assert at.title[0].value == "Centro de Alertas"
