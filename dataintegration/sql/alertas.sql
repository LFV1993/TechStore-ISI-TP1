-- TechStore - sistema de alertas (passo 4 da instalação)
-- Executar como administrador (postgres), ligado à base de dados techstore, depois de dataintegration/sql/schema.sql.
-- Seguir com dataintegration/sql/permissoes_alertas.sql (passo 5).
--
-- Cria:
--   alertas            - ocorrências detetadas pelas regras (uma linha por episódio)
--   alertas_execucoes  - registo de cada verificação (sucesso, erro ou sem dados)
--   verificar_alertas()  - avalia as cinco regras e atualiza os alertas
--   reconhecer_alerta()  - marca um alerta ativo como reconhecido (usado pelo dashboard)
--
-- Pode ser executado mais do que uma vez: as tabelas só são criadas se não existirem e as
-- funções são substituídas. Não apaga dados.

BEGIN;

CREATE TABLE IF NOT EXISTS alertas (
    id                BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tipo              VARCHAR(30)  NOT NULL CHECK (tipo IN ('qualidade_dados', 'queda_faturacao',
                                   'produto_sem_vendas', 'venda_atipica', 'concentracao_faturacao')),
    severidade        VARCHAR(10)  NOT NULL CHECK (severidade IN ('info', 'aviso', 'critico')),
    chave             VARCHAR(100) NOT NULL,   -- identifica a ocorrência, por exemplo 'A3:PRD-0012'
    titulo            VARCHAR(200) NOT NULL,
    descricao         TEXT         NOT NULL,
    detalhes          JSONB        NOT NULL,   -- valores que justificam o alerta
    estado            VARCHAR(12)  NOT NULL DEFAULT 'ativo'
                                   CHECK (estado IN ('ativo', 'reconhecido', 'resolvido')),
    primeira_detecao  TIMESTAMPTZ  NOT NULL,
    ultima_detecao    TIMESTAMPTZ  NOT NULL,
    n_detecoes        INTEGER      NOT NULL DEFAULT 1 CHECK (n_detecoes >= 1),
    reconhecido_em    TIMESTAMPTZ,
    resolvido_em      TIMESTAMPTZ,
    CONSTRAINT alertas_resolucao_ck CHECK ((estado = 'resolvido') = (resolvido_em IS NOT NULL)),
    CONSTRAINT alertas_reconhecimento_ck CHECK (estado <> 'reconhecido' OR reconhecido_em IS NOT NULL)
);

-- No máximo uma ocorrência em aberto (ativa ou reconhecida) por chave. As resolvidas ficam como histórico.
CREATE UNIQUE INDEX IF NOT EXISTS alertas_chave_aberta_uk ON alertas (chave) WHERE estado <> 'resolvido';
CREATE INDEX IF NOT EXISTS alertas_estado_idx ON alertas (estado, tipo);

CREATE TABLE IF NOT EXISTS alertas_execucoes (
    id           BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    inicio       TIMESTAMPTZ  NOT NULL,
    fim          TIMESTAMPTZ,
    origem       VARCHAR(20)  NOT NULL CHECK (origem IN ('agendador', 'etl', 'manual', 'teste')),
    estado       VARCHAR(12)  NOT NULL CHECK (estado IN ('em_curso', 'sucesso', 'erro', 'sem_dados')),
    novos        INTEGER      NOT NULL DEFAULT 0,
    atualizados  INTEGER      NOT NULL DEFAULT 0,
    resolvidos   INTEGER      NOT NULL DEFAULT 0,
    ativos       INTEGER      NOT NULL DEFAULT 0,   -- alertas em aberto no fim da execução
    mensagem     TEXT
);

CREATE INDEX IF NOT EXISTS alertas_execucoes_inicio_idx ON alertas_execucoes (inicio DESC);


-- ---------------------------------------------------------------------------------------------
-- verificar_alertas()
--
-- Avalia as regras sobre os dados já carregados e:
--   - cria alertas novos e atualiza os que continuam a verificar-se (sem duplicados);
--   - resolve os alertas em aberto cuja regra deixou de se verificar, apenas para as regras que
--     puderam ser avaliadas (sem dados suficientes, os alertas dessa regra mantêm-se);
--   - regista a execução em alertas_execucoes.
-- Se ocorrer um erro, as alterações aos alertas são desfeitas (bloco EXCEPTION) e a execução fica
-- registada com estado 'erro' e a mensagem. Não altera as tabelas de vendas.
-- p_data_referencia define o que é um "mês completo" (terminado antes desta data); por omissão é hoje.
-- A variável de sessão techstore.simular_erro_alertas = 'sim' provoca um erro no fim do processamento,
-- apenas para testar o registo de falhas e o rollback (não tem efeito se não estiver definida).
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION verificar_alertas(
    p_origem              TEXT    DEFAULT 'manual',
    p_data_referencia     DATE    DEFAULT current_date,
    p_limite_rejeicao     NUMERIC DEFAULT 0.03,   -- A1: rejeitadas / vendas lidas
    p_limite_queda        NUMERIC DEFAULT 0.30,   -- A2: queda entre meses completos consecutivos
    p_dias_sem_vendas     INTEGER DEFAULT 60,     -- A3: janela que termina na última data de venda
    p_fator_iqr           NUMERIC DEFAULT 1.5,    -- A4: limite Q3 + fator * IQR, por categoria
    p_min_vendas_iqr      INTEGER DEFAULT 8,      -- A4: mínimo de vendas por categoria
    p_limite_concentracao NUMERIC DEFAULT 0.50    -- A5: quota de uma categoria num mês completo
) RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_exec        BIGINT;
    v_agora       TIMESTAMPTZ := now();
    v_cand        JSONB  := '[]'::jsonb;   -- ocorrências detetadas nesta execução
    v_avaliadas   TEXT[] := '{}';          -- regras que tinham dados suficientes
    v_x           JSONB;
    v_n           INTEGER;
    v_novos       INTEGER := 0;
    v_atualizados INTEGER := 0;
    v_resolvidos  INTEGER := 0;
    v_lidas       NUMERIC;
    v_duplicados  NUMERIC;
    v_rejeitadas  INTEGER;
    v_taxa        NUMERIC;
    v_motivos     JSONB;
    v_min_data    DATE;
    v_max_data    DATE;
    v_inicio      DATE;
    v_msg         TEXT;
    v_sqlstate    TEXT;
BEGIN
    -- Uma verificação de cada vez (por exemplo, o job e o ETL em simultâneo).
    PERFORM pg_advisory_xact_lock(hashtext('techstore.verificar_alertas'));

    INSERT INTO alertas_execucoes (inicio, origem, estado)
    VALUES (clock_timestamp(), p_origem, 'em_curso')
    RETURNING id INTO v_exec;

    BEGIN
        IF p_data_referencia IS NULL
           OR p_limite_rejeicao IS NULL OR p_limite_rejeicao NOT BETWEEN 0 AND 1
           OR p_limite_queda IS NULL OR p_limite_queda NOT BETWEEN 0 AND 1
           OR p_dias_sem_vendas IS NULL OR p_dias_sem_vendas < 1
           OR p_fator_iqr IS NULL OR p_fator_iqr < 0
           OR p_min_vendas_iqr IS NULL OR p_min_vendas_iqr < 4
           OR p_limite_concentracao IS NULL OR p_limite_concentracao NOT BETWEEN 0 AND 1 THEN
            RAISE EXCEPTION 'Parâmetros inválidos em verificar_alertas()';
        END IF;

        -- Sem vendas (por exemplo, base de dados ainda por carregar): não avalia nem resolve nada.
        IF NOT EXISTS (SELECT 1 FROM vendas) THEN
            UPDATE alertas_execucoes
               SET estado = 'sem_dados', fim = clock_timestamp(),
                   ativos = (SELECT count(*) FROM alertas WHERE estado <> 'resolvido'),
                   mensagem = 'Tabela vendas vazia: regras não avaliadas e alertas mantidos.'
             WHERE id = v_exec;
            RETURN v_exec;
        END IF;

        -- A1 - Qualidade dos dados: rejeitadas / vendas lidas. Os duplicados removidos não são rejeições.
        SELECT max(valor) FILTER (WHERE indicador = 'vendas_lidas'),
               max(valor) FILTER (WHERE indicador = 'duplicados_removidos')
          INTO v_lidas, v_duplicados
          FROM resumo_qualidade;
        SELECT count(*) INTO v_rejeitadas FROM vendas_rejeitadas;

        IF coalesce(v_lidas, 0) > 0 THEN
            v_avaliadas := v_avaliadas || 'qualidade_dados'::text;
            v_taxa := v_rejeitadas / v_lidas;
            IF v_taxa > p_limite_rejeicao THEN
                SELECT coalesce(jsonb_agg(jsonb_build_object('tipo_rejeicao', tipo_rejeicao, 'registos', n)
                                          ORDER BY n DESC, tipo_rejeicao), '[]'::jsonb)
                  INTO v_motivos
                  FROM (SELECT tipo_rejeicao, count(*) AS n FROM vendas_rejeitadas
                        GROUP BY tipo_rejeicao ORDER BY n DESC, tipo_rejeicao LIMIT 3) m;
                v_cand := v_cand || jsonb_build_array(jsonb_build_object(
                    'tipo', 'qualidade_dados',
                    'severidade', CASE WHEN v_taxa > 2 * p_limite_rejeicao THEN 'critico' ELSE 'aviso' END,
                    'chave', 'A1:qualidade_dados',
                    'titulo', 'Taxa de rejeição acima do limite',
                    'descricao', format('%s de %s vendas lidas foram rejeitadas (%s%%), acima do limite de %s%%. '
                                        'Os %s duplicados removidos não contam como rejeições.',
                                        v_rejeitadas, v_lidas::int, round(v_taxa * 100, 2),
                                        round(p_limite_rejeicao * 100, 2), coalesce(v_duplicados, 0)::int),
                    'detalhes', jsonb_build_object(
                        'vendas_lidas', v_lidas::int,
                        'duplicados_removidos', coalesce(v_duplicados, 0)::int,
                        'rejeitadas', v_rejeitadas,
                        'taxa_rejeicao_pct', round(v_taxa * 100, 2),
                        'limite_pct', round(p_limite_rejeicao * 100, 2),
                        'principais_motivos', v_motivos)));
            END IF;
        END IF;

        -- A2 - Queda de faturação entre dois meses completos consecutivos, ambos com vendas.
        WITH m AS (
            SELECT date_trunc('month', data_venda::timestamp)::date AS mes, sum(valor_total) AS fat
              FROM vendas GROUP BY 1
        ), completos AS (
            SELECT mes, fat FROM m WHERE (mes + interval '1 month')::date <= p_data_referencia
        ), pares AS (
            SELECT a.mes AS mes_ant, a.fat AS fat_ant, b.mes AS mes_atual, b.fat AS fat_atual,
                   (b.fat - a.fat) / a.fat AS variacao
              FROM completos a JOIN completos b ON b.mes = (a.mes + interval '1 month')::date
        )
        SELECT count(*),
               coalesce(jsonb_agg(jsonb_build_object(
                   'tipo', 'queda_faturacao',
                   'severidade', 'aviso',
                   'chave', format('A2:%s>%s', to_char(mes_ant, 'YYYY-MM'), to_char(mes_atual, 'YYYY-MM')),
                   'titulo', format('Queda de faturação de %s%% em %s', round(-variacao * 100, 1), to_char(mes_atual, 'YYYY-MM')),
                   'descricao', format('A faturação passou de %s EUR (%s) para %s EUR (%s): diferença de %s EUR (%s%%), '
                                       'acima do limite de queda de %s%%.',
                                       fat_ant, to_char(mes_ant, 'YYYY-MM'), fat_atual, to_char(mes_atual, 'YYYY-MM'),
                                       fat_atual - fat_ant, round(variacao * 100, 2), round(p_limite_queda * 100, 2)),
                   'detalhes', jsonb_build_object(
                       'mes_anterior', to_char(mes_ant, 'YYYY-MM'), 'faturacao_anterior', fat_ant,
                       'mes_atual', to_char(mes_atual, 'YYYY-MM'), 'faturacao_atual', fat_atual,
                       'diferenca', fat_atual - fat_ant, 'variacao_pct', round(variacao * 100, 2),
                       'limite_pct', round(p_limite_queda * 100, 2)))
                   ORDER BY mes_atual) FILTER (WHERE -variacao > p_limite_queda), '[]'::jsonb)
          INTO v_n, v_x
          FROM pares;
        IF v_n > 0 THEN
            v_avaliadas := v_avaliadas || 'queda_faturacao'::text;
            v_cand := v_cand || v_x;
        END IF;

        -- A3 - Produtos sem vendas nos últimos p_dias_sem_vendas dias, contados até à última data de venda.
        -- Só avalia se os dados cobrirem a janela inteira (para não confundir falta de dados com falta de vendas).
        SELECT min(data_venda), max(data_venda) INTO v_min_data, v_max_data FROM vendas;
        v_inicio := v_max_data - (p_dias_sem_vendas - 1);
        IF v_min_data <= v_inicio THEN
            v_avaliadas := v_avaliadas || 'produto_sem_vendas'::text;
            SELECT coalesce(jsonb_agg(jsonb_build_object(
                       'tipo', 'produto_sem_vendas',
                       'severidade', 'info',
                       'chave', 'A3:' || p.codigo_produto,
                       'titulo', format('Produto sem vendas há %s dias: %s', p_dias_sem_vendas, p.nome_produto),
                       'descricao', format('%s (%s) não teve vendas entre %s e %s. %s',
                                           p.nome_produto, p.codigo_produto, v_inicio, v_max_data,
                                           CASE WHEN u.ultima IS NULL THEN 'Não tem nenhuma venda registada.'
                                                ELSE format('Última venda: %s.', u.ultima) END),
                       'detalhes', jsonb_build_object(
                           'codigo_produto', p.codigo_produto, 'nome_produto', p.nome_produto,
                           'periodo_inicio', v_inicio, 'periodo_fim', v_max_data, 'dias', p_dias_sem_vendas,
                           'ultima_venda', u.ultima,
                           'dias_sem_vendas', CASE WHEN u.ultima IS NULL THEN NULL ELSE v_max_data - u.ultima END))
                       ORDER BY p.codigo_produto), '[]'::jsonb)
              INTO v_x
              FROM produtos p
              LEFT JOIN (SELECT codigo_produto, max(data_venda) AS ultima FROM vendas GROUP BY 1) u
                     ON u.codigo_produto = p.codigo_produto
             WHERE u.ultima IS NULL OR u.ultima < v_inicio;
            v_cand := v_cand || v_x;
        END IF;

        -- A4 - Vendas atípicas: valor_total acima de Q3 + fator * IQR da respetiva categoria.
        WITH v AS (
            SELECT v.id_venda, v.data_venda, v.quantidade, v.preco_unitario, v.valor_total,
                   p.nome_produto, c.codigo_categoria, c.nome_categoria
              FROM vendas v
              JOIN produtos p ON p.codigo_produto = v.codigo_produto
              JOIN categorias c ON c.codigo_categoria = p.codigo_categoria
        ), estat AS (
            SELECT codigo_categoria, count(*) AS n,
                   percentile_cont(0.25) WITHIN GROUP (ORDER BY valor_total)::numeric AS q1,
                   percentile_cont(0.75) WITHIN GROUP (ORDER BY valor_total)::numeric AS q3
              FROM v GROUP BY codigo_categoria
        ), limites AS (
            SELECT codigo_categoria, n, q1, q3, q3 + p_fator_iqr * (q3 - q1) AS limite
              FROM estat WHERE n >= p_min_vendas_iqr
        )
        SELECT (SELECT count(*) FROM limites),
               coalesce(jsonb_agg(jsonb_build_object(
                   'tipo', 'venda_atipica',
                   'severidade', 'info',
                   'chave', 'A4:' || v.id_venda,
                   'titulo', format('Venda atípica %s: %s EUR em %s', v.id_venda, v.valor_total, v.nome_categoria),
                   'descricao', format('A venda %s (%s, %s) de %s x %s EUR = %s EUR excede o limite estatístico da '
                                       'categoria %s (%s EUR). É invulgar, mas não é inválida.',
                                       v.id_venda, v.data_venda, v.nome_produto, v.quantidade, v.preco_unitario,
                                       v.valor_total, v.nome_categoria, round(l.limite, 2)),
                   'detalhes', jsonb_build_object(
                       'id_venda', v.id_venda, 'data_venda', v.data_venda, 'produto', v.nome_produto,
                       'categoria', v.nome_categoria, 'quantidade', v.quantidade,
                       'preco_unitario', v.preco_unitario, 'valor_total', v.valor_total,
                       'q1', round(l.q1, 2), 'q3', round(l.q3, 2), 'iqr', round(l.q3 - l.q1, 2),
                       'fator_iqr', p_fator_iqr, 'limite', round(l.limite, 2), 'vendas_na_categoria', l.n))
                   ORDER BY v.id_venda), '[]'::jsonb)
          INTO v_n, v_x
          FROM v JOIN limites l ON l.codigo_categoria = v.codigo_categoria
         WHERE v.valor_total > l.limite;
        IF v_n > 0 THEN
            v_avaliadas := v_avaliadas || 'venda_atipica'::text;
            v_cand := v_cand || v_x;
        END IF;

        -- A5 - Concentração: uma categoria com mais de p_limite_concentracao da faturação de um mês completo.
        WITH mc AS (
            SELECT date_trunc('month', v.data_venda::timestamp)::date AS mes, p.codigo_categoria, sum(v.valor_total) AS fat
              FROM vendas v JOIN produtos p ON p.codigo_produto = v.codigo_produto
             GROUP BY 1, 2
        ), mt AS (
            SELECT mes, sum(fat) AS total FROM mc
             WHERE (mes + interval '1 month')::date <= p_data_referencia
             GROUP BY mes
        )
        SELECT (SELECT count(*) FROM mt),
               coalesce(jsonb_agg(jsonb_build_object(
                   'tipo', 'concentracao_faturacao',
                   'severidade', 'aviso',
                   'chave', format('A5:%s:%s', to_char(mc.mes, 'YYYY-MM'), mc.codigo_categoria),
                   'titulo', format('%s concentra %s%% da faturação de %s', c.nome_categoria,
                                    round(mc.fat / mt.total * 100, 1), to_char(mc.mes, 'YYYY-MM')),
                   'descricao', format('Em %s, a categoria %s faturou %s EUR de um total de %s EUR (%s%%), '
                                       'acima do limite de %s%%.',
                                       to_char(mc.mes, 'YYYY-MM'), c.nome_categoria, mc.fat, mt.total,
                                       round(mc.fat / mt.total * 100, 2), round(p_limite_concentracao * 100, 2)),
                   'detalhes', jsonb_build_object(
                       'mes', to_char(mc.mes, 'YYYY-MM'), 'codigo_categoria', mc.codigo_categoria,
                       'categoria', c.nome_categoria, 'faturacao_categoria', mc.fat, 'faturacao_total', mt.total,
                       'quota_pct', round(mc.fat / mt.total * 100, 2),
                       'limite_pct', round(p_limite_concentracao * 100, 2)))
                   ORDER BY mc.mes, mc.codigo_categoria), '[]'::jsonb)
          INTO v_n, v_x
          FROM mc
          JOIN mt ON mt.mes = mc.mes
          JOIN categorias c ON c.codigo_categoria = mc.codigo_categoria
         WHERE mt.total > 0 AND mc.fat / mt.total > p_limite_concentracao;
        IF v_n > 0 THEN
            v_avaliadas := v_avaliadas || 'concentracao_faturacao'::text;
            v_cand := v_cand || v_x;
        END IF;

        -- Criar ou atualizar as ocorrências detetadas (no máximo uma em aberto por chave).
        WITH cand AS (
            SELECT * FROM jsonb_to_recordset(v_cand)
                AS x(tipo TEXT, severidade TEXT, chave TEXT, titulo TEXT, descricao TEXT, detalhes JSONB)
        ), gravados AS (
            INSERT INTO alertas (tipo, severidade, chave, titulo, descricao, detalhes, estado,
                                 primeira_detecao, ultima_detecao, n_detecoes)
            SELECT tipo, severidade, chave, titulo, descricao, detalhes, 'ativo', v_agora, v_agora, 1
              FROM cand
            ON CONFLICT (chave) WHERE estado <> 'resolvido' DO UPDATE
               SET severidade     = EXCLUDED.severidade,
                   titulo         = EXCLUDED.titulo,
                   descricao      = EXCLUDED.descricao,
                   detalhes       = EXCLUDED.detalhes,
                   ultima_detecao = EXCLUDED.ultima_detecao,
                   n_detecoes     = alertas.n_detecoes + 1
            RETURNING (xmax = 0) AS inserido
        )
        SELECT count(*) FILTER (WHERE inserido), count(*) FILTER (WHERE NOT inserido)
          INTO v_novos, v_atualizados
          FROM gravados;

        -- Resolver as ocorrências em aberto que deixaram de se verificar (só regras avaliadas).
        UPDATE alertas a
           SET estado = 'resolvido', resolvido_em = v_agora
         WHERE a.estado <> 'resolvido'
           AND a.tipo = ANY (v_avaliadas)
           AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_cand) e WHERE e ->> 'chave' = a.chave);
        GET DIAGNOSTICS v_resolvidos = ROW_COUNT;

        -- Ponto de teste: permite verificar o registo de falhas e o rollback das alterações acima.
        IF current_setting('techstore.simular_erro_alertas', true) = 'sim' THEN
            RAISE EXCEPTION 'Erro simulado para teste (techstore.simular_erro_alertas)';
        END IF;

        UPDATE alertas_execucoes
           SET estado = 'sucesso', fim = clock_timestamp(),
               novos = v_novos, atualizados = v_atualizados, resolvidos = v_resolvidos,
               ativos = (SELECT count(*) FROM alertas WHERE estado <> 'resolvido'),
               mensagem = 'Regras avaliadas: ' || coalesce(nullif(array_to_string(v_avaliadas, ', '), ''), 'nenhuma')
         WHERE id = v_exec;
    EXCEPTION WHEN OTHERS THEN
        -- As alterações feitas dentro deste bloco foram desfeitas; o registo da execução mantém-se.
        GET STACKED DIAGNOSTICS v_msg = MESSAGE_TEXT, v_sqlstate = RETURNED_SQLSTATE;
        UPDATE alertas_execucoes
           SET estado = 'erro', fim = clock_timestamp(),
               ativos = (SELECT count(*) FROM alertas WHERE estado <> 'resolvido'),
               mensagem = v_sqlstate || ': ' || v_msg
         WHERE id = v_exec;
    END;

    RETURN v_exec;
END;
$$;

COMMENT ON FUNCTION verificar_alertas(TEXT, DATE, NUMERIC, NUMERIC, INTEGER, NUMERIC, INTEGER, NUMERIC) IS
    'TechStore: avalia as regras de alertas A1-A5 e regista a execução em alertas_execucoes.';


-- ---------------------------------------------------------------------------------------------
-- reconhecer_alerta(): marca um alerta ativo como reconhecido. Não o resolve: a resolução é feita
-- apenas por verificar_alertas() quando a regra deixa de se verificar. Devolve false se o alerta
-- não existir ou não estiver ativo.
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION reconhecer_alerta(p_id BIGINT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    UPDATE alertas SET estado = 'reconhecido', reconhecido_em = now()
     WHERE id = p_id AND estado = 'ativo';
    RETURN FOUND;
END;
$$;

COMMENT ON FUNCTION reconhecer_alerta(BIGINT) IS
    'TechStore: marca um alerta ativo como reconhecido (a resolução é sempre automática).';

COMMIT;
