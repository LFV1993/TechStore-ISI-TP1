"""Orquestração do ETL: extração -> transformação -> carregamento.

Utilização (a partir da raiz do projeto):
    python dataintegration/etl.py        # executa o ETL e exporta os resultados para data/output/
    python dataintegration/etl.py --bd   # também carrega os resultados no PostgreSQL (requer .env)
                                         # e, a seguir, executa a verificação de alertas (transação separada)
"""
import argparse
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from techstore import alertas, database, extract, load, transform

RAIZ = Path(__file__).resolve().parents[2]


@dataclass
class Resultados:
    vendas_lidas: pd.DataFrame
    produtos: pd.DataFrame
    categorias: pd.DataFrame
    duplicados: pd.DataFrame
    vendas_validas: pd.DataFrame
    vendas_rejeitadas: pd.DataFrame
    qualidade: pd.DataFrame
    por_mes: pd.DataFrame
    por_produto: pd.DataFrame
    por_categoria: pd.DataFrame
    contagens: dict = field(default_factory=dict)

    def tabelas(self) -> dict[str, pd.DataFrame]:
        return {"vendas_validas": self.vendas_validas, "vendas_rejeitadas": self.vendas_rejeitadas,
                "qualidade": self.qualidade, "por_mes": self.por_mes, "por_produto": self.por_produto,
                "por_categoria": self.por_categoria}


def transformar(vendas: pd.DataFrame, produtos: pd.DataFrame, categorias: pd.DataFrame) -> Resultados:
    """Aplica todas as transformações a dados já extraídos (usado também nos testes)."""
    unicas, duplicados = transform.remover_duplicados(vendas)
    codigos = unicas["codigo_produto"].str.strip()
    normalizados = (codigos != "") & (codigos.map(transform.normalizar_codigo) != unicas["codigo_produto"])

    validas, rejeitadas_validacao = transform.validar_vendas(unicas)
    integradas, rejeitadas_integracao = transform.integrar(validas, produtos, categorias)
    finais = transform.acrescentar_campos(integradas)
    rejeitadas = pd.concat([rejeitadas_validacao, rejeitadas_integracao], ignore_index=True)

    if len(vendas) != len(duplicados) + len(rejeitadas) + len(finais):
        raise AssertionError("reconciliação falhou: lidas != duplicados + rejeitadas + válidas")

    contagens = {
        "vendas_lidas": len(vendas),
        "produtos_lidos": len(produtos),
        "categorias_lidas": len(categorias),
        "duplicados_removidos": len(duplicados),
        "vendas_apos_deduplicacao": len(unicas),
        "codigos_normalizados": int(normalizados.sum()),
        "rejeitadas_validacao": len(rejeitadas_validacao),
        "rejeitadas_integracao": len(rejeitadas_integracao),
        "rejeitadas_total": len(rejeitadas),
        "vendas_validas": len(finais),
        "unidades_vendidas": int(finais["quantidade"].sum()),
        "produtos_vendidos": int(finais["codigo_produto"].nunique()),
        "faturacao_total": transform.somar_decimal(finais["valor_total"]),
    }
    for tipo in transform.TIPOS_REJEICAO:
        contagens[f"rejeicoes_{tipo}"] = int((rejeitadas["tipo_rejeicao"] == tipo).sum())

    return Resultados(vendas, produtos, categorias, duplicados, finais, rejeitadas,
                      resumo_qualidade(contagens), transform.agregar_por_mes(finais),
                      transform.agregar_por_produto(finais), transform.agregar_por_categoria(finais), contagens)


DESCRICOES = {
    "vendas_lidas": "Vendas lidas do CSV",
    "produtos_lidos": "Produtos lidos do JSON",
    "categorias_lidas": "Categorias lidas do XML",
    "duplicados_removidos": "Duplicados exatos removidos",
    "vendas_apos_deduplicacao": "Vendas após remoção de duplicados",
    "codigos_normalizados": "Códigos de produto normalizados",
    "rejeitadas_validacao": "Vendas rejeitadas na validação",
    "rejeitadas_integracao": "Vendas rejeitadas na integração",
    "rejeitadas_total": "Total de vendas rejeitadas",
    "vendas_validas": "Vendas válidas finais",
    "unidades_vendidas": "Unidades vendidas",
    "produtos_vendidos": "Produtos diferentes vendidos",
    "faturacao_total": "Faturação total (EUR)",
}


def resumo_qualidade(contagens: dict) -> pd.DataFrame:
    linhas = [(k, DESCRICOES.get(k) or f"Rejeições - {transform.TIPOS_REJEICAO[k.removeprefix('rejeicoes_')]}", v)
              for k, v in contagens.items()]
    df = pd.DataFrame(linhas, columns=["indicador", "descricao", "valor"])
    df["valor"] = df["valor"].map(lambda v: transform.Decimal(str(v)))
    df.insert(0, "ordem", range(1, len(df) + 1))
    return df


def executar(raiz: Path = RAIZ) -> Resultados:
    dados = raiz / "data" / "input"
    return transformar(extract.ler_vendas(dados / "vendas.csv"),
                       extract.ler_produtos(dados / "produtos.json"),
                       extract.ler_categorias(dados / "categorias.xml"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ETL TechStore")
    parser.add_argument("--bd", action="store_true", help="carregar também no PostgreSQL (configuração em .env)")
    parser.add_argument("--saida", default="data/output", help="pasta dos CSV de resultados (relativa à raiz)")
    args = parser.parse_args(argv)

    res = executar(RAIZ)
    c = res.contagens
    print("== ETL TechStore")
    for k in DESCRICOES:
        print(f"  {DESCRICOES[k]}: {c[k]}")
    print("  Rejeições por tipo:")
    for tipo, descricao in transform.TIPOS_REJEICAO.items():
        print(f"    {descricao}: {c['rejeicoes_' + tipo]}")
    print(f"  Reconciliação: {c['vendas_lidas']} = {c['duplicados_removidos']} duplicados + "
          f"{c['rejeitadas_total']} rejeitadas + {c['vendas_validas']} válidas")

    for f in load.exportar_csv(res.tabelas(), RAIZ / args.saida):
        print(f"  CSV: {f.relative_to(RAIZ)}")

    if args.bd:
        cfg = database.configuracao_bd(RAIZ)
        if cfg is None:
            print("Base de dados não configurada: crie o ficheiro .env a partir de src/.env.example.")
            return 1
        engine = database.criar_engine(cfg)
        print(f"  PostgreSQL: {database.destino(engine)}")
        ok, msg = database.testar_ligacao(engine)
        if not ok:
            print(f"Não foi possível ligar ao PostgreSQL ({msg}).")
            return 1
        em_falta = database.verificar_esquema(engine)
        if em_falta:
            print(f"Schema incompleto (em falta: {', '.join(em_falta)}). "
                  "Execute dataintegration/sql/schema.sql como administrador (ver README).")
            return 1
        try:
            totais = load.carregar_bd(engine, load.preparar_registos(res.produtos, res.categorias, res.tabelas()))
        except Exception as erro:  # noqa: BLE001 - mensagem clara, sem credenciais
            detalhe = str(getattr(erro, "orig", erro)).splitlines()[0]
            print(f"Erro na carga ({type(erro).__name__}: {detalhe}). "
                  "A transação foi revertida e os dados anteriores mantêm-se.")
            return 1
        print("  Carregado:", ", ".join(f"{t}={n}" for t, n in totais.items()))
        verificar_alertas_apos_carga(engine)
    return 0


def verificar_alertas_apos_carga(engine) -> None:
    """Executa verificar_alertas() numa transação separada da carga.

    Uma falha aqui não desfaz a carga (já confirmada): fica registada em alertas_execucoes
    (erro dentro da função) ou é apresentada no ecrã (por exemplo, sistema ainda não instalado).
    """
    try:
        with engine.connect() as conn:
            em_falta = alertas.objetos_em_falta(conn)
        if em_falta:
            print(f"  Alertas: sistema não instalado (em falta: {', '.join(em_falta)}); ver dataintegration/sql/alertas.sql.")
            return
        ex = alertas.executar_verificacao(engine, "etl")
        print(f"  Alertas: execução {ex['id']} {ex['estado']} - novos={ex['novos']}, atualizados={ex['atualizados']}, "
              f"resolvidos={ex['resolvidos']}, em aberto={ex['ativos']}")
        if ex["estado"] == "erro":
            print(f"  AVISO: a verificação de alertas falhou ({ex['mensagem']}). A carga dos dados foi concluída.")
    except Exception as erro:  # noqa: BLE001 - mensagem clara, sem credenciais
        detalhe = str(getattr(erro, "orig", erro)).splitlines()[0]
        print(f"  AVISO: não foi possível verificar os alertas ({type(erro).__name__}: {detalhe}). "
              "A carga dos dados foi concluída.")


if __name__ == "__main__":
    raise SystemExit(main())
