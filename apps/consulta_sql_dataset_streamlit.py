from pathlib import Path
import sqlite3

import pandas as pd
import streamlit as st

BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "dataset" / "dataset.csv"
DB_PATH = BASE_DIR / "spotify_dataset.db"


@st.cache_data
def load_dataset(csv_path: Path = CSV_PATH) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df = df.loc[:, ~df.columns.astype(str).str.contains(r"^Unnamed")]
    df.columns = [str(column).strip() for column in df.columns]
    return df


@st.cache_resource
def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def build_sqlite_db(df: pd.DataFrame, db_path: Path = DB_PATH) -> None:
    with sqlite3.connect(db_path) as conn:
        df.to_sql("tracks", conn, if_exists="replace", index=False)


def execute_query(query: str) -> pd.DataFrame:
    cleaned = query.strip()
    if not cleaned:
        return pd.DataFrame()

    if not cleaned.lower().startswith("select"):
        raise ValueError("Apenas consultas SELECT são permitidas.")

    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(cleaned, conn)


st.set_page_config(page_title="Spotify SQL Explorer", layout="wide")
st.title("Spotify SQL Explorer")
st.caption("Consultas SQL sobre o arquivo dataset.csv")

try:
    dataset = load_dataset()
    build_sqlite_db(dataset)
except Exception as exc:
    st.error(f"Erro ao carregar o dataset: {exc}")
    st.stop()

st.sidebar.header("Consultas rápidas")
examples = {
    "Top 10 músicas por popularidade": "SELECT track_name, artists, popularity FROM tracks ORDER BY popularity DESC LIMIT 10;",
    "Gêneros mais frequentes": "SELECT track_genre, COUNT(*) AS total FROM tracks GROUP BY track_genre ORDER BY total DESC LIMIT 10;",
    "Músicas com maior danceability": "SELECT track_name, danceability, popularity FROM tracks ORDER BY danceability DESC LIMIT 10;",
    "Faixas com energia alta": "SELECT track_name, energy, popularity FROM tracks WHERE energy > 0.8 ORDER BY popularity DESC LIMIT 10;",
    "Músicas mais longas": "SELECT track_name, duration_ms FROM tracks ORDER BY duration_ms DESC LIMIT 10;",
}

selected_example = st.sidebar.selectbox("Escolha um exemplo", list(examples.keys()))
query = st.text_area("SQL", value=examples[selected_example], height=180)

if st.button("Executar consulta"):
    try:
        result = execute_query(query)
        st.subheader("Resultado")
        st.dataframe(result, use_container_width=True)
    except Exception as exc:
        st.error(f"Consulta inválida: {exc}")

st.subheader("Dados carregados")
st.write(f"Linhas: {len(dataset)} | Colunas: {len(dataset.columns)}")
st.dataframe(dataset.head(10), use_container_width=True)
