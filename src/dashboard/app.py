"""Dashboard TechStore (Streamlit + Plotly).

Executar a partir da raiz do projeto:  streamlit run src/dashboard/app.py

Páginas:
- Análise de Vendas (vendas.py) - indicadores, gráficos e tabelas das vendas;
- Centro de Alertas (alertas.py) - consulta dos alertas gerados no PostgreSQL pela função verificar_alertas().
"""
import streamlit as st

st.set_page_config(page_title="TechStore", layout="wide")

pagina = st.navigation([
    st.Page("vendas.py", title="Análise de Vendas", default=True),
    st.Page("alertas.py", title="Centro de Alertas"),
])
pagina.run()
