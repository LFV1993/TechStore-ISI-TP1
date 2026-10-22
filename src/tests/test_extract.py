"""Leitura das três fontes reais, comparada com uma leitura independente dos ficheiros."""
import json
from pathlib import Path

from techstore import extract

DADOS = Path(__file__).resolve().parents[2] / "data" / "input"


def test_le_todas_as_linhas_do_csv():
    linhas = (DADOS / "vendas.csv").read_text(encoding="utf-8").splitlines()
    df = extract.ler_vendas(DADOS / "vendas.csv")
    assert len(df) == len(linhas) - 1  # sem o cabeçalho
    assert list(df.columns) == extract.COLUNAS_VENDAS
    # os valores ficam exatamente como no ficheiro (texto, sem conversões)
    assert ";".join(df.iloc[0]) == linhas[1]


def test_le_produtos_do_json():
    original = json.loads((DADOS / "produtos.json").read_text(encoding="utf-8"))
    df = extract.ler_produtos(DADOS / "produtos.json")
    assert len(df) == len(original)
    assert df.to_dict("records") == original


def test_le_categorias_do_xml():
    texto = (DADOS / "categorias.xml").read_text(encoding="utf-8")
    df = extract.ler_categorias(DADOS / "categorias.xml")
    assert len(df) == texto.count("<categoria>")
    assert list(df.columns) == extract.COLUNAS_CATEGORIAS
    for _, linha in df.iterrows():
        assert f"<codigo_categoria>{linha.codigo_categoria}</codigo_categoria>" in texto
        assert f"<nome_categoria>{linha.nome_categoria}</nome_categoria>" in texto


def test_csv_campos_vazios_ficam_texto_vazio(tmp_path):
    f = tmp_path / "v.csv"
    f.write_text("id_venda;data_venda;codigo_produto;quantidade;preco_unitario\nV1;;PRD-0001;2;1.00\n",
                 encoding="utf-8")
    df = extract.ler_vendas(f)
    assert df.loc[0, "data_venda"] == ""
