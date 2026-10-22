"""Ponto de entrada do ETL TechStore. Executar a partir da raiz do projeto: python dataintegration/etl.py [--bd]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from techstore.pipeline import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
