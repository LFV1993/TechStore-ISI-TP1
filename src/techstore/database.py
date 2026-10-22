"""Ligação ao PostgreSQL através de variáveis de ambiente (ficheiro .env na raiz do projeto)."""
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine

VARIAVEIS = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]


def configuracao_bd(raiz: Path) -> dict | None:
    """Lê a configuração de .env (se existir) e das variáveis de ambiente.

    Devolve None quando a base de dados não está configurada (DB_NAME ou DB_USER em falta).
    """
    load_dotenv(raiz / ".env", override=False)
    cfg = {v: os.getenv(v, "") for v in VARIAVEIS}
    if not cfg["DB_NAME"] or not cfg["DB_USER"]:
        return None
    return cfg


def criar_engine(cfg: dict) -> Engine:
    url = URL.create(
        "postgresql+psycopg",
        username=cfg["DB_USER"],
        password=cfg["DB_PASSWORD"] or None,
        host=cfg["DB_HOST"] or "localhost",
        port=int(cfg["DB_PORT"] or 5432),
        database=cfg["DB_NAME"],
    )
    # connect_timeout: sem limite, uma ligação a um servidor indisponível pode ficar à espera durante minutos
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})


def testar_ligacao(engine: Engine) -> tuple[bool, str]:
    """Tenta executar SELECT 1. Devolve (sucesso, mensagem)."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "ligação estabelecida"
    except Exception as erro:  # noqa: BLE001 - a mensagem é apresentada ao utilizador
        return False, f"{type(erro).__name__}: {erro}".splitlines()[0]


TABELAS = ["categorias", "produtos", "vendas", "vendas_rejeitadas", "resumo_qualidade"]
VISTAS = ["vw_vendas_detalhe", "vw_faturacao_mensal", "vw_vendas_produto", "vw_vendas_categoria"]


def destino(engine: Engine) -> str:
    """Descrição da ligação sem a password (para mensagens e registos)."""
    return engine.url.render_as_string(hide_password=True)


def verificar_esquema(engine: Engine) -> list[str]:
    """Devolve as tabelas e vistas em falta no schema public (lista vazia se estiver tudo criado).

    O schema é criado à parte, por um administrador, com dataintegration/sql/schema.sql. A aplicação apenas o verifica,
    porque o utilizador da aplicação não tem permissões de DDL.
    """
    with engine.connect() as conn:
        existentes = {r[0] for r in conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"))}
    return [o for o in TABELAS + VISTAS if o not in existentes]
