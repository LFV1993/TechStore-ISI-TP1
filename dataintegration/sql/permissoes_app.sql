-- TechStore - passo 3: permissões mínimas do utilizador da aplicação.
-- Executar como administrador, ligado à base de dados techstore, depois de dataintegration/sql/schema.sql:
--   psql -U postgres -d techstore -f dataintegration/sql/permissoes_app.sql
--
-- A aplicação não precisa de criar nem alterar objetos (sem CREATE no schema): o ETL só verifica
-- se o schema existe e substitui o conteúdo das tabelas (TRUNCATE + INSERT numa transação).

GRANT CONNECT ON DATABASE techstore TO techstore_app;
GRANT USAGE ON SCHEMA public TO techstore_app;
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE
    ON categorias, produtos, vendas, vendas_rejeitadas, resumo_qualidade TO techstore_app;
GRANT SELECT ON vw_vendas_detalhe, vw_faturacao_mensal, vw_vendas_produto, vw_vendas_categoria TO techstore_app;
GRANT USAGE ON SEQUENCE vendas_rejeitadas_id_seq TO techstore_app;
