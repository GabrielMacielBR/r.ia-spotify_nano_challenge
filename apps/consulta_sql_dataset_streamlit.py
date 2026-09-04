from pathlib import Path
import sqlite3
import io
import zipfile
import base64
import json
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor

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


def clean_search_term(text: str) -> str:
    if not text or not isinstance(text, str):
        return ""
    cleaned = text.replace('"', ' ').replace("'", ' ')
    cleaned = re.sub(r"\(.*?(remaster|live|feat|version|bonus|deluxe|edit).*?\)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\[.*?(remaster|live|feat|version|bonus|deluxe|edit).*?\]", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"-\s*(remaster|live|radio edit|mono|stereo|anniversary).*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else text.strip()


@st.cache_data(show_spinner=False, ttl=86400)
def find_deezer_preview(track_name: str, artists: str) -> dict | None:
    first_artist = str(artists).split(";")[0].strip()
    clean_track = clean_search_term(str(track_name))
    clean_art = clean_search_term(first_artist)

    queries = [
        f'artist:"{clean_art}" track:"{clean_track}"',
        f"{clean_track} {clean_art}",
        f"{track_name} {first_artist}",
    ]

    for query in queries:
        try:
            response = requests.get(
                "https://api.deezer.com/search",
                params={"q": query, "limit": 3},
                timeout=6,
            )
            if response.status_code == 200:
                results = response.json().get("data", [])
                for match in results:
                    if match.get("preview"):
                        album = match.get("album", {})
                        artist = match.get("artist", {})
                        return {
                            "preview_url": match["preview"],
                            "title": match.get("title", track_name),
                            "artist": artist.get("name", first_artist),
                            "cover": album.get("cover_medium") or album.get("cover_small") or "",
                            "album": album.get("title", ""),
                            "duration": match.get("duration", 30),
                            "deezer_id": match.get("id"),
                            "link": match.get("link", ""),
                        }
        except Exception:
            continue
    return None


def fetch_all_previews(playlist_df: pd.DataFrame, max_tracks: int = 40) -> list[dict]:
    tracks_to_fetch = playlist_df.head(max_tracks)
    previews = []

    def fetch_single(item):
        idx, track = item
        match = find_deezer_preview(str(track.get("track_name", "")), str(track.get("artists", "")))
        if match:
            return {
                "position": idx,
                "title": match["title"],
                "artist": match["artist"],
                "preview_url": match["preview_url"],
                "cover": match.get("cover", ""),
                "album": match.get("album", ""),
                "duration": match.get("duration", 30),
                "key_camelot": str(track.get("key_camelot", "")),
                "tempo": float(track.get("tempo", 0.0)) if pd.notna(track.get("tempo")) else 0.0,
                "track_genre": str(track.get("track_genre", "")),
                "estilo_musical": str(track.get("estilo_musical", "")),
                "match": float(track.get("match", 0.0)) if pd.notna(track.get("match")) else 0.0,
                "rank_score": float(track.get("rank_display", 0.0)) if pd.notna(track.get("rank_display")) else 0.0,
            }
        return None

    items = list(enumerate(tracks_to_fetch.to_dict(orient="records"), start=1))
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(fetch_single, items))

    for res in results:
        if res is not None:
            previews.append(res)

    return previews


@st.cache_data(show_spinner=False)
def download_preview_bytes(preview_url: str) -> bytes | None:
    try:
        response = requests.get(preview_url, timeout=15)
        if response.status_code == 200:
            return response.content
    except Exception:
        pass
    return None


def create_previews_zip(previews: list[dict]) -> bytes:
    PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for idx, preview in enumerate(previews, start=1):
            url = preview.get("preview_url")
            if not url:
                continue
            preview_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
            clean_title = "".join(c for c in f"{idx:02d}_{preview.get('artist')}_{preview.get('title')}" if c.isalnum() or c in (" ", "_", "-")).strip()[:50]
            filename = f"{clean_title}_{preview_id}.mp3"
            dest = PREVIEW_CACHE_DIR / filename
            data = None
            if dest.exists():
                data = dest.read_bytes()
            else:
                data = download_preview_bytes(url)
                if data:
                    dest.write_bytes(data)
            if data:
                zip_file.writestr(filename, data)
    return archive.getvalue()


def render_crossfade_player(previews: list[dict], default_crossfade: float = 3.0) -> None:
    if not previews:
        st.info("Nenhum preview de áudio disponível para reprodução.")
        return

    previews_json = json.dumps(previews)
    component_template = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    user-select: none;
}
body {
    background-color: transparent;
    color: #ffffff;
    padding: 4px;
    overflow: hidden;
}
.player-card {
    background: linear-gradient(160deg, #1f1f1f 0%, #121212 100%);
    border: 1px solid #2e2e2e;
    border-radius: 12px;
    padding: 16px 18px;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
    max-width: 860px;
    margin: 0 auto;
}
.now-playing {
    display: flex;
    align-items: center;
    gap: 14px;
    margin-bottom: 12px;
}
.cover-box {
    width: 62px;
    height: 62px;
    border-radius: 8px;
    overflow: hidden;
    background: #252525;
    flex-shrink: 0;
    box-shadow: 0 4px 10px rgba(0,0,0,0.4);
    display: flex;
    align-items: center;
    justify-content: center;
}
.cover-img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: none;
}
.cover-fallback {
    font-size: 26px;
    display: flex;
    align-items: center;
    justify-content: center;
}
.track-info {
    flex: 1;
    min-width: 0;
}
.track-top-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 2px;
}
.track-pos {
    font-size: 11px;
    color: #888888;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.xfade-badge {
    display: none;
    background: rgba(29, 185, 84, 0.15);
    color: #1db954;
    border: 1px solid #1db954;
    font-size: 10px;
    font-weight: 700;
    padding: 2px 7px;
    border-radius: 8px;
    letter-spacing: 0.5px;
    animation: pulse 1.2s infinite;
}
@keyframes pulse {
    0% { opacity: 0.6; transform: scale(0.98); }
    50% { opacity: 1; transform: scale(1.02); }
    100% { opacity: 0.6; transform: scale(0.98); }
}
.track-title {
    font-size: 16px;
    font-weight: 700;
    color: #ffffff;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    line-height: 1.3;
}
.track-artist {
    font-size: 13px;
    color: #b3b3b3;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-top: 1px;
}
.track-tags {
    display: flex;
    gap: 6px;
    margin-top: 4px;
    flex-wrap: wrap;
}
.tag {
    font-size: 10px;
    background: #252525;
    color: #1db954;
    padding: 1px 7px;
    border-radius: 6px;
    border: 1px solid #333;
}
.progress-container {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 10px;
}
.time-num {
    font-size: 11px;
    color: #a0a0a0;
    min-width: 32px;
    font-variant-numeric: tabular-nums;
    text-align: center;
}
.progress-slider {
    flex: 1;
    -webkit-appearance: none;
    appearance: none;
    height: 4px;
    border-radius: 2px;
    background: #383838;
    outline: none;
    cursor: pointer;
    transition: background 0.15s;
}
.progress-slider:hover {
    background: #4a4a4a;
}
.progress-slider::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: #1db954;
    cursor: pointer;
    box-shadow: 0 0 5px rgba(0,0,0,0.6);
}
.controls-panel {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 10px;
    padding-bottom: 12px;
    border-bottom: 1px solid #282828;
    margin-bottom: 10px;
}
.playback-group {
    display: flex;
    align-items: center;
    gap: 12px;
}
.btn-round {
    background: none;
    border: none;
    color: #b3b3b3;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 50%;
    transition: all 0.15s ease;
    padding: 6px;
}
.btn-round:hover {
    color: #ffffff;
    transform: scale(1.1);
}
.btn-main-play {
    width: 42px;
    height: 42px;
    background: #1db954;
    color: #000000;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 4px 12px rgba(29,185,84,0.3);
}
.btn-main-play:hover {
    background: #1ed760;
    color: #000;
    transform: scale(1.06);
}
.settings-group {
    display: flex;
    align-items: center;
    gap: 14px;
    flex-wrap: wrap;
}
.xfade-wrapper {
    display: flex;
    align-items: center;
    gap: 8px;
    background: #1c1c1c;
    padding: 3px 10px;
    border-radius: 16px;
    border: 1px solid #333;
}
.xfade-toggle-pill {
    background: #2a2a2a;
    border: 1px solid #444;
    color: #aaa;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 7px;
    border-radius: 10px;
    cursor: pointer;
    transition: all 0.2s;
}
.xfade-toggle-pill.active {
    background: #1db954;
    color: #000;
    border-color: #1db954;
}
.xfade-slider-box {
    display: flex;
    align-items: center;
    gap: 6px;
}
.xfade-slider {
    -webkit-appearance: none;
    appearance: none;
    width: 60px;
    height: 4px;
    border-radius: 2px;
    background: #444;
    outline: none;
    cursor: pointer;
}
.xfade-slider::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 11px;
    height: 11px;
    border-radius: 50%;
    background: #1db954;
    cursor: pointer;
}
.xfade-val-label {
    font-size: 11px;
    color: #1db954;
    font-weight: 700;
    min-width: 26px;
}
.volume-wrapper {
    display: flex;
    align-items: center;
    gap: 6px;
}
.vol-slider {
    -webkit-appearance: none;
    appearance: none;
    width: 70px;
    height: 4px;
    border-radius: 2px;
    background: #444;
    outline: none;
    cursor: pointer;
}
.vol-slider::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: #fff;
    cursor: pointer;
}
.queue-box {
    background: #151515;
    border: 1px solid #242424;
    border-radius: 8px;
    overflow: hidden;
}
.queue-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 12px;
    background: #1a1a1a;
    border-bottom: 1px solid #262626;
    font-size: 11px;
    color: #888;
}
.queue-scroll {
    max-height: 175px;
    overflow-y: auto;
}
.queue-scroll::-webkit-scrollbar {
    width: 6px;
}
.queue-scroll::-webkit-scrollbar-thumb {
    background: #333;
    border-radius: 3px;
}
.queue-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 12px;
    cursor: pointer;
    border-bottom: 1px solid #1a1a1a;
    transition: background 0.15s;
}
.queue-row:last-child {
    border-bottom: none;
}
.queue-row:hover {
    background: #232323;
}
.queue-row.active {
    background: #162b1e;
    border-left: 3px solid #1db954;
}
.queue-num {
    font-size: 11px;
    color: #666;
    min-width: 18px;
    text-align: right;
}
.queue-row.active .queue-num {
    color: #1db954;
    font-weight: 700;
}
.queue-thumb {
    width: 28px;
    height: 28px;
    border-radius: 4px;
    object-fit: cover;
    background: #282828;
    flex-shrink: 0;
}
.queue-details {
    flex: 1;
    min-width: 0;
}
.queue-row-title {
    font-size: 12px;
    font-weight: 600;
    color: #eee;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.queue-row.active .queue-row-title {
    color: #1db954;
}
.queue-row-artist {
    font-size: 11px;
    color: #888;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.queue-row-dur {
    font-size: 11px;
    color: #666;
    font-variant-numeric: tabular-nums;
}
@media (max-width: 600px) {
    body {
        padding: 0;
    }
    .player-card {
        padding: 12px 10px;
    }
    .track-title {
        font-size: 14px;
    }
    .track-artist {
        font-size: 12px;
    }
    .controls-panel {
        flex-direction: column;
        align-items: stretch;
        gap: 10px;
    }
    .playback-group {
        justify-content: center;
    }
    .settings-group {
        justify-content: space-between;
    }
}
</style>
</head>
<body>
<div class="player-card">
    <div class="now-playing">
        <div class="cover-box">
            <img id="cover-img" class="cover-img" alt="Capa" />
            <div id="cover-fallback" class="cover-fallback">🎵</div>
        </div>
        <div class="track-info">
            <div class="track-top-row">
                <span id="track-pos" class="track-pos">Faixa 1 de 1</span>
                <span id="xfade-badge" class="xfade-badge">CROSSFADE</span>
            </div>
            <div id="track-title" class="track-title">Carregando playlist...</div>
            <div id="track-artist" class="track-artist">Deezer Previews</div>
            <div class="track-tags">
                <span id="tag-style" class="tag" style="display:none;"></span>
                <span id="tag-tempo" class="tag" style="display:none;"></span>
                <span id="tag-genre" class="tag" style="display:none;"></span>
                <span id="tag-match" class="tag" style="display:none; background: rgba(29, 185, 84, 0.2); border-color: #1db954;"></span>
                <span id="tag-key" class="tag" style="display:none;"></span>
            </div>
        </div>
    </div>

    <div class="progress-container">
        <span id="time-current" class="time-num">0:00</span>
        <input id="progress-bar" class="progress-slider" type="range" min="0" max="100" value="0" step="0.1" />
        <span id="time-total" class="time-num">0:30</span>
    </div>

    <div class="controls-panel">
        <div class="playback-group">
            <button id="btn-prev" class="btn-round" title="Faixa Anterior">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><polygon points="19 20 9 12 19 4 19 20"></polygon><line x1="5" y1="4" x2="5" y2="20" stroke="currentColor" stroke-width="2.5"></line></svg>
            </button>
            <button id="btn-play" class="btn-main-play" title="Reproduzir / Pausar">
                <svg id="icon-play" width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><polygon points="6 4 20 12 6 20 6 4"></polygon></svg>
                <svg id="icon-pause" width="22" height="22" viewBox="0 0 24 24" fill="currentColor" style="display:none;"><rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect></svg>
            </button>
            <button id="btn-next" class="btn-round" title="Próxima Faixa">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 4 15 12 5 20 5 4"></polygon><line x1="19" y1="4" x2="19" y2="20" stroke="currentColor" stroke-width="2.5"></line></svg>
            </button>
        </div>

        <div class="settings-group">
            <div class="xfade-wrapper">
                <button id="btn-xfade-toggle" class="xfade-toggle-pill active" title="Alternar Crossfade">Crossfade: ON</button>
                <div class="xfade-slider-box">
                    <input id="slider-xfade" class="xfade-slider" type="range" min="0" max="6" step="0.5" value="__DEFAULT_CROSSFADE__" />
                    <span id="xfade-val-text" class="xfade-val-label">__DEFAULT_CROSSFADE__s</span>
                </div>
            </div>

            <div class="volume-wrapper">
                <button id="btn-mute" class="btn-round" title="Mudo">
                    <svg id="icon-vol" width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M15.54 8.46a5 5 0 0 1 0 7.07" stroke="currentColor" stroke-width="2" fill="none"></path><path d="M19.07 4.93a10 10 0 0 1 0 14.14" stroke="currentColor" stroke-width="2" fill="none"></path></svg>
                    <svg id="icon-mute" width="18" height="18" viewBox="0 0 24 24" fill="currentColor" style="display:none;"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><line x1="23" y1="9" x2="17" y2="15" stroke="currentColor" stroke-width="2"></line><line x1="17" y1="9" x2="23" y2="15" stroke="currentColor" stroke-width="2"></line></svg>
                </button>
                <input id="slider-vol" class="vol-slider" type="range" min="0" max="100" value="85" />
            </div>
        </div>
    </div>

    <div class="queue-box">
        <div class="queue-header">
            <span>Fila da Playlist</span>
            <span id="queue-status">Clique em qualquer faixa para transição suave</span>
        </div>
        <div id="queue-scroll" class="queue-scroll"></div>
    </div>
</div>

<script>
let playlist = __PREVIEWS_JSON__;
let currentIndex = 0;
let isPlaying = false;
let isCrossfading = false;
let crossfadeDuration = parseFloat("__DEFAULT_CROSSFADE__") || 3.0;
let crossfadeEnabled = crossfadeDuration > 0;
let masterVolume = 0.85;
let isMuted = false;
let crossfadeTimer = null;

const deckA = new Audio();
const deckB = new Audio();
deckA.preload = "auto";
deckB.preload = "auto";

let activeDeck = deckA;
let inactiveDeck = deckB;

const coverImg = document.getElementById("cover-img");
const coverFallback = document.getElementById("cover-fallback");
const trackPos = document.getElementById("track-pos");
const xfadeBadge = document.getElementById("xfade-badge");
const trackTitle = document.getElementById("track-title");
const trackArtist = document.getElementById("track-artist");
const tagStyle = document.getElementById("tag-style");
const tagTempo = document.getElementById("tag-tempo");
const tagGenre = document.getElementById("tag-genre");
const tagMatch = document.getElementById("tag-match");
const tagKey = document.getElementById("tag-key");

const timeCurrent = document.getElementById("time-current");
const timeTotal = document.getElementById("time-total");
const progressBar = document.getElementById("progress-bar");

const btnPlay = document.getElementById("btn-play");
const iconPlay = document.getElementById("icon-play");
const iconPause = document.getElementById("icon-pause");
const btnPrev = document.getElementById("btn-prev");
const btnNext = document.getElementById("btn-next");

const btnXfadeToggle = document.getElementById("btn-xfade-toggle");
const sliderXfade = document.getElementById("slider-xfade");
const xfadeValText = document.getElementById("xfade-val-text");

const btnMute = document.getElementById("btn-mute");
const iconVol = document.getElementById("icon-vol");
const iconMute = document.getElementById("icon-mute");
const sliderVol = document.getElementById("slider-vol");

const queueScroll = document.getElementById("queue-scroll");
const queueStatus = document.getElementById("queue-status");

function formatTime(sec) {
    if (isNaN(sec) || sec < 0) return "0:00";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return m + ":" + (s < 10 ? "0" : "") + s;
}

function updatePlayButton() {
    if (isPlaying) {
        iconPlay.style.display = "none";
        iconPause.style.display = "block";
    } else {
        iconPlay.style.display = "block";
        iconPause.style.display = "none";
    }
}

function stopCrossfade() {
    if (crossfadeTimer) {
        clearInterval(crossfadeTimer);
        crossfadeTimer = null;
    }
    isCrossfading = false;
    xfadeBadge.style.display = "none";
}

function updateVolume() {
    const baseVol = isMuted ? 0 : masterVolume;
    if (!isCrossfading) {
        activeDeck.volume = baseVol;
        inactiveDeck.volume = 0;
    }
    if (isMuted) {
        iconVol.style.display = "none";
        iconMute.style.display = "block";
    } else {
        iconVol.style.display = "block";
        iconMute.style.display = "none";
    }
}

function loadDeck(deck, index) {
    if (index >= 0 && index < playlist.length) {
        deck.src = playlist[index].preview_url;
        deck.currentTime = 0;
        deck.volume = isMuted ? 0 : masterVolume;
    }
}

function updateUI() {
    if (!playlist || playlist.length === 0) return;
    const current = playlist[currentIndex];
    trackPos.textContent = "Faixa " + (currentIndex + 1) + " de " + playlist.length;
    trackTitle.textContent = current.title || "Sem título";
    trackArtist.textContent = current.artist || "Artista desconhecido";

    if (current.cover) {
        coverImg.src = current.cover;
        coverImg.style.display = "block";
        coverFallback.style.display = "none";
    } else {
        coverImg.style.display = "none";
        coverFallback.style.display = "flex";
    }

    if (current.estilo_musical) {
        tagStyle.textContent = "Estilo: " + current.estilo_musical;
        tagStyle.style.display = "inline-block";
    } else {
        tagStyle.style.display = "none";
    }

    if (current.tempo && current.tempo > 0) {
        tagTempo.textContent = Math.round(current.tempo) + " BPM";
        tagTempo.style.display = "inline-block";
    } else {
        tagTempo.style.display = "none";
    }

    if (current.track_genre) {
        tagGenre.textContent = current.track_genre;
        tagGenre.style.display = "inline-block";
    } else {
        tagGenre.style.display = "none";
    }

    if (current.match && current.match > 0) {
        tagMatch.textContent = "Match: " + Math.round(current.match) + "%";
        tagMatch.style.display = "inline-block";
    } else {
        tagMatch.style.display = "none";
    }

    if (current.key_camelot && current.key_camelot !== "?") {
        tagKey.textContent = "Tom: " + current.key_camelot;
        tagKey.style.display = "inline-block";
    } else {
        tagKey.style.display = "none";
    }

    updatePlayButton();

    const rows = queueScroll.querySelectorAll(".queue-row");
    rows.forEach((row, idx) => {
        if (idx === currentIndex) {
            row.classList.add("active");
            row.scrollIntoView({ behavior: "smooth", block: "nearest" });
        } else {
            row.classList.remove("active");
        }
    });
}

function startCrossfade(targetIndex, customDuration) {
    if (playlist.length <= 1 || targetIndex === currentIndex) return;
    stopCrossfade();
    isCrossfading = true;
    xfadeBadge.style.display = "inline-block";

    const fadeSeconds = (customDuration !== undefined && customDuration !== null) ? customDuration : crossfadeDuration;
    const fadeMs = Math.max(250, fadeSeconds * 1000);
    const fadeStart = Date.now();
    const baseVol = isMuted ? 0 : masterVolume;

    inactiveDeck.src = playlist[targetIndex].preview_url;
    inactiveDeck.currentTime = 0;
    inactiveDeck.volume = 0;

    const playPromise = inactiveDeck.play();
    if (playPromise !== undefined) {
        playPromise.catch(e => console.warn("Erro no play do incoming deck:", e));
    }

    crossfadeTimer = setInterval(() => {
        const elapsed = Date.now() - fadeStart;
        const t = Math.min(1.0, Math.max(0.0, elapsed / fadeMs));

        // Curva Equal-Power (seno/cosseno)
        const gainOut = Math.cos(t * Math.PI * 0.5);
        const gainIn = Math.sin(t * Math.PI * 0.5);

        activeDeck.volume = Math.max(0, Math.min(1, gainOut * baseVol));
        inactiveDeck.volume = Math.max(0, Math.min(1, gainIn * baseVol));

        if (t >= 1.0) {
            finalizeCrossfade(targetIndex);
        }
    }, 35);
}

function finalizeCrossfade(newIndex) {
    stopCrossfade();
    activeDeck.pause();
    activeDeck.currentTime = 0;
    activeDeck.volume = 0;

    const temp = activeDeck;
    activeDeck = inactiveDeck;
    inactiveDeck = temp;

    activeDeck.volume = isMuted ? 0 : masterVolume;
    currentIndex = newIndex;
    updateUI();
}

function goToTrack(targetIndex, allowCrossfade = true) {
    if (targetIndex < 0 || targetIndex >= playlist.length) return;
    if (targetIndex === currentIndex && isPlaying) return;

    if (isPlaying && allowCrossfade && crossfadeEnabled && crossfadeDuration > 0) {
        const quickFade = Math.min(crossfadeDuration, 1.2);
        startCrossfade(targetIndex, quickFade);
    } else {
        stopCrossfade();
        activeDeck.pause();
        inactiveDeck.pause();
        inactiveDeck.currentTime = 0;
        currentIndex = targetIndex;
        loadDeck(activeDeck, currentIndex);
        if (isPlaying) {
            activeDeck.play().catch(e => console.warn("Erro ao iniciar áudio:", e));
        }
        updateUI();
    }
}

function togglePlay() {
    if (isPlaying) {
        activeDeck.pause();
        inactiveDeck.pause();
        isPlaying = false;
        stopCrossfade();
        activeDeck.volume = isMuted ? 0 : masterVolume;
        updatePlayButton();
    } else {
        if (!activeDeck.src || activeDeck.src !== playlist[currentIndex].preview_url) {
            loadDeck(activeDeck, currentIndex);
        }
        activeDeck.play().then(() => {
            isPlaying = true;
            updatePlayButton();
        }).catch(e => console.warn("Autoplay bloqueado ou erro:", e));
    }
}

function attachAudioEvents(audio) {
    audio.addEventListener("timeupdate", () => {
        if (audio !== activeDeck) return;
        if (audio.duration && !isNaN(audio.duration)) {
            const pct = (audio.currentTime / audio.duration) * 100;
            progressBar.value = pct;
            timeCurrent.textContent = formatTime(audio.currentTime);
            timeTotal.textContent = formatTime(audio.duration);

            if (isPlaying && crossfadeEnabled && crossfadeDuration > 0 && !isCrossfading) {
                const remaining = audio.duration - audio.currentTime;
                if (remaining <= crossfadeDuration && audio.duration > crossfadeDuration) {
                    const nextIndex = (currentIndex + 1) % playlist.length;
                    startCrossfade(nextIndex, crossfadeDuration);
                }
            }
        }
    });

    audio.addEventListener("ended", () => {
        if (audio === activeDeck) {
            if (isCrossfading) {
                const nextIndex = (currentIndex + 1) % playlist.length;
                finalizeCrossfade(nextIndex);
            } else {
                const nextIndex = (currentIndex + 1) % playlist.length;
                if (isPlaying) {
                    goToTrack(nextIndex, false);
                }
            }
        }
    });

    audio.addEventListener("error", (e) => {
        console.warn("Erro no elemento de áudio:", e);
        if (audio === activeDeck && isPlaying) {
            queueStatus.textContent = "Erro no preview. Pulando faixa...";
            setTimeout(() => {
                const nextIndex = (currentIndex + 1) % playlist.length;
                goToTrack(nextIndex, false);
            }, 600);
        }
    });
}

attachAudioEvents(deckA);
attachAudioEvents(deckB);

btnPlay.onclick = togglePlay;
btnPrev.onclick = () => {
    const prevIndex = (currentIndex - 1 + playlist.length) % playlist.length;
    goToTrack(prevIndex, true);
};
btnNext.onclick = () => {
    const nextIndex = (currentIndex + 1) % playlist.length;
    goToTrack(nextIndex, true);
};

progressBar.oninput = () => {
    if (activeDeck.duration) {
        if (isCrossfading) {
            stopCrossfade();
            inactiveDeck.pause();
            inactiveDeck.currentTime = 0;
            activeDeck.volume = isMuted ? 0 : masterVolume;
        }
        activeDeck.currentTime = (progressBar.value / 100) * activeDeck.duration;
        timeCurrent.textContent = formatTime(activeDeck.currentTime);
    }
};

sliderXfade.oninput = () => {
    crossfadeDuration = parseFloat(sliderXfade.value);
    xfadeValText.textContent = crossfadeDuration.toFixed(1) + "s";
    if (crossfadeDuration === 0) {
        crossfadeEnabled = false;
        btnXfadeToggle.classList.remove("active");
        btnXfadeToggle.textContent = "Crossfade: OFF";
    } else {
        crossfadeEnabled = true;
        btnXfadeToggle.classList.add("active");
        btnXfadeToggle.textContent = "Crossfade: ON";
    }
};

btnXfadeToggle.onclick = () => {
    crossfadeEnabled = !crossfadeEnabled;
    if (crossfadeEnabled) {
        if (crossfadeDuration === 0) crossfadeDuration = 3.0;
        sliderXfade.value = crossfadeDuration;
        xfadeValText.textContent = crossfadeDuration.toFixed(1) + "s";
        btnXfadeToggle.classList.add("active");
        btnXfadeToggle.textContent = "Crossfade: ON";
    } else {
        btnXfadeToggle.classList.remove("active");
        btnXfadeToggle.textContent = "Crossfade: OFF";
    }
};

sliderVol.oninput = () => {
    masterVolume = sliderVol.value / 100;
    isMuted = false;
    updateVolume();
};

btnMute.onclick = () => {
    isMuted = !isMuted;
    updateVolume();
};

function renderQueueList() {
    queueScroll.innerHTML = "";
    playlist.forEach((track, idx) => {
        const row = document.createElement("div");
        row.className = "queue-row" + (idx === currentIndex ? " active" : "");
        row.onclick = () => goToTrack(idx, true);

        const num = document.createElement("span");
        num.className = "queue-num";
        num.textContent = (idx + 1);

        const thumb = document.createElement("img");
        thumb.className = "queue-thumb";
        thumb.src = track.cover || "";
        thumb.onerror = () => { thumb.style.display = "none"; };
        if (!track.cover) thumb.style.display = "none";

        const details = document.createElement("div");
        details.className = "queue-details";

        const title = document.createElement("div");
        title.className = "queue-row-title";
        title.textContent = track.title || ("Faixa " + (idx + 1));

        const artist = document.createElement("div");
        artist.className = "queue-row-artist";
        artist.textContent = track.artist || "";

        details.appendChild(title);
        details.appendChild(artist);

        const dur = document.createElement("span");
        dur.className = "queue-row-dur";
        dur.textContent = "0:30";

        row.appendChild(num);
        if (track.cover) row.appendChild(thumb);
        row.appendChild(details);
        row.appendChild(dur);

        queueScroll.appendChild(row);
    });
}

function handleNewTrack(newTrack) {
    if (!newTrack || !newTrack.preview_url) return;
    const exists = playlist.some(t => t.preview_url === newTrack.preview_url || (t.title === newTrack.title && t.artist === newTrack.artist));
    if (!exists) {
        playlist.push(newTrack);
        renderQueueList();
        trackPos.textContent = "Faixa " + (currentIndex + 1) + " de " + playlist.length;
        queueStatus.textContent = playlist.length + " faixas disponíveis";
    }
}

if (window.BroadcastChannel) {
    try {
        const streamBc = new BroadcastChannel("retail_sound_stream");
        streamBc.onmessage = (e) => {
            if (e.data && e.data.type === "ADD_TRACK") {
                handleNewTrack(e.data.track);
            }
        };
    } catch(err) {
        console.warn("BroadcastChannel error:", err);
    }
}

window.addEventListener("message", (e) => {
    if (e.data && e.data.type === "ADD_TRACK") {
        handleNewTrack(e.data.track);
    }
});

renderQueueList();
if (playlist.length > 0) {
    loadDeck(activeDeck, 0);
    updateUI();
}
</script>
</body>
</html>
    """
    component = component_template.replace("__PREVIEWS_JSON__", previews_json).replace("__DEFAULT_CROSSFADE__", f"{default_crossfade:.1f}")
    components.html(component, height=540)



st.set_page_config(page_title="Retail Sound", page_icon="🎵", layout="centered", initial_sidebar_state="collapsed")

# Initialize session state early to prevent KeyError
if "playlist" not in st.session_state:
    st.session_state["playlist"] = None
if "previews" not in st.session_state:
    st.session_state["previews"] = []
if "playlist_env" not in st.session_state:
    st.session_state["playlist_env"] = ""

st.markdown(
    """
    <style>
    /* Remove sidebar and its toggle button entirely */
    [data-testid="stSidebar"], [data-testid="stSidebarCollapseButton"], button[data-testid="baseButton-headerNoPadding"] {
        display: none !important;
    }
    /* Hide the deploy button if present to prevent header overlap */
    .stAppDeployButton, header[data-testid="stHeader"] .stAppDeployButton {
        display: none !important;
    }
    /* Header background transparent */
    header[data-testid="stHeader"] {
        background: transparent !important;
    }
    /* Centralized, mobile-friendly container with ample top padding for toolbar */
    .block-container {
        max-width: 780px !important;
        padding-top: 5.2rem !important;
        padding-bottom: 2.5rem !important;
        padding-left: 1rem !important;
        padding-right: 1rem !important;
        margin: 0 auto !important;
    }
    /* Header typography */
    .studio-header {
        text-align: center;
        margin-top: 0.8rem;
        margin-bottom: 1.8rem;
    }
    .studio-title {
        font-size: 2.2rem;
        font-weight: 800;
        letter-spacing: -0.5px;
        margin-bottom: 0.3rem;
        color: #ffffff;
    }
    .studio-title span {
        color: #1db954;
    }
    .studio-subtitle {
        font-size: 1.02rem;
        color: #b3b3b3;
        line-height: 1.4;
    }
    /* Touch-friendly buttons */
    .stButton > button {
        border-radius: 24px !important;
        font-weight: 700 !important;
        padding: 0.65rem 1.4rem !important;
        font-size: 1.05rem !important;
        transition: all 0.2s ease !important;
    }
    .stDownloadButton > button {
        border-radius: 20px !important;
        font-weight: 600 !important;
        padding: 0.5rem 1.2rem !important;
    }
    /* Clean expander styling */
    div[data-testid="stExpander"] {
        border-radius: 12px !important;
        border: 1px solid #282828 !important;
        background: #181818 !important;
        margin-bottom: 1rem !important;
    }
    </style>
    <div class="studio-header">
        <div class="studio-title">Retail <span>Sound</span></div>
        <div class="studio-subtitle">Curadoria musical inteligente e transições perfeitas para o seu espaço comercial</div>
    </div>
    """,
    unsafe_allow_html=True,
)

try:
    dataset = load_dataset()
    build_sqlite_db(dataset)
except Exception as exc:
    st.error(f"Erro ao carregar o dataset: {exc}")
    st.stop()

feature_columns = ["energy", "acousticness", "valence", "instrumentalness"]
feature_labels = {
    "energy": ("Energia", "Intensidade e pegada sonora (Calmo ↔ Intenso)"),
    "acousticness": ("Acústico", "Instrumentos orgânicos vs. produção eletrônica (Sintético ↔ Orgânico)"),
    "valence": ("Humor & Vibe", "Positividade emocional da música (Sério/Melancólico ↔ Solar/Alegre)"),
    "instrumentalness": ("Instrumental", "Presença de vocais vs. batidas/arranjos instrumentais (Com Vocais ↔ Instrumental)"),
}
CENTROIDS_PATH = BASE_DIR.parent / "dataset" / "cluster_centroids.csv"


@st.cache_data
def get_dataset_feature_stats(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    m = df[feature_columns].apply(pd.to_numeric, errors="coerce").mean()
    s = df[feature_columns].apply(pd.to_numeric, errors="coerce").std(ddof=0).replace(0, 1.0)
    return m, s


@st.cache_data
def load_cluster_centroids() -> pd.DataFrame:
    if CENTROIDS_PATH.exists():
        c_df = pd.read_csv(CENTROIDS_PATH)
        return c_df.sort_values("cluster").reset_index(drop=True)
    return dataset.groupby("cluster")[feature_columns].mean().reset_index()


dataset_means, dataset_stds = get_dataset_feature_stats(dataset)
centroids_df = load_cluster_centroids()
centroids_z = (centroids_df[feature_columns].values - dataset_means.values) / dataset_stds.values

# Ambientes e Perfis Acústicos Alvo (incorporando os ambientes do sonora.ipynb: Consultório, Loja, Supermercado)
environment_targets = {
    "Consultório": {"energy": 0.12, "acousticness": 0.85, "valence": 0.40, "instrumentalness": 0.90, "tempo": 80, "milliman_dir": "slow"},
    "Supermercado": {"energy": 0.35, "acousticness": 0.40, "valence": 0.60, "instrumentalness": 0.00, "tempo": 85, "milliman_dir": "slow"},
    "Loja": {"energy": 0.85, "acousticness": 0.10, "valence": 0.80, "instrumentalness": 0.00, "tempo": 122, "milliman_dir": "fast"},
    "Restaurante": {"energy": 0.35, "acousticness": 0.65, "valence": 0.50, "instrumentalness": 0.20, "tempo": 95, "milliman_dir": "slow"},
    "Loja de alto padrão": {"energy": 0.35, "acousticness": 0.60, "valence": 0.45, "instrumentalness": 0.55, "tempo": 100, "milliman_dir": "slow"},
    "High fashion": {"energy": 0.65, "acousticness": 0.15, "valence": 0.45, "instrumentalness": 0.60, "tempo": 120, "milliman_dir": "fast"},
    "Relaxante": {"energy": 0.15, "acousticness": 0.85, "valence": 0.35, "instrumentalness": 0.65, "tempo": 75, "milliman_dir": "slow"},
    "Academia": {"energy": 0.88, "acousticness": 0.08, "valence": 0.65, "instrumentalness": 0.20, "tempo": 130, "milliman_dir": "fast"},
    "Loja de roupas": {"energy": 0.65, "acousticness": 0.25, "valence": 0.70, "instrumentalness": 0.15, "tempo": 118, "milliman_dir": "fast"},
    "Shopping": {"energy": 0.60, "acousticness": 0.35, "valence": 0.65, "instrumentalness": 0.15, "tempo": 110, "milliman_dir": "moderate"},
    "Mercado": {"energy": 0.55, "acousticness": 0.35, "valence": 0.70, "instrumentalness": 0.15, "tempo": 100, "milliman_dir": "slow"},
}

environment_presets = {
    "Consultório": {"energy": (0.00, 0.30), "acousticness": (0.60, 1.00), "valence": (0.20, 0.65), "instrumentalness": (0.50, 1.00)},
    "Supermercado": {"energy": (0.20, 0.55), "acousticness": (0.20, 0.70), "valence": (0.35, 0.85), "instrumentalness": (0.00, 0.30)},
    "Loja": {"energy": (0.65, 1.00), "acousticness": (0.00, 0.30), "valence": (0.50, 0.95), "instrumentalness": (0.00, 0.30)},
    "Restaurante": {"energy": (0.20, 0.55), "acousticness": (0.35, 1.00), "valence": (0.35, 0.80), "instrumentalness": (0.00, 0.60)},
    "Loja de alto padrão": {"energy": (0.20, 0.60), "acousticness": (0.25, 0.90), "valence": (0.35, 0.75), "instrumentalness": (0.10, 0.85)},
    "High fashion": {"energy": (0.45, 0.85), "acousticness": (0.00, 0.35), "valence": (0.25, 0.70), "instrumentalness": (0.20, 1.00)},
    "Relaxante": {"energy": (0.00, 0.35), "acousticness": (0.60, 1.00), "valence": (0.15, 0.65), "instrumentalness": (0.30, 1.00)},
    "Academia": {"energy": (0.70, 1.00), "acousticness": (0.00, 0.30), "valence": (0.30, 0.95), "instrumentalness": (0.00, 0.80)},
    "Loja de roupas": {"energy": (0.45, 0.85), "acousticness": (0.05, 0.50), "valence": (0.45, 0.95), "instrumentalness": (0.00, 0.50)},
    "Shopping": {"energy": (0.40, 0.80), "acousticness": (0.10, 0.65), "valence": (0.40, 0.90), "instrumentalness": (0.00, 0.50)},
    "Mercado": {"energy": (0.35, 0.85), "acousticness": (0.10, 0.70), "valence": (0.45, 0.95), "instrumentalness": (0.00, 0.45)},
}

environment_popularity_ranges = {
    "Consultório": (0, 80),
    "Supermercado": (35, 100),
    "Loja": (45, 100),
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
    "Consultório": ["ambient", "classical", "piano", "chill", "acoustic", "jazz", "blues", "guitar", "new-age"],
    "Supermercado": ["pop", "mpb", "soul", "groove", "disco", "r-n-b", "samba", "pagode", "brazil", "acoustic"],
    "Loja": ["pop", "dance", "disco", "indie-pop", "synth-pop", "house", "funk", "groove", "r-n-b", "reggaeton"],
    "Restaurante": ["jazz", "acoustic", "mpb", "soul", "blues", "piano", "brazil", "latin", "chill"],
    "Loja de alto padrão": ["jazz", "acoustic", "soul", "piano", "chill", "trip-hop", "mpb", "blues", "deep-house"],
    "High fashion": ["deep-house", "electronic", "minimal-techno", "synth-pop", "trip-hop", "house", "electro", "indie-pop"],
    "Relaxante": ["ambient", "chill", "piano", "classical", "acoustic", "guitar", "new-age", "world-music"],
    "Academia": ["dance", "edm", "house", "electro", "hip-hop", "reggaeton", "rock", "techno", "pop"],
    "Loja de roupas": ["pop", "indie-pop", "synth-pop", "dance", "disco", "deep-house", "house", "groove", "r-n-b", "reggaeton"],
    "Shopping": ["pop", "indie-pop", "dance", "disco", "groove", "r-n-b", "soul", "mpb", "acoustic"],
    "Mercado": ["mpb", "samba", "pagode", "brazil", "sertanejo", "pop", "forro", "groove", "acoustic"],
}

profile_descriptions = {
    0: ("Social & Ensolarado", "Músicas alegres, pop e dançantes, com vocais marcantes e clima vibrante para confraternizações e lojas dinâmicas."),
    1: ("Ambiental & Relaxante", "Sons calmos, orgânicos e puramente instrumentais para relaxamento, foco mental, spa e bem-estar."),
    2: ("Acústico & Intimista", "Arranjos acústicos e orgânicos com vocais suaves e elegantes; perfeito para jantares, conversas e sofisticação."),
    3: ("Eletrônico & Moderno", "Batidas eletrônicas contínuas e contemporâneas sem vocais; atmosfera urbana, lounge e moda."),
    4: ("Intenso & Dinâmico", "Ritmo acelerado, guitarras ou batidas pesadas com alta intensidade física; ideal para treinos e academias."),
}
profile_names = {cid: info[0] for cid, info in profile_descriptions.items()}


def camelot_label(key, mode):
    major = {0: "8B", 1: "3B", 2: "10B", 3: "5B", 4: "12B", 5: "7B", 6: "2B", 7: "9B", 8: "4B", 9: "11B", 10: "6B", 11: "1B"}
    minor = {0: "5A", 1: "12A", 2: "7A", 3: "2A", 4: "9A", 5: "4A", 6: "11A", 7: "6A", 8: "1A", 9: "8A", 10: "3A", 11: "10A"}
    return (major if int(mode) == 1 else minor).get(int(key), "?")


def harmonic_distance(previous, candidate):
    pitch_distance = abs(int(previous["key"]) - int(candidate["key"])) % 12
    pitch_distance = min(pitch_distance, 12 - pitch_distance)
    mode_distance = 0 if int(previous["mode"]) == int(candidate["mode"]) else 1
    return pitch_distance + mode_distance * 0.75


def build_playlist(filtered: pd.DataFrame, size: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed if seed > 0 else None)
    remaining = filtered.copy()
    if remaining.empty:
        return remaining

    selected = []
    # Primeira faixa sorteada do topo do ranking Sonora
    first_pool = remaining.nlargest(min(24, len(remaining)), "rank_score")
    selected.append(rng.choice(first_pool.index.to_numpy()))
    remaining = remaining.drop(index=selected[0])

    while len(selected) < min(size, len(filtered)) and not remaining.empty:
        previous = filtered.loc[selected[-1]]
        pool = remaining.nlargest(min(40, len(remaining)), "rank_score").copy()
        pool["harmonic_cost"] = pool.apply(
            lambda row, previous=previous: harmonic_distance(previous, row), axis=1
        )
        pool["tempo_cost"] = (pool["tempo"] - previous["tempo"]).abs() / 40.0
        # Combina o score do motor de 2 estágios com continuidade harmônica e de andamento
        pool["selection_score"] = pool["rank_score"] - pool["harmonic_cost"] * 0.05 - pool["tempo_cost"] * 0.04
        best_pool = pool.nlargest(min(10, len(pool)), "selection_score")
        next_index = rng.choice(best_pool.index.to_numpy())
        selected.append(next_index)
        remaining = remaining.drop(index=next_index)

    result = filtered.loc[selected].copy()
    result["key_camelot"] = [camelot_label(key, mode) for key, mode in zip(result["key"], result["mode"])]
    result["tempo_difference"] = [np.nan, *[(result.iloc[i]["tempo"] - result.iloc[i - 1]["tempo"]) for i in range(1, len(result))]]
    result["estilo_musical"] = result["cluster"].map(profile_names)
    result["rank_display"] = (result["rank_score"] * 100).round(1)
    return result


st.markdown("### 1. Ambiente do seu Espaço")
selected_environment = st.selectbox(
    "Selecione o Ambiente",
    list(environment_targets),
    label_visibility="collapsed",
    help="Escolha o tipo de ambiente ou proposta do seu espaço.",
)
preset_ranges = environment_presets[selected_environment]
milliman_dir = environment_targets[selected_environment]["milliman_dir"]

# Mapeamento sonoro inteligente para o ambiente
target_z_env = (np.array([environment_targets[selected_environment][f] for f in feature_columns]) - dataset_means.values) / dataset_stds.values
cluster_distances_env = np.linalg.norm(centroids_z - target_z_env, axis=1)
best_cluster_id = int(np.argmin(cluster_distances_env))
sorted_cluster_ids = np.argsort(cluster_distances_env)
recommended_styles = [profile_names[c] for c in sorted_cluster_ids[:2]]

st.markdown("### 2. Atmosferas Sonoras")
st.success(
    f"✨ **Atmosfera recomendada:** **{profile_names[best_cluster_id]}** — sonoridade ideal para a proposta de **{selected_environment}**."
)

available_styles = [info[0] for info in profile_descriptions.values()]
style_to_cluster = {info[0]: cid for cid, info in profile_descriptions.items()}

selected_styles = st.multiselect(
    "Estilos Musicais Selecionados",
    available_styles,
    default=recommended_styles,
    key=f"{selected_environment}_styles",
    label_visibility="collapsed",
    help="Escolha as atmosferas sonoras para o seu espaço.",
)
st.caption("✨ **Sugestão para este espaço:** " + ", ".join(recommended_styles))
selected_clusters = [style_to_cluster[s] for s in selected_styles]

st.markdown("### 3. Ritmo e Andamento")
milli_info = {
    "slow": ("🐢 Ritmo Acolhedor (Calmo / Relaxante)", "Músicas em andamento suave convidam o cliente a permanecer mais tempo e circular com tranquilidade pelo espaço."),
    "fast": ("⚡ Ritmo Estimulante (Dinâmico / Acelerado)", "Batidas com mais energia estimulam o dinamismo, vitalidade e circulação contínua."),
    "moderate": ("⚖️ Ritmo Equilibrado (Moderado)", "Andamento equilibrado para um fluxo agradável e constante ao longo do dia."),
}
milli_badge, milli_help = milli_info[milliman_dir]

col_milli1, col_milli2 = st.columns([3, 2])
with col_milli1:
    use_milliman = st.checkbox(
        "Harmonizar andamento com a proposta do ambiente",
        value=True,
        help="Prioriza músicas com andamento (BPM) favorável ao comportamento desejado no espaço.",
    )
with col_milli2:
    st.markdown(f"**Proposta:** {milli_badge}", help=milli_help)
st.caption(milli_help)

with st.expander("ℹ️ Conhecer as Atmosferas Musicais", expanded=False):
    style_profile_df = pd.DataFrame([
        {
            "Atmosfera Musical": name,
            "Sensação & Proposta Sonora": description,
            "Recomendação": "✨ Sugerido para o espaço" if name in recommended_styles else "Opcional",
        }
        for cid, (name, description) in profile_descriptions.items()
    ])
    st.dataframe(style_profile_df, hide_index=True, use_container_width=True)

with st.expander("⚙️ Ajustes de Som e Filtros Avançados (Opcional)", expanded=False):
    st.markdown("#### Dimensões Sonoras")
    feature_ranges = {}
    for feature in feature_columns:
        label, help_text = feature_labels[feature]
        feature_ranges[feature] = st.slider(
            label,
            0.0,
            1.0,
            preset_ranges[feature],
            0.01,
            format="%.2f",
            key=f"{selected_environment}_{feature}",
            help=help_text,
        )

    st.markdown("#### Popularidade e Gêneros")
    popularity_range = st.slider(
        "Faixa de Popularidade das Músicas",
        min_value=0,
        max_value=100,
        value=environment_popularity_ranges[selected_environment],
        step=1,
        key=f"{selected_environment}_popularity",
        help="Ambientes comerciais equilibram familiaridade com nicho sonoro.",
    )

    available_genres = sorted(dataset["track_genre"].dropna().unique())
    genre_preset = [genre for genre in environment_genre_presets[selected_environment] if genre in available_genres]
    selected_genres = st.multiselect(
        "Gêneros Musicais Específicos (vazio = todos)",
        available_genres,
        default=genre_preset,
        key=f"{selected_environment}_genres",
    )
    st.caption(f"Preset de gêneros: {len(genre_preset)} sugeridos para este ambiente.")

    col_chk1, col_chk2 = st.columns(2)
    with col_chk1:
        avoid_explicit = st.checkbox("Evitar conteúdo explícito", value=True)
    with col_chk2:
        use_all = st.checkbox("Selecionar todas as músicas elegíveis", value=False)

    random_seed = st.number_input(
        "Semente aleatória (0 = variada a cada clique)",
        min_value=0,
        max_value=999999,
        value=0,
        step=1,
    )

    with st.expander("Médias de referência do acervo musical", expanded=False):
        ref_df = pd.DataFrame([
            {
                "Atributo": feature_labels[f][0],
                "Descrição": feature_labels[f][1],
                "Média no Acervo": f"{dataset_means[f]:.2f}",
            }
            for f in feature_columns
        ])
        st.dataframe(ref_df, hide_index=True, use_container_width=True)

if "feature_ranges" not in locals():
    feature_ranges = {feature: preset_ranges[feature] for feature in feature_columns}
if "popularity_range" not in locals():
    popularity_range = environment_popularity_ranges[selected_environment]
if "selected_genres" not in locals():
    available_genres = sorted(dataset["track_genre"].dropna().unique())
    selected_genres = [g for g in environment_genre_presets[selected_environment] if g in available_genres]
if "avoid_explicit" not in locals():
    avoid_explicit = True
if "use_all" not in locals():
    use_all = False
if "random_seed" not in locals():
    random_seed = 0
if "use_milliman" not in locals():
    use_milliman = True

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

# ESTÁGIO 1 (Sonora): Ponto-alvo e Aderência Acústica (Exponential Match Score)
profile_target = {
    feature: (feature_ranges[feature][0] + feature_ranges[feature][1]) / 2.0
    for feature in feature_columns
}
tz_active = np.array([(profile_target[f] - dataset_means[f]) / dataset_stds[f] for f in feature_columns])

if not filtered_dataset.empty:
    Z_tracks = (filtered_dataset[feature_columns].values - dataset_means.values) / dataset_stds.values
    dt = np.linalg.norm(Z_tracks - tz_active, axis=1)
    # Aderência calibrada com decaimento exponencial tau=2.2 (Sonora)
    filtered_dataset["match"] = np.round(np.exp(-dt / 2.2) * 100, 1)

    # ESTÁGIO 2 (Sonora): Regra comportamental de Milliman (1982) por andamento
    slow_func = lambda t: np.clip((110.0 - t) / 60.0, 0.0, 1.0)
    fast_func = lambda t: np.clip((t - 100.0) / 60.0, 0.0, 1.0)
    mod_func = lambda t: np.clip(1.0 - np.abs(t - 105.0) / 40.0, 0.0, 1.0)

    if milliman_dir == "slow":
        beh = slow_func(filtered_dataset["tempo"].values)
    elif milliman_dir == "fast":
        beh = fast_func(filtered_dataset["tempo"].values)
    else:
        beh = mod_func(filtered_dataset["tempo"].values)

    if use_milliman:
        filtered_dataset["milliman_score"] = np.round(beh * 100, 1)
        filtered_dataset["rank_score"] = (
            0.5 * (filtered_dataset["match"] / 100.0) +
            0.3 * (filtered_dataset["popularity"] / 100.0) +
            0.2 * beh
        )
    else:
        filtered_dataset["milliman_score"] = 0.0
        filtered_dataset["rank_score"] = (
            0.6 * (filtered_dataset["match"] / 100.0) +
            0.4 * (filtered_dataset["popularity"] / 100.0)
        )
else:
    filtered_dataset["match"] = []
    filtered_dataset["milliman_score"] = []
    filtered_dataset["rank_score"] = []

st.markdown("---")
playlist_size = st.slider("Tamanho da Playlist (músicas)", 5, 50, 20)

col_gen, col_clear = st.columns([3, 1])
with col_gen:
    generate_clicked = st.button("🎵 Gerar Playlist", type="primary", use_container_width=True)
with col_clear:
    if st.session_state.get("playlist") is not None:
        if st.button("Limpar", use_container_width=True):
            st.session_state["playlist"] = None
            st.session_state["previews"] = []
            st.session_state["playlist_env"] = ""
            st.rerun()

st.caption(
    f"🔍 **{len(filtered_dataset):,}** faixas elegíveis para o ambiente **{selected_environment}**."
)

if generate_clicked:
    selection_size = len(filtered_dataset) if use_all else playlist_size
    playlist = build_playlist(filtered_dataset, selection_size, int(random_seed))
    if playlist.empty:
        st.warning("Nenhuma música corresponde aos filtros selecionados.")
        st.session_state["playlist"] = None
        st.session_state["previews"] = []
    else:
        st.session_state["playlist"] = playlist
        st.session_state["playlist_env"] = selected_environment
        with st.spinner("Buscando previews na API Deezer..."):
            previews = fetch_all_previews(playlist, max_tracks=min(selection_size, 40))
            st.session_state["previews"] = previews
        st.rerun()

if st.session_state.get("playlist") is not None:
    playlist = st.session_state["playlist"]
    previews = st.session_state.get("previews", [])

    if "estilo_musical" not in playlist.columns and "cluster" in playlist.columns:
        playlist["estilo_musical"] = playlist["cluster"].map(profile_names)

    st.markdown("---")
    st.success(f"✨ Playlist para **{st.session_state.get('playlist_env', selected_environment)}** gerada com sucesso ({len(playlist)} faixas)!")

    if previews:
        st.markdown("### 🎧 Player com Efeito Crossfade")
        st.caption("Transições suaves de áudio entre as faixas geradas:")
        render_crossfade_player(previews, default_crossfade=3.0)
    else:
        st.warning("Nenhum preview de áudio foi encontrado no Deezer para as faixas selecionadas.")

    col_csv, col_zip = st.columns(2)
    with col_csv:
        csv_name = f"playlist_{st.session_state.get('playlist_env', selected_environment).lower().replace(' ', '_')}.csv"
        st.download_button("📥 Baixar Playlist (CSV)", playlist.to_csv(index=False).encode("utf-8"), csv_name, "text/csv", use_container_width=True)
    with col_zip:
        if previews:
            zip_bytes = create_previews_zip(previews)
            st.download_button("📦 Baixar Músicas (ZIP)", zip_bytes, "deezer_previews.zip", "application/zip", use_container_width=True)

    with st.expander("📋 Ver Lista Completa de Faixas", expanded=True):
        display_columns = [
            "track_name",
            "artists",
            "track_genre",
            "estilo_musical",
            "match",
            "popularity",
            "tempo",
            "key_camelot",
            *feature_columns,
        ]
        display_rename = {
            "track_name": "Música",
            "artists": "Artista",
            "track_genre": "Gênero",
            "estilo_musical": "Atmosfera",
            "match": "Aderência (% Match)",
            "popularity": "Popularidade",
            "tempo": "BPM (Andamento)",
            "key_camelot": "Tom Harmônico",
            "energy": "Energia",
            "acousticness": "Acústico",
            "valence": "Humor (Valence)",
            "instrumentalness": "Instrumental",
        }
        friendly_df = playlist[[column for column in display_columns if column in playlist.columns]].rename(columns=display_rename)
        st.dataframe(friendly_df, use_container_width=True, height=360)

st.markdown("---")
with st.expander("🔍 Consulta SQL ao Acervo (Modo Desenvolvedor)", expanded=False):
    st.caption("Execute consultas SQL diretas no banco de dados SQLite:")
    examples = {
        "Top 10 músicas por popularidade": "SELECT track_name, artists, popularity FROM tracks ORDER BY popularity DESC LIMIT 10;",
        "Gêneros mais frequentes": "SELECT track_genre, COUNT(*) AS total FROM tracks GROUP BY track_genre ORDER BY total DESC LIMIT 10;",
        "Faixas acústicas mais populares": "SELECT track_name, acousticness, artists FROM tracks WHERE acousticness > 0.8 ORDER BY popularity DESC LIMIT 10;",
        "Faixas com energia alta": "SELECT track_name, energy, popularity FROM tracks WHERE energy > 0.8 ORDER BY popularity DESC LIMIT 10;",
        "Músicas mais longas": "SELECT track_name, duration_ms FROM tracks ORDER BY duration_ms DESC LIMIT 10;",
    }
    selected_example = st.selectbox("Exemplos de consulta", list(examples.keys()))
    query = st.text_area("Comando SQL", value=examples[selected_example], height=130)

    if st.button("Executar Consulta SQL"):
        try:
            result = execute_query(query)
            st.dataframe(result, use_container_width=True)
        except Exception as exc:
            st.error(f"Erro na consulta: {exc}")

st.markdown(
    f"<div style='text-align:center; color:#777; font-size:0.85rem; margin-top:2.5rem;'>"
    f"Acervo Spotify: {len(dataset):,} faixas disponíveis"
    f"</div>",
    unsafe_allow_html=True,
)
