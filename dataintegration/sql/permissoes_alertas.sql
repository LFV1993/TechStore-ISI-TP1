-- TechStore - permissões do sistema de alertas (passo 5 da instalação)
-- Executar como administrador (postgres), ligado à base de dados techstore, depois de dataintegration/sql/alertas.sql.
--
-- Modelo de permissões mínimas:
--   techstore_alertas (NOLOGIN) - dono das funções verificar_alertas() e reconhecer_alerta().
--       Não permite iniciar sessão. Pode ler as tabelas do TechStore e inserir/atualizar alertas.
--       As funções são SECURITY DEFINER: executam com estes privilégios, e só com estes.
--   techstore_app (aplicação) - pode apenas LER os alertas e as execuções e EXECUTAR as duas funções.
--       Não pode inserir, alterar ou apagar alertas diretamente: a criação e a resolução são feitas
--       exclusivamente pelas regras SQL, e o reconhecimento pela função reconhecer_alerta().
--
-- Pode ser executado mais do que uma vez.

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'techstore_alertas') THEN
        CREATE ROLE techstore_alertas NOLOGIN;
    END IF;
END;
$$;

-- Privilégios do dono das funções
GRANT USAGE ON SCHEMA public TO techstore_alertas;
GRANT SELECT ON categorias, produtos, vendas, vendas_rejeitadas, resumo_qualidade TO techstore_alertas;
GRANT SELECT, INSERT, UPDATE ON alertas, alertas_execucoes TO techstore_alertas;

ALTER FUNCTION verificar_alertas(TEXT, DATE, NUMERIC, NUMERIC, INTEGER, NUMERIC, INTEGER, NUMERIC)
    OWNER TO techstore_alertas;
ALTER FUNCTION reconhecer_alerta(BIGINT) OWNER TO techstore_alertas;

-- Por omissão, qualquer utilizador pode executar funções novas: retirar e dar só à aplicação.
REVOKE ALL ON FUNCTION verificar_alertas(TEXT, DATE, NUMERIC, NUMERIC, INTEGER, NUMERIC, INTEGER, NUMERIC) FROM PUBLIC;
REVOKE ALL ON FUNCTION reconhecer_alerta(BIGINT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION verificar_alertas(TEXT, DATE, NUMERIC, NUMERIC, INTEGER, NUMERIC, INTEGER, NUMERIC)
    TO techstore_app;
GRANT EXECUTE ON FUNCTION reconhecer_alerta(BIGINT) TO techstore_app;

-- A aplicação (ETL, job e dashboard) só lê os alertas.
GRANT SELECT ON alertas, alertas_execucoes TO techstore_app;

COMMIT;
