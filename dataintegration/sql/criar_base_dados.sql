-- TechStore - passo 1: criação do utilizador da aplicação e da base de dados.
-- Executar uma vez como administrador (utilizador postgres), ligado à base de dados "postgres":
--   psql -U postgres -d postgres -f dataintegration/sql/criar_base_dados.sql
-- Substitua ALTERAR_PASSWORD por uma password à sua escolha e use a mesma no ficheiro .env.
--
-- A base de dados, as tabelas e as vistas pertencem ao administrador. O utilizador da aplicação
-- (techstore_app) recebe apenas as permissões necessárias para ler e carregar dados (passo 3).
--
-- Passos seguintes, ligado à base de dados techstore e também como administrador:
--   2. psql -U postgres -d techstore -f dataintegration/sql/schema.sql
--   3. psql -U postgres -d techstore -f dataintegration/sql/permissoes_app.sql

CREATE ROLE techstore_app WITH LOGIN PASSWORD 'ALTERAR_PASSWORD';
-- TEMPLATE template0 garante a codificação UTF8 mesmo que template1 use outra (comum em Windows).
CREATE DATABASE techstore ENCODING 'UTF8' TEMPLATE template0;
