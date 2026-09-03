from pathlib import Path
import sqlite3
import io
import zipfile
import base64
import json
import hashlib

import numpy as np
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR.parent / "dataset" / "tracks_with_clusters.csv"
DB_PATH = BASE_DIR.parent / "spotify_dataset.db"
PREVIEW_CACHE_DIR = BASE_DIR.parent / ".cache" / "deezer_previews"


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


@st.cache_data(show_spinner=False)
def find_deezer_preview(track_name: str, artists: str) -> dict | None:
    query = f"{track_name} {artists.split(';')[0]}"
    response = requests.get(
        "https://api.deezer.com/search",
        params={"q": query, "limit": 1},
        timeout=10,
    )
    response.raise_for_status()
    results = response.json().get("data", [])
    if not results or not results[0].get("preview"):
        return None
    match = results[0]
    return {
        "preview_url": match["preview"],
        "title": match.get("title", track_name),
        "artist": match.get("artist", {}).get("name", artists),
    }


@st.cache_data(show_spinner=False)
def download_preview(preview_url: str) -> bytes:
    response = requests.get(preview_url, timeout=20)
    response.raise_for_status()
    return response.content


def cache_preview(preview_url: str, position: int) -> Path:
    PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    preview_id = hashlib.sha256(preview_url.encode("utf-8")).hexdigest()[:12]
    destination = PREVIEW_CACHE_DIR / f"preview_{position}_{preview_id}.mp3"
    if not destination.exists():
        destination.write_bytes(download_preview(preview_url))
    return destination


def preview_archive(previews: list[dict]) -> bytes:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for preview in previews:
            zip_file.writestr(preview["path"].name, preview["data"])
    return archive.getvalue()


def render_crossfade_player(previews: list[dict], crossfade_seconds: float) -> None:
    audio_sources = json.dumps([preview["data_url"] for preview in previews])
    audio_labels = json.dumps([
        f"{preview['title']} - {preview['artist']}" for preview in previews
    ])
    component = f"""
        <div style="font-family: sans-serif; max-width: 720px; padding: 12px; border: 1px solid #ddd; border-radius: 8px;">
            <strong id="track">Preview 1</strong>
            <input id="progress" type="range" min="0" max="100" value="0" style="width: 100%; margin: 12px 0;">
            <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
                <button id="previous">Anterior</button>
                <button id="play">Reproduzir</button>
                <button id="next">Próxima</button>
                <label>Crossfade: {crossfade_seconds:.1f}s</label>
            </div>
            <small id="status">Clique em Reproduzir para iniciar.</small>
    </div>
    <script>
            const sources = {audio_sources};
            const labels = {audio_labels};
      const fade = {crossfade_seconds};
            const audios = sources.map((source) => new Audio(source));
            let currentIndex = 0;
            let playing = false;
            let fadeTimer = null;
      const button = document.getElementById("play");
            const previous = document.getElementById("previous");
            const next = document.getElementById("next");
            const progress = document.getElementById("progress");
            const track = document.getElementById("track");
      const status = document.getElementById("status");
            function updateTrack() {{
                track.textContent = labels[currentIndex];
                progress.value = 0;
                status.textContent = `Faixa ${{currentIndex + 1}} de ${{audios.length}}`;
            }}
            function stopFade() {{
                if (fadeTimer) clearInterval(fadeTimer);
                fadeTimer = null;
            }}
            function setVolume(audio, volume) {{ audio.volume = Math.max(0, Math.min(1, volume)); }}
            function playCurrent() {{
                audios[currentIndex].play();
                playing = true;
                button.textContent = "Pausar";
                status.textContent = `Reproduzindo faixa ${{currentIndex + 1}} de ${{audios.length}}`;
            }}
            function pauseCurrent() {{
                audios[currentIndex].pause();
                playing = false;
                button.textContent = "Reproduzir";
                status.textContent = "Pausado";
            }}
            function switchTrack(direction) {{
                const targetIndex = (currentIndex + direction + audios.length) % audios.length;
                const oldAudio = audios[currentIndex];
                const newAudio = audios[targetIndex];
                stopFade();
                newAudio.currentTime = 0;
                if (!playing) {{
                    oldAudio.pause();
                    setVolume(oldAudio, 0);
                    currentIndex = targetIndex;
                    setVolume(newAudio, 1);
                    updateTrack();
                    return;
                }}
                setVolume(newAudio, 0);
                newAudio.play();
                const startedAt = Date.now();
                fadeTimer = setInterval(() => {{
                    const amount = Math.min(1, (Date.now() - startedAt) / (fade * 1000));
                    setVolume(oldAudio, 1 - amount);
                    setVolume(newAudio, amount);
                    if (amount >= 1) {{
                        stopFade();
                        oldAudio.pause();
                        oldAudio.currentTime = 0;
                    }}
                }}, 50);
                currentIndex = targetIndex;
                updateTrack();
            }}
            button.onclick = () => playing ? pauseCurrent() : playCurrent();
            previous.onclick = () => switchTrack(-1);
            next.onclick = () => switchTrack(1);
            progress.oninput = () => {{
                const audio = audios[currentIndex];
                if (audio.duration) audio.currentTime = (progress.value / 100) * audio.duration;
            }};
            audios.forEach((audio, index) => {{
                audio.addEventListener("timeupdate", () => {{
                    if (index === currentIndex && audio.duration) progress.value = (audio.currentTime / audio.duration) * 100;
                }});
                audio.addEventListener("ended", () => {{ if (index === currentIndex) switchTrack(1); }});
            }});
            setVolume(audios[0], 1);
            audios.slice(1).forEach((audio) => setVolume(audio, 0));
            updateTrack();
    </script>
    """
    components.html(component, height=100)


st.set_page_config(page_title="Spotify Music Explorer", layout="wide")
st.title("Playlist Studio")
st.caption("Monte playlists por ambiente, perfil sonoro e compatibilidade entre faixas")

try:
    dataset = load_dataset()
    build_sqlite_db(dataset)
except Exception as exc:
    st.error(f"Erro ao carregar o dataset: {exc}")
    st.stop()

feature_columns = ["danceability", "energy", "speechiness", "acousticness", "instrumentalness", "liveness", "valence"]
dataset_means = pd.Series({
    "danceability": 0.562,
    "energy": 0.636,
    "speechiness": 0.080,
    "acousticness": 0.323,
    "instrumentalness": 0.183,
    "liveness": 0.214,
    "valence": 0.466,
})

environment_presets = {
    "Restaurante": {"danceability": (0.30, 0.60), "energy": (0.20, 0.55), "speechiness": (0.00, 0.15), "acousticness": (0.35, 1.00), "instrumentalness": (0.10, 0.70), "liveness": (0.00, 0.35), "valence": (0.45, 0.85)},
    "Loja de alto padrão": {"danceability": (0.25, 0.60), "energy": (0.15, 0.50), "speechiness": (0.00, 0.12), "acousticness": (0.25, 0.85), "instrumentalness": (0.15, 0.80), "liveness": (0.00, 0.25), "valence": (0.45, 0.80)},
    "High fashion": {"danceability": (0.45, 0.85), "energy": (0.35, 0.80), "speechiness": (0.00, 0.20), "acousticness": (0.00, 0.45), "instrumentalness": (0.25, 1.00), "liveness": (0.00, 0.30), "valence": (0.35, 0.75)},
    "Relaxante": {"danceability": (0.00, 0.40), "energy": (0.00, 0.35), "speechiness": (0.00, 0.10), "acousticness": (0.55, 1.00), "instrumentalness": (0.35, 1.00), "liveness": (0.00, 0.25), "valence": (0.25, 0.75)},
    "Academia": {"danceability": (0.55, 1.00), "energy": (0.65, 1.00), "speechiness": (0.00, 0.35), "acousticness": (0.00, 0.45), "instrumentalness": (0.00, 0.60), "liveness": (0.00, 0.40), "valence": (0.45, 1.00)},
    "Loja de roupas": {"danceability": (0.45, 0.90), "energy": (0.35, 0.75), "speechiness": (0.00, 0.25), "acousticness": (0.05, 0.55), "instrumentalness": (0.05, 0.75), "liveness": (0.00, 0.35), "valence": (0.50, 0.95)},
    "Shopping": {"danceability": (0.45, 0.85), "energy": (0.35, 0.75), "speechiness": (0.00, 0.25), "acousticness": (0.10, 0.60), "instrumentalness": (0.05, 0.65), "liveness": (0.00, 0.35), "valence": (0.45, 0.90)},
    "Mercado": {"danceability": (0.35, 0.80), "energy": (0.30, 0.70), "speechiness": (0.00, 0.25), "acousticness": (0.15, 0.70), "instrumentalness": (0.00, 0.55), "liveness": (0.00, 0.40), "valence": (0.45, 0.90)},
}

environment_popularity_ranges = {
    "Restaurante": (35, 100),
    "Loja de alto padrão": (25, 100),
    "High fashion": (20, 100),
    "Relaxante": (0, 80),
    "Academia": (45, 100),
    "Loja de roupas": (45, 100),
    "Shopping": (50, 100),
    "Mercado": (50, 100),
}

environment_genre_presets = {
    "Restaurante": ["acoustic", "jazz", "blues", "piano", "romance", "soul", "r-n-b", "classical", "opera", "brazil", "mpb", "samba", "pagode", "latin", "salsa", "tango", "folk", "guitar", "reggae"],
    "Loja de alto padrão": ["ambient", "classical", "jazz", "blues", "piano", "acoustic", "deep-house", "electronic", "minimal-techno", "indie", "indie-pop", "synth-pop", "soul", "r-n-b", "trip-hop", "lounge", "chill"],
    "High fashion": ["electronic", "deep-house", "minimal-techno", "techno", "house", "progressive-house", "synth-pop", "indie", "indie-pop", "pop", "disco", "edm", "electro", "garage", "idm", "breakbeat", "trip-hop", "j-dance", "k-pop"],
    "Relaxante": ["ambient", "chill", "new-age", "piano", "classical", "acoustic", "romance", "sleep", "study", "folk", "guitar", "singer-songwriter", "jazz", "blues", "opera", "world-music", "dub", "reggae"],
    "Academia": ["dance", "edm", "house", "deep-house", "electro", "techno", "trance", "drum-and-bass", "hardstyle", "dubstep", "breakbeat", "rock", "hard-rock", "heavy-metal", "metalcore", "punk-rock", "hip-hop", "r-n-b", "funk", "groove", "dancehall", "reggaeton", "afrobeat"],
    "Loja de roupas": ["pop", "indie-pop", "power-pop", "alternative", "indie", "synth-pop", "dance", "disco", "house", "deep-house", "electronic", "electro", "funk", "groove", "r-n-b", "soul", "hip-hop", "k-pop", "j-pop", "latin", "reggaeton", "afrobeat"],
    "Shopping": ["pop", "indie-pop", "alternative", "dance", "disco", "house", "deep-house", "edm", "electronic", "latin", "latino", "reggaeton", "salsa", "funk", "groove", "r-n-b", "soul", "k-pop", "j-pop", "afrobeat", "dancehall", "reggae", "rock"],
    "Mercado": ["pop", "dance", "disco", "house", "latin", "latino", "reggaeton", "brazil", "forro", "samba", "pagode", "mpb", "sertanejo", "reggae", "dancehall", "country", "folk", "rock", "funk", "groove", "soul", "r-n-b", "k-pop"],
}

environment_cluster_presets = {
    "Restaurante": [0, 1, 5],
    "Loja de alto padrão": [0, 1, 5],
    "High fashion": [1, 4, 5],
    "Relaxante": [0, 6],
    "Academia": [1, 2, 3, 4, 5],
    "Loja de roupas": [1, 4, 5],
    "Shopping": [1, 4, 5],
    "Mercado": [1, 2, 4],
}

cluster_descriptions = {
    0: ("Acústico e introspectivo", "Baixa energia, alta acousticness e perfil suave."),
    1: ("Dançante e positivo", "Alta danceability, energia e valence; indicado para ambientes sociais."),
    2: ("Performance ao vivo", "Energia e liveness elevadas; lembra shows e apresentações."),
    3: ("Intenso e sombrio", "Energia muito alta e valence baixa; perfil pesado e dramático."),
    4: ("Dançante e vocalizado", "Danceability e speechiness elevadas; compatível com rap e funk."),
    5: ("Eletrônico instrumental", "Alta instrumentalness e energia; próximo de techno e house."),
    6: ("Ambiental e relaxante", "Baixa energia, alta instrumentalness e acousticness."),
}

def camelot_label(key, mode):
    major = {0: "8B", 1: "3B", 2: "10B", 3: "5B", 4: "12B", 5: "7B", 6: "2B", 7: "9B", 8: "4B", 9: "11B", 10: "6B", 11: "1B"}
    minor = {0: "5A", 1: "12A", 2: "7A", 3: "2A", 4: "9A", 5: "4A", 6: "11A", 7: "6A", 8: "1A", 9: "8A", 10: "3A", 11: "10A"}
    return (major if int(mode) == 1 else minor).get(int(key), "?")

def harmonic_distance(previous, candidate):
    pitch_distance = abs(int(previous["key"]) - int(candidate["key"])) % 12
    pitch_distance = min(pitch_distance, 12 - pitch_distance)
    mode_distance = 0 if int(previous["mode"]) == int(candidate["mode"]) else 1
    return pitch_distance + mode_distance * 0.75

def build_playlist(filtered, size, seed):
    rng = np.random.default_rng(seed if seed > 0 else None)
    remaining = filtered.copy()
    if remaining.empty:
        return remaining
    selected = []
    first_pool = remaining.nlargest(min(24, len(remaining)), "fit_score")
    selected.append(rng.choice(first_pool.index.to_numpy()))
    remaining = remaining.drop(index=selected[0])
    while len(selected) < min(size, len(filtered)) and not remaining.empty:
        previous = filtered.loc[selected[-1]]
        pool = remaining.nlargest(min(40, len(remaining)), "fit_score").copy()
        pool["harmonic_cost"] = pool.apply(
            lambda row, previous=previous: harmonic_distance(previous, row), axis=1
        )
        pool["tempo_cost"] = (pool["tempo"] - previous["tempo"]).abs() / 40
        pool["selection_score"] = pool["fit_score"] - pool["harmonic_cost"] * 0.06 - pool["tempo_cost"] * 0.04
        best_pool = pool.nlargest(min(10, len(pool)), "selection_score")
        next_index = rng.choice(best_pool.index.to_numpy())
        selected.append(next_index)
        remaining = remaining.drop(index=next_index)
    result = filtered.loc[selected].copy()
    result["key_camelot"] = [camelot_label(key, mode) for key, mode in zip(result["key"], result["mode"])]
    result["tempo_difference"] = [np.nan, *[(result.iloc[i]["tempo"] - result.iloc[i - 1]["tempo"]) for i in range(1, len(result))]]
    return result

st.sidebar.header("Configuração da playlist")
selected_environment = st.sidebar.selectbox("Ambiente", list(environment_presets))
preset_ranges = environment_presets[selected_environment]
feature_ranges = {}
st.sidebar.caption("Ranges musicais")
for feature in feature_columns:
    feature_ranges[feature] = st.sidebar.slider(
        feature.capitalize(),
        0.0,
        1.0,
        preset_ranges[feature],
        0.01,
        format="%.2f",
        key=f"{selected_environment}_{feature}",
    )

popularity_range = st.sidebar.slider(
    "Popularidade",
    min_value=0,
    max_value=100,
    value=environment_popularity_ranges[selected_environment],
    step=1,
    key=f"{selected_environment}_popularity",
    help="Ambientes acessíveis e casuais começam priorizando músicas populares.",
)

available_genres = sorted(dataset["track_genre"].dropna().unique())
genre_preset = [genre for genre in environment_genre_presets[selected_environment] if genre in available_genres]
selected_genres = st.sidebar.multiselect(
    "Gêneros (vazio = todos)",
    available_genres,
    default=genre_preset,
    key=f"{selected_environment}_genres",
)
st.sidebar.caption(
    f"Preset amplo: {len(genre_preset)} gêneros. Limpe a seleção para liberar todos."
)

cluster_options = sorted(dataset["cluster"].dropna().unique().tolist())
available_clusters = set(cluster_options)
recommended_clusters = [
    cluster_id
    for cluster_id in environment_cluster_presets[selected_environment]
    if cluster_id in available_clusters
]
selected_clusters = st.sidebar.multiselect(
    "Perfis de músicas / clusters (vazio = todos)",
    cluster_options,
    default=recommended_clusters,
    key=f"{selected_environment}_clusters",
)
st.sidebar.caption(
    "Clusters sugeridos: "
    + ", ".join(str(cluster_id) for cluster_id in recommended_clusters)
)
avoid_explicit = st.sidebar.checkbox("Evitar conteúdo explícito", value=True)
playlist_size = st.sidebar.slider("Faixas na playlist", 5, 100, 20)
random_seed = st.sidebar.number_input("Semente (0 = aleatória)", min_value=0, max_value=999999, value=0, step=1)
use_all = st.sidebar.checkbox("Selecionar todas as músicas possíveis", value=False)

with st.expander("Médias dos atributos no dataset"):
    st.dataframe(
        dataset_means.rename("média").to_frame().round(3),
        use_container_width=True,
    )

filtered_dataset = dataset.copy()
for column in feature_columns + ["key", "mode", "tempo", "popularity"]:
    filtered_dataset[column] = pd.to_numeric(filtered_dataset[column], errors="coerce")
filtered_dataset = filtered_dataset.loc[
    filtered_dataset["popularity"].between(*popularity_range)
]
if selected_clusters:
    filtered_dataset = filtered_dataset.loc[filtered_dataset["cluster"].isin(selected_clusters)]
if selected_genres:
    filtered_dataset = filtered_dataset.loc[filtered_dataset["track_genre"].isin(selected_genres)]
if avoid_explicit:
    filtered_dataset = filtered_dataset.loc[~filtered_dataset["explicit"].astype(bool)]
filtered_dataset = filtered_dataset.dropna(subset=feature_columns + ["key", "mode", "tempo", "popularity"])
profile_target = {
    feature: sum(bounds) / 2
    for feature, bounds in feature_ranges.items()
}
environment_target = pd.Series({
    feature: 0.65 * profile_target[feature] + 0.35 * dataset_means[feature]
    for feature in feature_columns
})
distance_to_target = filtered_dataset[feature_columns].sub(environment_target, axis="columns").abs().mean(axis=1)
range_width = pd.Series({
    feature: max(maximum - minimum, 0.20)
    for feature, (minimum, maximum) in feature_ranges.items()
})
outside_preference = pd.DataFrame({
    feature: (filtered_dataset[feature] - maximum).clip(lower=0)
    + (minimum - filtered_dataset[feature]).clip(lower=0)
    for feature, (minimum, maximum) in feature_ranges.items()
}).div(range_width).mean(axis=1)
filtered_dataset["fit_score"] = 1 - (distance_to_target * 0.75 + outside_preference * 0.25)

st.subheader("Perfil dos clusters")
cluster_profile = pd.DataFrame([{"cluster": cluster_id, "perfil": name, "descrição": description, "recomendado": cluster_id in recommended_clusters} for cluster_id, (name, description) in cluster_descriptions.items()])
st.dataframe(cluster_profile, hide_index=True, use_container_width=True)

st.subheader("Playlist gerada")
st.caption(
    f"{len(filtered_dataset):,} músicas elegíveis após gênero, cluster, popularidade e conteúdo explícito. "
    "Os ranges musicais funcionam como preferência de ranking, sem eliminar faixas."
)
if st.button("Gerar playlist", type="primary"):
    selection_size = len(filtered_dataset) if use_all else playlist_size
    playlist = build_playlist(filtered_dataset, selection_size, int(random_seed))
    if playlist.empty:
        st.warning("Nenhuma música corresponde aos filtros selecionados.")
    else:
        st.success(f"{len(playlist)} faixas selecionadas a partir de {len(filtered_dataset)} candidatas.")
        display_columns = ["track_name", "artists", "track_genre", "cluster", "popularity", "key_camelot", "tempo", "tempo_difference", *feature_columns]
        st.dataframe(playlist[[column for column in display_columns if column in playlist.columns]], use_container_width=True, height=560)
        st.download_button("Baixar playlist CSV", playlist.to_csv(index=False).encode("utf-8"), f"playlist_{selected_environment.lower().replace(' ', '_')}.csv", "text/csv")

        st.subheader("Previews Deezer")
        st.caption("Os previews têm aproximadamente 30 segundos e são fornecidos pela API do Deezer.")
        st.info("A fila tenta carregar a playlist inteira no cache. Faixas sem preview são puladas automaticamente.")
        preview_tracks = playlist
        previews = []
        for position, (_, track) in enumerate(preview_tracks.iterrows(), start=1):
            try:
                match = find_deezer_preview(track["track_name"], track["artists"])
                if match is None:
                    st.warning(f"Preview não encontrado: {track['track_name']}")
                    continue
                preview_path = cache_preview(match["preview_url"], position)
                preview_data = preview_path.read_bytes()
                data_url = "data:audio/mpeg;base64," + base64.b64encode(preview_data).decode("ascii")
                previews.append({
                    "position": position,
                    "title": match["title"],
                    "artist": match["artist"],
                    "path": preview_path,
                    "data": preview_data,
                    "data_url": data_url,
                })
            except requests.RequestException:
                st.warning(f"Não foi possível consultar o Deezer: {track['track_name']}")
            except OSError:
                st.warning(f"Não foi possível armazenar o preview: {track['track_name']}")

        if previews:
            st.download_button(
                "Baixar previews (ZIP)",
                preview_archive(previews),
                "deezer_previews.zip",
                "application/zip",
            )
            if len(previews) >= 2:
                st.markdown("**Crossfade experimental**")
                st.caption("O crossfade usa Web Audio API e começa somente após seu clique, por restrições do navegador.")
                crossfade_seconds = st.slider("Duração do crossfade (segundos)", 1.0, 8.0, 3.0, 0.5)
                render_crossfade_player(previews, crossfade_seconds)

st.divider()
st.subheader("Consulta SQL avançada")
examples = {
    "Top 10 músicas por popularidade": "SELECT track_name, artists, popularity FROM tracks ORDER BY popularity DESC LIMIT 10;",
    "Gêneros mais frequentes": "SELECT track_genre, COUNT(*) AS total FROM tracks GROUP BY track_genre ORDER BY total DESC LIMIT 10;",
    "Músicas com maior danceability": "SELECT track_name, danceability, popularity FROM tracks ORDER BY danceability DESC LIMIT 10;",
    "Faixas com energia alta": "SELECT track_name, energy, popularity FROM tracks WHERE energy > 0.8 ORDER BY popularity DESC LIMIT 10;",
    "Músicas mais longas": "SELECT track_name, duration_ms FROM tracks ORDER BY duration_ms DESC LIMIT 10;",
}

selected_example = st.selectbox("Escolha um exemplo", list(examples.keys()))
query = st.text_area("SQL", value=examples[selected_example], height=180)

if st.button("Executar consulta"):
    try:
        result = execute_query(query)
        st.subheader("Resultado")
        st.dataframe(result, use_container_width=True)
    except Exception as exc:
        st.error(f"Consulta inválida: {exc}")

st.caption(f"Dataset carregado: {len(dataset):,} faixas | {len(dataset.columns)} colunas")
