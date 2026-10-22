# TechStore - Integração e Análise de Dados de Vendas

- **Aluno:** André Daniel Freitas Ferreira, n.º 20764
- **Unidade curricular:** Integração de Sistemas de Informação (Licenciatura em Engenharia de Sistemas Informáticos, 2026/2027)
- **Trabalho:** Trabalho Prático I (individual)
- **Relatório:** [doc/20764_doc.pdf](doc/20764_doc.pdf)
- **Vídeo de demonstração:** <https://youtu.be/N6skA5iyNMo>

## Vídeo de demonstração

O vídeo tem cerca de 5 minutos e mostra o ETL a funcionar, a carga no PostgreSQL, o dashboard e os alertas: [https://youtu.be/N6skA5iyNMo](https://youtu.be/N6skA5iyNMo)

## Repositório GitHub

O código e os restantes ficheiros do projeto estão em: [https://github.com/LFV1993/TechStore-ISI-TP1](https://github.com/LFV1993/TechStore-ISI-TP1)

## Objetivo

A TechStore é uma loja fictícia de informática. Os dados das vendas estão em três ficheiros com formatos diferentes: vendas em CSV, produtos em JSON e categorias em XML. Também têm vários erros: duplicados, códigos mal escritos, campos vazios e datas inválidas.

O trabalho junta estes dados num processo ETL em Python: lê os três ficheiros, limpa e valida as vendas, cruza as vendas com os produtos e as categorias e carrega o resultado em CSV e numa base de dados PostgreSQL. Na base de dados há também um sistema de alertas com cinco regras. Os resultados e os alertas podem ser consultados num dashboard em Streamlit.

**Tecnologias:** Python 3.11 (pandas), PostgreSQL 18, Streamlit e Plotly, pytest.

## Estrutura

```text
tp01-20764/
  doc/               relatório em PDF
  dataintegration/   etl.py, executar_alertas.py, scripts SQL e scripts do agendamento
  data/input/        ficheiros originais (CSV, JSON e XML)
  data/output/       CSV gerados pelo ETL
  src/               módulos Python, dashboard, testes e requirements.txt
```

## Como executar

Os comandos são para PowerShell, a partir da pasta `tp01-20764`.

### 1. Instalar as dependências

É preciso ter o Python 3.11 instalado. Criar o ambiente virtual:

```powershell
py -3.11 -m venv .venv
```

Instalar as dependências:

```powershell
.venv\Scripts\python.exe -m pip install -r src\requirements.txt
```

### 2. Executar o ETL sem base de dados

```powershell
.venv\Scripts\python.exe dataintegration\etl.py
```

Os resultados ficam em `data/output`.

### 3. Configurar o PostgreSQL

Só é preciso fazer uma vez, com o utilizador administrador `postgres`.

Primeiro, trocar `ALTERAR_PASSWORD` em `dataintegration\sql\criar_base_dados.sql` por uma password à escolha. Esta é a password do utilizador da aplicação, `techstore_app`, que o script cria.

Guardar o caminho do `psql`:

```powershell
$psql = "C:\Program Files\PostgreSQL\18\bin\psql.exe"
```

Criar a base de dados `techstore` e o utilizador `techstore_app`:

```powershell
& $psql -U postgres -d postgres -f dataintegration\sql\criar_base_dados.sql
```

Criar as tabelas, as permissões e o sistema de alertas:

```powershell
foreach ($f in "schema", "permissoes_app", "alertas", "permissoes_alertas") { & $psql -U postgres -d techstore -f "dataintegration\sql\$f.sql" }
```

Por fim, criar na raiz do projeto (pasta `tp01-20764`) um ficheiro de texto chamado `.env` com este conteúdo:

```text
DB_HOST=localhost
DB_PORT=5432
DB_NAME=techstore
DB_USER=techstore_app
DB_PASSWORD=
```

Em `DB_PASSWORD`, escrever a password definida para o `techstore_app` no passo anterior (não é a password do `postgres`).

### 4. Executar o ETL com carga na base de dados

```powershell
.venv\Scripts\python.exe dataintegration\etl.py --bd
```

No fim da carga, os alertas são verificados automaticamente.

### 5. Abrir o dashboard

```powershell
.venv\Scripts\python.exe -m streamlit run src\dashboard\app.py
```

Abre em <http://localhost:8501>. Tem duas páginas: Análise de Vendas e Centro de Alertas. Sem base de dados, a primeira página usa os CSV de `data/output`.

### 6. Verificar os alertas manualmente (opcional)

```powershell
.venv\Scripts\python.exe dataintegration\executar_alertas.py
```

### 7. Testes (opcional)

```powershell
.venv\Scripts\python.exe -m pytest src\tests
```

Sem base de dados, os testes que precisam do PostgreSQL são ignorados.

## Notas

- Os scripts da pasta `dataintegration\agendamento` criariam uma tarefa no Agendador do Windows para verificar os alertas todos os dias. Ficaram preparados, mas a tarefa não foi instalada.
- O ficheiro `.env` tem a password da base de dados e não está incluído na entrega.
