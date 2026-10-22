"""Dashboard TechStore - página "Análise de Vendas" (Streamlit + Plotly).

Página aberta por src/dashboard/app.py (executar a partir da raiz do projeto: streamlit run src/dashboard/app.py).

Fonte dos dados:
- PostgreSQL, quando o ficheiro .env está configurado e a base de dados responde;
- caso contrário, modo de demonstração local com os CSV exportados pelo ETL (pasta data/output/).
As agregações usam as mesmas funções do ETL (techstore.transform), sem regras de negócio duplicadas.
"""
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "src"))

from techstore import database, load, transform  # noqa: E402

# Paleta categórica de referência (ordem fixa; cada categoria mantém sempre a mesma cor)
PALETA = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"],
    "dark": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"],
}


# ---------------------------------------------------------------- dados

@st.cache_data(ttl=300, show_spinner="A carregar os dados...")
def obter_dados() -> tuple[dict, str, str]:
    """Devolve (tabelas, origem, detalhe). Tenta o PostgreSQL e recorre aos CSV do ETL."""
    cfg = database.configuracao_bd(RAIZ)
    motivo = "a base de dados não está configurada (ficheiro .env)"
    if cfg is not None:
        engine = database.criar_engine(cfg)
        ok, msg = database.testar_ligacao(engine)
        if ok:
            try:
                return load.ler_bd(engine), "postgresql", f"{cfg['DB_NAME']} em {cfg['DB_HOST'] or 'localhost'}"
            except Exception as erro:  # noqa: BLE001 - por exemplo, tabelas ainda não carregadas
                motivo = f"erro ao ler a base de dados ({type(erro).__name__})"
        else:
            motivo = f"não foi possível ligar ao PostgreSQL ({msg})"
    pasta = RAIZ / "data" / "output"
    if not (pasta / load.FICHEIROS["vendas_validas"]).exists():
        return {}, "sem_dados", motivo
    return load.ler_csv(pasta), "demo", motivo


def euros(valor) -> str:
    """Formato português: 69 981,01 €"""
    texto = f"{Decimal(valor):,.2f}".replace(",", " ").replace(".", ",")
    return f"{texto} €"


def inteiro(valor) -> str:
    return f"{int(valor):,}".replace(",", " ")


dados, origem, detalhe = obter_dados()

st.title("TechStore - Análise de Vendas")

if origem == "sem_dados":
    st.error("Não há dados tratados. Execute primeiro o ETL na raiz do projeto: `python dataintegration/etl.py`")
    st.stop()
if origem == "demo":
    st.info(f"**Modo de demonstração local** - dados lidos da pasta `data/output/` (CSV exportados pelo ETL), "
            f"porque {detalhe}.")

vendas = dados["vendas_validas"]
rejeitadas = dados["vendas_rejeitadas"]
qualidade = dados["qualidade"]

if vendas.empty:
    st.warning("Não existem vendas válidas para apresentar. Se a base de dados acabou de ser criada, "
               "execute a carga: `python etl.py --bd`.")
    st.stop()

modo = "dark" if getattr(getattr(st.context, "theme", None), "type", "light") == "dark" else "light"
cores = PALETA[modo]
categorias = (vendas[["codigo_categoria", "nome_categoria"]].drop_duplicates()
              .sort_values("codigo_categoria"))
COR_CATEGORIA = {n: cores[i % len(cores)] for i, n in enumerate(categorias["nome_categoria"])}
COR_SERIE = cores[0]

# ---------------------------------------------------------------- filtros

with st.sidebar:
    st.header("Filtros")
    dmin, dmax = vendas["data_venda"].min().date(), vendas["data_venda"].max().date()
    intervalo = st.date_input("Intervalo de datas", value=(dmin, dmax), min_value=dmin, max_value=dmax,
                              format="DD/MM/YYYY")
    inicio, fim = (intervalo if isinstance(intervalo, tuple) and len(intervalo) == 2 else (dmin, dmax))
    escolha_cat = st.multiselect("Categoria", categorias["nome_categoria"].tolist(),
                                 placeholder="Todas as categorias")
    produtos_disp = sorted(vendas.loc[vendas["nome_categoria"].isin(escolha_cat or categorias["nome_categoria"]),
                                      "nome_produto"].unique())
    escolha_prod = st.multiselect("Produto", produtos_disp, placeholder="Todos os produtos")
    st.caption("Os registos rejeitados não dependem dos filtros.")

filtro = vendas["data_venda"].dt.date.between(inicio, fim)
if escolha_cat:
    filtro &= vendas["nome_categoria"].isin(escolha_cat)
if escolha_prod:
    filtro &= vendas["nome_produto"].isin(escolha_prod)
sel = vendas[filtro]

if sel.empty:
    st.warning("Nenhuma venda corresponde aos filtros escolhidos.")
    st.stop()

# ---------------------------------------------------------------- indicadores

k = st.columns(5)
k[0].metric("Faturação total", euros(transform.somar_decimal(sel["valor_total"])))
k[1].metric("Vendas válidas", inteiro(len(sel)))
k[2].metric("Unidades vendidas", inteiro(sel["quantidade"].sum()))
k[3].metric("Produtos vendidos", inteiro(sel["codigo_produto"].nunique()))
k[4].metric("Registos rejeitados", inteiro(len(rejeitadas)), help="Total do ETL, independente dos filtros")

# ---------------------------------------------------------------- gráficos

def para_float(df: pd.DataFrame, colunas=("faturacao",)) -> pd.DataFrame:
    df = df.copy()
    for c in colunas:
        df[c] = df[c].map(float)
    return df


def estilo(fig: go.Figure, altura: int = 360) -> go.Figure:
    fig.update_layout(height=altura, margin=dict(l=10, r=10, t=10, b=10), showlegend=False,
                      hoverlabel=dict(font_size=13), separators=", ")
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridwidth=1)
    return fig


MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def rotulo_mes(ano_mes: str) -> str:
    """'2026-03' -> 'mar/26'"""
    ano, mes = ano_mes.split("-")
    return f"{MESES[int(mes) - 1]}/{ano[2:]}"


def espaco_para_rotulos(fig: go.Figure, maximo: float) -> None:
    """Alarga o eixo horizontal para que os rótulos à direita das barras não fiquem cortados."""
    fig.update_xaxes(range=[0, maximo * 1.28])


c1, c2 = st.columns(2)
with c1:
    st.subheader("Evolução da faturação mensal")
    mes = para_float(transform.agregar_por_mes(sel))
    mes["rotulo"] = mes["ano_mes"].map(rotulo_mes)
    fig = px.line(mes, x="rotulo", y="faturacao", markers=True,
                  labels={"rotulo": "Mês", "faturacao": "Faturação (€)"},
                  custom_data=["n_vendas", "unidades"])
    fig.update_traces(line=dict(width=2, color=COR_SERIE), marker=dict(size=8, color=COR_SERIE),
                      hovertemplate="<b>%{x}</b><br>Faturação: %{y:,.2f} €<br>"
                                    "Vendas: %{customdata[0]}<br>Unidades: %{customdata[1]}<extra></extra>")
    fig.update_xaxes(type="category")
    fig.update_yaxes(rangemode="tozero", tickformat=",.0f")
    st.plotly_chart(estilo(fig), width="stretch")

with c2:
    st.subheader("Top 5 produtos mais vendidos")
    top = para_float(transform.top_produtos(sel, 5)).iloc[::-1]
    fig = px.bar(top, x="unidades", y="nome_produto", orientation="h", text="unidades",
                 labels={"unidades": "Unidades vendidas", "nome_produto": ""},
                 custom_data=["nome_categoria", "faturacao"])
    fig.update_traces(marker_color=COR_SERIE, marker_cornerradius=4, textposition="outside", cliponaxis=False,
                      hovertemplate="<b>%{y}</b><br>%{customdata[0]}<br>Unidades: %{x}<br>"
                                    "Faturação: %{customdata[1]:,.2f} €<extra></extra>")
    fig.update_layout(bargap=0.35)
    espaco_para_rotulos(fig, top["unidades"].max())
    st.plotly_chart(estilo(fig), width="stretch")

c3, c4 = st.columns(2)
cat = para_float(transform.agregar_por_categoria(sel))
with c3:
    st.subheader("Faturação por categoria")
    ordem = cat.sort_values("faturacao")
    fig = px.bar(ordem, x="faturacao", y="nome_categoria", orientation="h", color="nome_categoria",
                 color_discrete_map=COR_CATEGORIA, text=ordem["faturacao"].map(lambda v: euros(Decimal(str(v)))),
                 labels={"faturacao": "Faturação (€)", "nome_categoria": ""},
                 custom_data=["n_vendas", "unidades"])
    fig.update_traces(marker_cornerradius=4, textposition="outside", cliponaxis=False,
                      hovertemplate="<b>%{y}</b><br>Faturação: %{x:,.2f} €<br>Vendas: %{customdata[0]}<br>"
                                    "Unidades: %{customdata[1]}<extra></extra>")
    fig.update_xaxes(tickformat=",.0f")
    fig.update_layout(bargap=0.35)
    espaco_para_rotulos(fig, ordem["faturacao"].max())
    st.plotly_chart(estilo(fig), width="stretch")

with c4:
    st.subheader("Número de vendas por categoria")
    ordem = cat.sort_values("faturacao")  # mesma ordem do gráfico ao lado, para comparar
    ordem["percentagem"] = ordem["n_vendas"] / ordem["n_vendas"].sum() * 100
    fig = px.bar(ordem, x="n_vendas", y="nome_categoria", orientation="h", color="nome_categoria",
                 color_discrete_map=COR_CATEGORIA,
                 text=[f"{n} ({p:.1f}%)".replace(".", ",") for n, p in zip(ordem["n_vendas"], ordem["percentagem"])],
                 labels={"n_vendas": "Número de vendas", "nome_categoria": ""}, custom_data=["unidades"])
    fig.update_traces(marker_cornerradius=4, textposition="outside", cliponaxis=False,
                      hovertemplate="<b>%{y}</b><br>Vendas: %{x}<br>Unidades: %{customdata[0]}<extra></extra>")
    fig.update_layout(bargap=0.35)
    espaco_para_rotulos(fig, ordem["n_vendas"].max())
    st.plotly_chart(estilo(fig), width="stretch")

# ---------------------------------------------------------------- tabelas

st.subheader("Dados")
t1, t2, t3, t4 = st.tabs(["Vendas detalhadas", "Registos rejeitados", "Qualidade dos dados", "Resumo mensal"])
with t1:
    tabela = para_float(sel, ("preco_unitario", "valor_total"))
    st.dataframe(
        tabela[["id_venda", "data_venda", "nome_produto", "nome_categoria", "quantidade", "preco_unitario",
                "valor_total", "codigo_original", "codigo_produto"]],
        hide_index=True, width="stretch",
        column_config={
            "id_venda": "Venda", "data_venda": st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
            "nome_produto": "Produto", "nome_categoria": "Categoria", "quantidade": "Qtd.",
            "preco_unitario": st.column_config.NumberColumn("Preço unitário", format="%.2f €"),
            "valor_total": st.column_config.NumberColumn("Valor total", format="%.2f €"),
            "codigo_original": "Código lido", "codigo_produto": "Código normalizado"})
    st.caption(f"{inteiro(len(sel))} vendas após os filtros.")
with t2:
    st.dataframe(rejeitadas, hide_index=True, width="stretch",
                 column_config={"id_venda": "Venda", "data_venda": "Data (lida)", "codigo_produto": "Código lido",
                                "codigo_normalizado": "Código normalizado", "quantidade": "Qtd. (lida)",
                                "preco_unitario": "Preço (lido)", "etapa": "Etapa", "tipo_rejeicao": None,
                                "motivo_rejeicao": "Motivo"})
    st.caption("Valores tal como foram lidos da fonte, antes de qualquer conversão.")
    por_tipo = (rejeitadas["tipo_rejeicao"].map(transform.TIPOS_REJEICAO).value_counts()
                .rename_axis("Tipo de rejeição").rename("Registos"))
    st.dataframe(por_tipo, width="content")
with t3:
    q = qualidade.copy()
    q["valor"] = [euros(v) if i == "faturacao_total" else inteiro(v) for i, v in zip(q["indicador"], q["valor"])]
    st.dataframe(q[["descricao", "valor"]], hide_index=True, width="content", height=(len(q) + 1) * 35 + 3,
                 column_config={"descricao": "Indicador", "valor": "Valor"})
with t4:
    st.dataframe(para_float(transform.agregar_por_mes(sel)), hide_index=True, width="content",
                 column_config={"ano_mes": "Mês", "n_vendas": "Vendas", "unidades": "Unidades",
                                "faturacao": st.column_config.NumberColumn("Faturação", format="%.2f €")})
