"""Dashboard TechStore - página "Centro de Alertas".

Os alertas são gerados no PostgreSQL pela função verificar_alertas() (após cada carga do ETL, por execução
manual ou por um job agendado, se estiver configurado).
Esta página apenas os consulta. A única ação disponível é "Reconhecer", que chama a função SQL
reconhecer_alerta(): o alerta continua em aberto e só é resolvido pelas regras SQL quando deixar de se verificar.
"""
import json
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "src"))

from techstore import alertas, database  # noqa: E402

FUSO = "Europe/Lisbon"
ORDEM_SEVERIDADE = ["critico", "aviso", "info"]
# Cores de estado (reservadas para a severidade e sempre acompanhadas do texto)
COR_SEVERIDADE = {"critico": "#d03b3b", "aviso": "#fab219", "info": "#8a8984"}
COR_SERIE = {"light": "#2a78d6", "dark": "#3987e5"}

st.title("Centro de Alertas")

if database.configuracao_bd(RAIZ) is None:
    st.info("O Centro de Alertas requer o PostgreSQL configurado (ficheiro `.env`). Os alertas existem apenas "
            "na base de dados e não são calculados no dashboard.")
    st.stop()


@st.cache_resource
def obter_engine():
    return database.criar_engine(database.configuracao_bd(RAIZ))


@st.cache_data(ttl=30, show_spinner="A carregar os alertas...")
def carregar() -> tuple[pd.DataFrame | None, pd.DataFrame | None, list[str]]:
    with obter_engine().connect() as conn:
        em_falta = alertas.objetos_em_falta(conn)
        if em_falta:
            return None, None, em_falta
        return alertas.ler_alertas(conn), alertas.ler_execucoes(conn, 50), []


try:
    tabela, execucoes, em_falta = carregar()
except Exception as erro:  # noqa: BLE001 - mensagem clara, sem credenciais
    st.error(f"Não foi possível ler os alertas do PostgreSQL ({type(erro).__name__}).")
    st.stop()
if em_falta:
    st.warning(f"O sistema de alertas ainda não está instalado na base de dados (em falta: {', '.join(em_falta)}). "
               "Ver `dataintegration/sql/alertas.sql`.")
    st.stop()


def local(serie: pd.Series) -> pd.Series:
    return pd.to_datetime(serie, utc=True).dt.tz_convert(FUSO)


def data_hora(valor) -> str:
    return "-" if pd.isna(valor) else valor.strftime("%d/%m/%Y %H:%M")


for c in ["primeira_detecao", "ultima_detecao", "reconhecido_em", "resolvido_em"]:
    tabela[c] = local(tabela[c])
for c in ["inicio", "fim"]:
    execucoes[c] = local(execucoes[c])

# ---------------------------------------------------------------- última execução

if execucoes.empty:
    st.info("Ainda não existe nenhuma execução da verificação de alertas registada.")
else:
    u = execucoes.iloc[0]
    e = st.columns(4)
    e[0].metric("Última verificação", u["inicio"].strftime("%d/%m %H:%M"), help=data_hora(u["inicio"]))
    e[1].metric("Origem", {"agendador": "Agendador", "etl": "ETL", "manual": "Manual",
                           "teste": "Teste"}.get(u["origem"], u["origem"]))
    e[2].metric("Estado", {"sucesso": "Sucesso", "erro": "Erro", "sem_dados": "Sem dados",
                           "em_curso": "Em curso"}.get(u["estado"], u["estado"]))
    e[3].metric("Novos / resolvidos", f"{u['novos']} / {u['resolvidos']}",
                help=f"Atualizados: {u['atualizados']}. Em aberto no fim: {u['ativos']}.")
    if u["estado"] == "erro":
        st.error(f"A última verificação falhou e as alterações foram revertidas: {u['mensagem']}")
    agendadas = execucoes[execucoes["origem"] == "agendador"]
    st.caption("Última execução pelo agendador: "
               + (f"{data_hora(agendadas.iloc[0]['inicio'])} ({agendadas.iloc[0]['estado']})"
                  if not agendadas.empty else "nenhuma registada nas últimas 50 execuções."))

# ---------------------------------------------------------------- indicadores

abertos = tabela[tabela["estado"] != "resolvido"]
k = st.columns(5)
k[0].metric("Alertas ativos", int((tabela["estado"] == "ativo").sum()))
k[1].metric("Reconhecidos", int((tabela["estado"] == "reconhecido").sum()))
for i, sev in enumerate(ORDEM_SEVERIDADE, start=2):
    k[i].metric(f"{alertas.SEVERIDADES[sev]} (em aberto)", int((abertos["severidade"] == sev).sum()))

modo = "dark" if getattr(getattr(st.context, "theme", None), "type", "light") == "dark" else "light"


def estilo(fig, altura=260):
    fig.update_layout(height=altura, margin=dict(l=10, r=10, t=10, b=10), showlegend=False, bargap=0.35)
    fig.update_xaxes(showgrid=False, rangemode="tozero", tickformat="d", nticks=6)
    return fig


if not abertos.empty:
    g1, g2 = st.columns(2)
    with g1:
        st.subheader("Alertas em aberto por severidade")
        s = (abertos["severidade"].value_counts().reindex(ORDEM_SEVERIDADE, fill_value=0)
             .rename_axis("severidade").reset_index(name="alertas").iloc[::-1])
        s["rotulo"] = s["severidade"].map(alertas.SEVERIDADES)
        fig = px.bar(s, x="alertas", y="rotulo", orientation="h", text="alertas", color="severidade",
                     color_discrete_map=COR_SEVERIDADE, labels={"alertas": "Alertas", "rotulo": ""})
        fig.update_traces(marker_cornerradius=4, textposition="outside", cliponaxis=False,
                          hovertemplate="<b>%{y}</b><br>Alertas: %{x}<extra></extra>")
        fig.update_xaxes(range=[0, max(s["alertas"].max(), 1) * 1.25])
        st.plotly_chart(estilo(fig), width="stretch")
    with g2:
        st.subheader("Alertas em aberto por tipo")
        t = (abertos["tipo"].value_counts().reindex(list(alertas.TIPOS), fill_value=0)
             .rename_axis("tipo").reset_index(name="alertas").iloc[::-1])
        t["rotulo"] = t["tipo"].map(alertas.TIPOS)
        fig = px.bar(t, x="alertas", y="rotulo", orientation="h", text="alertas", labels={"alertas": "Alertas", "rotulo": ""})
        fig.update_traces(marker_color=COR_SERIE[modo], marker_cornerradius=4, textposition="outside",
                          cliponaxis=False, hovertemplate="<b>%{y}</b><br>Alertas: %{x}<extra></extra>")
        fig.update_xaxes(range=[0, max(t["alertas"].max(), 1) * 1.25])
        st.plotly_chart(estilo(fig), width="stretch")

# ---------------------------------------------------------------- lista, histórico e execuções

separador_lista, separador_historico, separador_execucoes = st.tabs(
    ["Alertas", "Histórico de resolvidos", "Execuções da verificação"])

with separador_lista:
    f1, f2, f3 = st.columns(3)
    f_tipo = f1.multiselect("Tipo", list(alertas.TIPOS), format_func=alertas.TIPOS.get, placeholder="Todos os tipos")
    f_sev = f2.multiselect("Severidade", ORDEM_SEVERIDADE, format_func=alertas.SEVERIDADES.get,
                           placeholder="Todas as severidades")
    f_estado = f3.multiselect("Estado", list(alertas.ESTADOS), default=["ativo", "reconhecido"],
                              format_func=alertas.ESTADOS.get, placeholder="Todos os estados")
    sel = tabela.copy()
    if f_tipo:
        sel = sel[sel["tipo"].isin(f_tipo)]
    if f_sev:
        sel = sel[sel["severidade"].isin(f_sev)]
    if f_estado:
        sel = sel[sel["estado"].isin(f_estado)]
    sel = sel.assign(_ordem=sel["severidade"].map(ORDEM_SEVERIDADE.index)).sort_values(
        ["_ordem", "ultima_detecao", "id"], ascending=[True, False, True])

    st.caption(f"{len(sel)} alertas correspondem aos filtros.")
    if sel.empty:
        st.info("Nenhum alerta corresponde aos filtros escolhidos.")
    for _, a in sel.head(100).iterrows():
        cabecalho = (f"{alertas.SEVERIDADES[a['severidade']]} - {alertas.TIPOS[a['tipo']]} - {a['titulo']}"
                     f"  ({alertas.ESTADOS[a['estado']]})")
        with st.expander(cabecalho):
            st.write(a["descricao"])
            d = st.columns(4)
            d[0].caption(f"**Estado**  \n{alertas.ESTADOS[a['estado']]}")
            d[1].caption(f"**Primeira deteção**  \n{data_hora(a['primeira_detecao'])}")
            d[2].caption(f"**Última deteção**  \n{data_hora(a['ultima_detecao'])}")
            d[3].caption(f"**Deteções**  \n{a['n_detecoes']}")
            if a["estado"] == "reconhecido":
                st.caption(f"Reconhecido em {data_hora(a['reconhecido_em'])}. "
                           "Continua em aberto até a regra deixar de se verificar.")
            if a["estado"] == "resolvido":
                st.caption(f"Resolvido automaticamente em {data_hora(a['resolvido_em'])}.")
            detalhes = a["detalhes"] if isinstance(a["detalhes"], dict) else json.loads(a["detalhes"])
            st.dataframe(pd.DataFrame(
                [(k, json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
                 for k, v in detalhes.items()], columns=["Indicador", "Valor"]).astype(str),
                hide_index=True, width="stretch")
            if a["estado"] == "ativo" and st.button("Reconhecer", key=f"reconhecer_{a['id']}",
                                                    help="Marca o alerta como visto. Não o resolve."):
                with obter_engine().begin() as conn:
                    alterado = alertas.reconhecer(conn, a["id"])
                carregar.clear()
                st.toast("Alerta reconhecido." if alterado else "O alerta já não estava ativo.")
                st.rerun()
    if len(sel) > 100:
        st.caption("A mostrar os primeiros 100 alertas. Use os filtros para restringir a lista.")

with separador_historico:
    resolvidos = tabela[tabela["estado"] == "resolvido"].sort_values("resolvido_em", ascending=False)
    if resolvidos.empty:
        st.info("Ainda não existem alertas resolvidos.")
    else:
        st.dataframe(resolvidos.assign(
            tipo=resolvidos["tipo"].map(alertas.TIPOS), severidade=resolvidos["severidade"].map(alertas.SEVERIDADES),
            primeira_detecao=resolvidos["primeira_detecao"].map(data_hora),
            resolvido_em=resolvidos["resolvido_em"].map(data_hora))[
            ["titulo", "tipo", "severidade", "primeira_detecao", "resolvido_em", "n_detecoes"]],
            hide_index=True, width="stretch",
            column_config={"titulo": "Alerta", "tipo": "Tipo", "severidade": "Severidade",
                           "primeira_detecao": "Primeira deteção", "resolvido_em": "Resolvido em",
                           "n_detecoes": "Deteções"})

with separador_execucoes:
    if not execucoes.empty:
        st.dataframe(execucoes.assign(inicio=execucoes["inicio"].map(data_hora), fim=execucoes["fim"].map(data_hora))[
            ["id", "inicio", "fim", "origem", "estado", "novos", "atualizados", "resolvidos", "ativos", "mensagem"]],
            hide_index=True, width="stretch",
            column_config={"id": "Execução", "inicio": "Início", "fim": "Fim", "origem": "Origem",
                           "estado": "Estado", "novos": "Novos", "atualizados": "Atualizados",
                           "resolvidos": "Resolvidos", "ativos": "Em aberto", "mensagem": "Mensagem"})
