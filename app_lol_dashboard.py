import os
import time
from datetime import datetime

import requests
import pandas as pd
import plotly.express as px
import streamlit as st


REGION = "americas"
API_KEY = os.getenv("RIOT_API_KEY")

BASE_MATCH_URL = f"https://{REGION}.api.riotgames.com/lol/match/v5/matches"


st.set_page_config(
    page_title="LoL Match Dashboard",
    page_icon="🎮",
    layout="wide"
)


def get_headers():
    if not API_KEY:
        st.error("Variável de ambiente RIOT_API_KEY não encontrada.")
        st.stop()

    return {
        "X-Riot-Token": API_KEY
    }


def riot_get(url, params=None):
    """
    Cliente simples com tratamento básico de rate limit.
    Se receber 429, aguarda o tempo indicado em Retry-After.
    """
    headers = get_headers()

    while True:
        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=30
        )

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", "2"))
            st.warning(f"Rate limit atingido. Aguardando {retry_after}s...")
            time.sleep(retry_after)
            continue

        if response.status_code == 403:
            st.error("Erro 403: API Key inválida, expirada ou sem permissão.")
            st.stop()

        if response.status_code == 404:
            return None

        response.raise_for_status()
        return response.json()


def get_match_ids_by_puuid(puuid, count=20, queue=None):
    url = f"{BASE_MATCH_URL}/by-puuid/{puuid}/ids"

    params = {
        "start": 0,
        "count": count
    }

    if queue:
        params["queue"] = queue

    return riot_get(url, params=params)


def get_match_detail(match_id):
    url = f"{BASE_MATCH_URL}/{match_id}"
    return riot_get(url)


def safe_get_challenge(participant, key, default=0):
    return participant.get("challenges", {}).get(key, default)


def parse_match(match_json):
    """
    Transforma o JSON bruto de uma partida em linhas por participante.
    """
    if not match_json:
        return []

    metadata = match_json.get("metadata", {})
    info = match_json.get("info", {})

    match_id = metadata.get("matchId")
    game_mode = info.get("gameMode")
    queue_id = info.get("queueId")
    platform_id = info.get("platformId")
    game_version = info.get("gameVersion")
    game_duration_seconds = info.get("gameDuration", 0)
    game_creation = info.get("gameCreation")

    try:
        game_date = datetime.fromtimestamp(game_creation / 1000)
    except Exception:
        game_date = None

    rows = []

    for p in info.get("participants", []):
        kills = p.get("kills", 0)
        deaths = p.get("deaths", 0)
        assists = p.get("assists", 0)

        kda = (kills + assists) / max(deaths, 1)

        row = {
            "match_id": match_id,
            "game_date": game_date,
            "game_mode": game_mode,
            "queue_id": queue_id,
            "platform_id": platform_id,
            "game_version": game_version,
            "duration_min": round(game_duration_seconds / 60, 2),

            "puuid": p.get("puuid"),
            "riot_id": f"{p.get('riotIdGameName', '')}#{p.get('riotIdTagline', '')}",
            "champion": p.get("championName"),
            "team_id": p.get("teamId"),
            "win": p.get("win"),

            "kills": kills,
            "deaths": deaths,
            "assists": assists,
            "kda": round(kda, 2),

            "gold": p.get("goldEarned", 0),
            "damage_champions": p.get("totalDamageDealtToChampions", 0),
            "damage_taken": p.get("totalDamageTaken", 0),
            "damage_mitigated": p.get("damageSelfMitigated", 0),
            "healing": p.get("totalHeal", 0),

            "cs": p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0),
            "vision_score": p.get("visionScore", 0),

            "double_kills": p.get("doubleKills", 0),
            "triple_kills": p.get("tripleKills", 0),
            "quadra_kills": p.get("quadraKills", 0),
            "penta_kills": p.get("pentaKills", 0),

            "kill_participation": safe_get_challenge(p, "killParticipation", 0),
            "damage_per_minute": safe_get_challenge(p, "damagePerMinute", 0),
            "gold_per_minute": safe_get_challenge(p, "goldPerMinute", 0),
            "team_damage_percentage": safe_get_challenge(p, "teamDamagePercentage", 0),
            "skillshots_hit": safe_get_challenge(p, "skillshotsHit", 0),
            "skillshots_dodged": safe_get_challenge(p, "skillshotsDodged", 0),
        }

        rows.append(row)

    return rows


def fetch_matches(match_ids):
    all_rows = []
    raw_matches = []

    progress = st.progress(0)

    for index, match_id in enumerate(match_ids):
        match_json = get_match_detail(match_id)

        if match_json:
            raw_matches.append(match_json)
            all_rows.extend(parse_match(match_json))

        progress.progress((index + 1) / len(match_ids))

    return pd.DataFrame(all_rows), raw_matches


def format_percent(value):
    try:
        return f"{value * 100:.1f}%"
    except Exception:
        return "0.0%"


st.title("🎮 Dashboard simples de partidas — League of Legends")

st.markdown(
    """
    Este dashboard consome a Riot API, busca partidas por PUUID ou por lista de match IDs,
    normaliza os participantes e monta visualizações simples de desempenho.
    """
)

with st.sidebar:
    st.header("Configuração")

    input_mode = st.radio(
        "Modo de entrada",
        ["Buscar por PUUID", "Colar match IDs"]
    )

    count = st.slider(
        "Quantidade de partidas",
        min_value=1,
        max_value=20,
        value=10,
        step=1
    )

    queue_label = st.selectbox(
        "Filtro de fila",
        [
            "Todas",
            "ARAM - 450",
            "Ranked Solo/Duo - 420",
            "Normal Draft - 400",
            "Flex - 440"
        ]
    )

    queue_map = {
        "Todas": None,
        "ARAM - 450": 450,
        "Ranked Solo/Duo - 420": 420,
        "Normal Draft - 400": 400,
        "Flex - 440": 440
    }

    queue_id = queue_map[queue_label]

    target_puuid = ""
    pasted_match_ids = ""

    if input_mode == "Buscar por PUUID":
        target_puuid = st.text_area(
            "PUUID do jogador",
            placeholder="Cole o PUUID aqui",
            height=100
        ).strip()
    else:
        pasted_match_ids = st.text_area(
            "Match IDs, um por linha ou separados por vírgula",
            placeholder="BR1_3236604303\nBR1_3236583884",
            height=160
        )

    buscar = st.button("Buscar dados")


if buscar:
    if input_mode == "Buscar por PUUID":
        if not target_puuid:
            st.error("Informe um PUUID.")
            st.stop()

        with st.spinner("Buscando lista de partidas..."):
            match_ids = get_match_ids_by_puuid(
                puuid=target_puuid,
                count=count,
                queue=queue_id
            )

    else:
        raw_ids = pasted_match_ids.replace(",", "\n").splitlines()
        match_ids = [x.strip() for x in raw_ids if x.strip()]
        match_ids = match_ids[:count]

        if not match_ids:
            st.error("Informe pelo menos um match ID.")
            st.stop()

    st.subheader("Match IDs encontrados")
    st.write(match_ids)

    with st.spinner("Buscando detalhes das partidas..."):
        df, raw_matches = fetch_matches(match_ids)

    if df.empty:
        st.warning("Nenhum dado encontrado.")
        st.stop()

    st.success(f"{len(match_ids)} partida(s) carregada(s), {len(df)} participantes processados.")

    st.divider()

    # Dataset geral
    df["resultado"] = df["win"].map({True: "Vitória", False: "Derrota"})
    df["time"] = df["team_id"].map({100: "Azul / Team 100", 200: "Vermelho / Team 200"})
    df["kill_participation_pct"] = df["kill_participation"] * 100
    df["team_damage_percentage_pct"] = df["team_damage_percentage"] * 100

    # Se tiver target PUUID, separa visão do jogador
    player_df = pd.DataFrame()

    if input_mode == "Buscar por PUUID" and target_puuid:
        player_df = df[df["puuid"] == target_puuid].copy()

    tab_geral, tab_jogador, tab_partidas, tab_raw = st.tabs(
        ["Visão geral", "Jogador alvo", "Partidas", "JSON bruto"]
    )

    with tab_geral:
        st.header("Visão geral das partidas")

        col1, col2, col3, col4 = st.columns(4)

        total_matches = df["match_id"].nunique()
        avg_duration = df.drop_duplicates("match_id")["duration_min"].mean()
        total_kills = df["kills"].sum()
        avg_damage = df["damage_champions"].mean()

        col1.metric("Partidas", total_matches)
        col2.metric("Duração média", f"{avg_duration:.1f} min")
        col3.metric("Kills totais", int(total_kills))
        col4.metric("Dano médio/player", f"{avg_damage:,.0f}".replace(",", "."))

        st.subheader("Dano a campeões por jogador")

        damage_df = df.sort_values("damage_champions", ascending=False).head(30)

        fig_damage = px.bar(
            damage_df,
            x="damage_champions",
            y="riot_id",
            color="resultado",
            orientation="h",
            hover_data=["champion", "kills", "deaths", "assists", "kda", "match_id"],
            labels={
                "damage_champions": "Dano a campeões",
                "riot_id": "Jogador",
                "resultado": "Resultado"
            }
        )
        fig_damage.update_layout(yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig_damage, use_container_width=True)

        st.subheader("Relação ouro x dano")

        fig_scatter = px.scatter(
            df,
            x="gold",
            y="damage_champions",
            color="resultado",
            size="kills",
            hover_name="riot_id",
            hover_data=["champion", "kda", "team_damage_percentage_pct", "match_id"],
            labels={
                "gold": "Ouro ganho",
                "damage_champions": "Dano a campeões",
                "resultado": "Resultado"
            }
        )
        st.plotly_chart(fig_scatter, use_container_width=True)

        st.subheader("Resumo por campeão")

        champion_summary = (
            df.groupby("champion", as_index=False)
            .agg(
                partidas=("match_id", "nunique"),
                kills=("kills", "sum"),
                deaths=("deaths", "sum"),
                assists=("assists", "sum"),
                dano_medio=("damage_champions", "mean"),
                kda_medio=("kda", "mean"),
                winrate=("win", "mean")
            )
            .sort_values(["partidas", "dano_medio"], ascending=False)
        )

        champion_summary["winrate"] = champion_summary["winrate"].apply(lambda x: f"{x * 100:.1f}%")
        champion_summary["dano_medio"] = champion_summary["dano_medio"].round(0)
        champion_summary["kda_medio"] = champion_summary["kda_medio"].round(2)

        st.dataframe(champion_summary, use_container_width=True)

    with tab_jogador:
        st.header("Análise do jogador alvo")

        if player_df.empty:
            st.info("Para ver esta aba, use o modo 'Buscar por PUUID'.")
        else:
            player_df = player_df.sort_values("game_date")

            wins = player_df["win"].sum()
            games = len(player_df)
            winrate = wins / games if games else 0

            avg_kda = player_df["kda"].mean()
            avg_damage = player_df["damage_champions"].mean()
            avg_kp = player_df["kill_participation"].mean()

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Partidas", games)
            col2.metric("Winrate", format_percent(winrate))
            col3.metric("KDA médio", f"{avg_kda:.2f}")
            col4.metric("KP médio", format_percent(avg_kp))

            st.subheader("Histórico de KDA")

            fig_kda = px.line(
                player_df,
                x="game_date",
                y="kda",
                markers=True,
                hover_data=["champion", "kills", "deaths", "assists", "resultado", "match_id"],
                labels={
                    "game_date": "Data",
                    "kda": "KDA"
                }
            )
            st.plotly_chart(fig_kda, use_container_width=True)

            st.subheader("Dano, ouro e participação em kill por partida")

            metric_choice = st.selectbox(
                "Métrica",
                [
                    "damage_champions",
                    "gold",
                    "kill_participation_pct",
                    "team_damage_percentage_pct",
                    "damage_per_minute",
                    "gold_per_minute"
                ]
            )

            fig_metric = px.bar(
                player_df,
                x="match_id",
                y=metric_choice,
                color="resultado",
                hover_data=["champion", "kills", "deaths", "assists", "kda"],
                labels={
                    "match_id": "Partida",
                    metric_choice: metric_choice
                }
            )
            st.plotly_chart(fig_metric, use_container_width=True)

            st.subheader("Partidas do jogador")

            player_table = player_df[
                [
                    "game_date",
                    "match_id",
                    "game_mode",
                    "champion",
                    "resultado",
                    "kills",
                    "deaths",
                    "assists",
                    "kda",
                    "damage_champions",
                    "gold",
                    "kill_participation_pct",
                    "team_damage_percentage_pct"
                ]
            ].copy()

            player_table["kill_participation_pct"] = player_table["kill_participation_pct"].round(1)
            player_table["team_damage_percentage_pct"] = player_table["team_damage_percentage_pct"].round(1)

            st.dataframe(player_table, use_container_width=True)

    with tab_partidas:
        st.header("Resumo por partida")

        match_summary = (
            df.groupby(["match_id", "game_mode", "duration_min", "team_id", "win"], as_index=False)
            .agg(
                kills=("kills", "sum"),
                deaths=("deaths", "sum"),
                assists=("assists", "sum"),
                gold=("gold", "sum"),
                damage_champions=("damage_champions", "sum"),
                damage_taken=("damage_taken", "sum")
            )
        )

        match_summary["resultado"] = match_summary["win"].map({True: "Vitória", False: "Derrota"})
        match_summary["time"] = match_summary["team_id"].map({100: "Azul / Team 100", 200: "Vermelho / Team 200"})

        st.dataframe(match_summary, use_container_width=True)

        st.subheader("Kills por time")

        fig_team_kills = px.bar(
            match_summary,
            x="match_id",
            y="kills",
            color="time",
            barmode="group",
            hover_data=["resultado", "gold", "damage_champions"],
            labels={
                "match_id": "Partida",
                "kills": "Kills",
                "time": "Time"
            }
        )
        st.plotly_chart(fig_team_kills, use_container_width=True)

    with tab_raw:
        st.header("Amostra do JSON bruto")

        selected_match = st.selectbox("Selecione uma partida", match_ids)

        selected_raw = None
        for raw in raw_matches:
            if raw.get("metadata", {}).get("matchId") == selected_match:
                selected_raw = raw
                break

        if selected_raw:
            st.json(selected_raw)
        else:
            st.warning("JSON não encontrado para essa partida.")