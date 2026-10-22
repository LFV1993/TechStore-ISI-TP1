-- TechStore - esquema relacional (PostgreSQL)
-- Pode ser executado várias vezes: só cria o que ainda não existe e recria as vistas.

CREATE TABLE IF NOT EXISTS categorias (
    codigo_categoria  VARCHAR(10)  PRIMARY KEY,
    nome_categoria    VARCHAR(100) NOT NULL
);

CREATE TABLE IF NOT EXISTS produtos (
    codigo_produto    VARCHAR(10)  PRIMARY KEY CHECK (codigo_produto ~ '^PRD-[0-9]{4}$'),
    nome_produto      VARCHAR(150) NOT NULL,
    codigo_categoria  VARCHAR(10)  NOT NULL REFERENCES categorias (codigo_categoria)
);

-- Vendas válidas, já normalizadas e integradas.
-- Os campos temporais (ano, mes, ...) são calculados no ETL e guardados para facilitar a análise.
CREATE TABLE IF NOT EXISTS vendas (
    id_venda          VARCHAR(20)   PRIMARY KEY,
    data_venda        DATE          NOT NULL,
    ano               SMALLINT      NOT NULL,
    mes               SMALLINT      NOT NULL CHECK (mes BETWEEN 1 AND 12),
    ano_mes           CHAR(7)       NOT NULL,
    trimestre         SMALLINT      NOT NULL CHECK (trimestre BETWEEN 1 AND 4),
    dia_semana        VARCHAR(15)   NOT NULL,
    codigo_produto    VARCHAR(10)   NOT NULL REFERENCES produtos (codigo_produto),
    codigo_original   VARCHAR(20)   NOT NULL,
    codigo_corrigido  BOOLEAN       NOT NULL,
    quantidade        INTEGER       NOT NULL CHECK (quantidade > 0),
    preco_unitario    NUMERIC(10,2) NOT NULL CHECK (preco_unitario > 0),
    valor_total       NUMERIC(12,2) NOT NULL,
    CONSTRAINT vendas_valor_total_ck CHECK (valor_total = quantidade * preco_unitario)
);

CREATE INDEX IF NOT EXISTS vendas_data_idx ON vendas (data_venda);
CREATE INDEX IF NOT EXISTS vendas_produto_idx ON vendas (codigo_produto);

-- Vendas rejeitadas: os valores ficam como texto, tal como foram lidos, com o motivo da rejeição.
CREATE TABLE IF NOT EXISTS vendas_rejeitadas (
    id                 SERIAL       PRIMARY KEY,
    id_venda           VARCHAR(20),
    data_venda         VARCHAR(20),
    codigo_produto     VARCHAR(20),
    codigo_normalizado VARCHAR(20),
    quantidade         VARCHAR(20),
    preco_unitario     VARCHAR(20),
    etapa              VARCHAR(20)  NOT NULL,
    tipo_rejeicao      VARCHAR(30)  NOT NULL,
    motivo_rejeicao    VARCHAR(200) NOT NULL
);

-- Indicadores de qualidade da última execução do ETL.
CREATE TABLE IF NOT EXISTS resumo_qualidade (
    ordem      SMALLINT      PRIMARY KEY,
    indicador  VARCHAR(50)   NOT NULL UNIQUE,
    descricao  VARCHAR(100)  NOT NULL,
    valor      NUMERIC(14,2) NOT NULL
);

-- Vistas de apoio à análise e ao dashboard
CREATE OR REPLACE VIEW vw_vendas_detalhe AS
SELECT v.id_venda, v.data_venda, v.ano, v.mes, v.ano_mes, v.trimestre, v.dia_semana,
       v.codigo_produto, v.codigo_original, v.codigo_corrigido, p.nome_produto,
       p.codigo_categoria, c.nome_categoria, v.quantidade, v.preco_unitario, v.valor_total
FROM vendas v
JOIN produtos p   ON p.codigo_produto = v.codigo_produto
JOIN categorias c ON c.codigo_categoria = p.codigo_categoria;

CREATE OR REPLACE VIEW vw_faturacao_mensal AS
SELECT ano_mes, COUNT(*) AS n_vendas, SUM(quantidade) AS unidades, SUM(valor_total) AS faturacao
FROM vendas
GROUP BY ano_mes;

CREATE OR REPLACE VIEW vw_vendas_produto AS
SELECT codigo_produto, nome_produto, nome_categoria,
       COUNT(*) AS n_vendas, SUM(quantidade) AS unidades, SUM(valor_total) AS faturacao
FROM vw_vendas_detalhe
GROUP BY codigo_produto, nome_produto, nome_categoria;

CREATE OR REPLACE VIEW vw_vendas_categoria AS
SELECT codigo_categoria, nome_categoria,
       COUNT(*) AS n_vendas, SUM(quantidade) AS unidades, SUM(valor_total) AS faturacao
FROM vw_vendas_detalhe
GROUP BY codigo_categoria, nome_categoria;
