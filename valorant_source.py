"""
valorant_source.py : stats Valorant pour Box Screen.

Deux sources, combinées dans un seul état :

1. Client Valorant local (le jeu tourne sur ce PC)
   Lit le lockfile du Riot Client puis la présence du joueur sur l'API locale
   (https://127.0.0.1:<port>). Donne l'état live : menu, sélection d'agent,
   en partie, map, mode, score de la manche. Lecture seule.

2. HenrikDev API (https://api.henrikdev.xyz, non officielle, gratuite)
   Rang, RR, dernier gain/perte, peak, 5 derniers matchs.
   Clé à générer sur https://api.henrikdev.xyz/dashboard/ (via leur Discord).

Config : valorant_config.json (modèle : valorant_config.example.json).
Cache  : valorant_cache.json (dernières données HenrikDev, relu au démarrage).

API du module :
    start()            lance le thread de polling (idempotent)
    snapshot()         état complet pour le dashboard (dict JSON-sérialisable)
    panel_mode()       "live" | "menu" | None   (ce que le panel doit montrer)
    panel_priority()   "after_lyrics" | "priority"
    set_panel(mode)    "after_lyrics" | "priority" | "off" (persisté)
    refresh_now()      force un rafraîchissement HenrikDev

Agent en direct (option live_agent, activée par défaut) :
    Pendant la sélection d'agent et la partie, lit l'agent choisi via les serveurs de
    partie de Riot (glz), avec le jeton du client local. Lecture seule, deux requêtes GET.
    Ces endpoints ne sont pas officiels : live_agent=false dans valorant_config.json coupe tout.
    Les artworks ASCII sont lus dans agents_ascii/<agent>.txt (route GET /agents/<agent>.txt).
"""

from __future__ import annotations

import base64
import copy
import re
import unicodedata
import datetime as _dt
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_HERE = Path(__file__).parent
CONFIG_PATH = _HERE / "valorant_config.json"
CACHE_PATH = _HERE / "valorant_cache.json"
HISTORY_PATH = _HERE / "valorant_history.json"     # tous les matchs vus, pour les stats (voir VALORANT.md)

HENRIK_BASE = "https://api.henrikdev.xyz"
PANEL_MODES = ("after_lyrics", "priority", "off")

_DEFAULT_CONFIG = {
    "enabled": True,
    "api_key": "",
    "name": "",
    "tag": "",
    "affinity": "eu",
    "platform": "pc",
    "panel": "after_lyrics",
    "live_agent": True,
}

# ── Tables de traduction ────────────────────────────────────────────────────

_TIER_FAMILIES = [
    ("Fer", "#7a7f84"),
    ("Bronze", "#b0763d"),
    ("Argent", "#c9d3d6"),
    ("Or", "#e9c349"),
    ("Platine", "#3fb6c1"),
    ("Diamant", "#c78cf0"),
    ("Ascendant", "#3ecf7a"),
    ("Immortel", "#e0455f"),
]
_UNRANKED_COLOR = "#8a8f99"
_RADIANT_COLOR = "#fff1a6"


def tier_info(tier_id) -> tuple[str, str]:
    """id de rang (0 à 27) → (nom français, couleur hex)."""
    try:
        tid = int(tier_id)
    except (TypeError, ValueError):
        return "Non classé", _UNRANKED_COLOR
    if tid == 27:
        return "Radiant", _RADIANT_COLOR
    if 3 <= tid <= 26:
        name, color = _TIER_FAMILIES[(tid - 3) // 3]
        return f"{name} {(tid - 3) % 3 + 1}", color
    return "Non classé", _UNRANKED_COLOR


# Noms internes des maps (chemin /Game/Maps/<Code>/...) → nom affiché
_MAP_CODES = {
    "ascent": "Ascent",
    "bonsai": "Split",
    "duality": "Bind",
    "triad": "Haven",
    "port": "Icebox",
    "foxtrot": "Breeze",
    "canyon": "Fracture",
    "pitt": "Pearl",
    "jam": "Lotus",
    "juliett": "Sunset",
    "infinity": "Abyss",
    "rook": "Corrode",
    "range": "Stand de tir",
    "poveglia": "Stand de tir",
    "hurm_alley": "District",
    "hurm_bowl": "Kasbah",
    "hurm_yard": "Piazza",
    "hurm_helix": "Drift",
    "hurm_hightide": "Glitch",
}


def map_name(path_or_name) -> str:
    """'/Game/Maps/Triad/Triad' → 'Haven'. Laisse passer un nom déjà lisible."""
    if not path_or_name:
        return ""
    raw = str(path_or_name).strip()
    parts = [p for p in raw.replace("\\", "/").split("/") if p]
    if not parts:
        return ""
    # /Game/Maps/<Code>/<Code> ou /Game/Maps/HURM/HURM_Alley/HURM_Alley
    after_maps = None
    for i, p in enumerate(parts):
        if p.lower() == "maps" and i + 1 < len(parts):
            after_maps = parts[i + 1]
            break
    for code in (parts[-1], after_maps):
        if code and code.lower() in _MAP_CODES:
            return _MAP_CODES[code.lower()]
    return (after_maps or parts[-1]).replace("_", " ")


_QUEUES = {
    "competitive": "Compétition",
    "unrated": "Non classé",
    "swiftplay": "Swiftplay",
    "spikerush": "Spike Rush",
    "deathmatch": "Deathmatch",
    "ggteam": "Escalation",
    "hurm": "Team Deathmatch",
    "premier": "Premier",
    "newmap": "Nouvelle map",
    "onefa": "Réplication",
    "snowball": "Boules de neige",
    "valaram": "All Random",
}


def queue_name(queue_id, fallback: str = "") -> str:
    if not queue_id:
        return fallback or ""
    return _QUEUES.get(str(queue_id).lower(), fallback or str(queue_id).capitalize())


def agent_slug(name) -> str:
    """'KAY/O' → 'kay-o', 'Reyna' → 'reyna' : nom du fichier agents_ascii/<slug>.txt."""
    s = unicodedata.normalize("NFD", str(name or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


# ── Parsing (fonctions pures, testées dans test_valorant_source.py) ─────────

def decode_presence_private(private_b64) -> dict:
    """Décode le champ 'private' (base64 → JSON) d'une présence Valorant."""
    if not private_b64:
        return {}
    try:
        s = str(private_b64)
        raw = base64.b64decode(s + "=" * (-len(s) % 4))
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _pget(d: dict, key: str):
    """Cherche une clé à plat (ancien format) ou dans les sous-objets (format 2024+)."""
    if key in d:
        return d[key]
    for sub in ("matchPresenceData", "partyPresenceData", "playerPresenceData"):
        s = d.get(sub)
        if isinstance(s, dict) and key in s:
            return s[key]
    return None


def parse_presence(private: dict) -> dict:
    """Présence décodée → état live lisible."""
    loop = _pget(private, "sessionLoopState") or None
    flow = _pget(private, "provisioningFlow") or ""
    qid = _pget(private, "queueId") or ""
    if flow == "CustomGame":
        mode = "Personnalisée"
    elif flow == "ShootingRange":
        mode = "Stand de tir"
    else:
        mode = queue_name(qid)

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    in_match = loop in ("PREGAME", "INGAME")
    return {
        "state": loop,
        "map": map_name(_pget(private, "matchMap")) if in_match else "",
        "mode": mode,
        "queue_id": qid,
        "score_ally": _int(_pget(private, "partyOwnerMatchScoreAllyTeam")) if loop == "INGAME" else None,
        "score_enemy": _int(_pget(private, "partyOwnerMatchScoreEnemyTeam")) if loop == "INGAME" else None,
        "party_size": _int(_pget(private, "partySize")),
        "tier_id": _int(_pget(private, "competitiveTier")),
        "level": _int(_pget(private, "accountLevel")),
    }


def parse_mmr(payload: dict) -> dict:
    """Réponse HenrikDev /v3/by-puuid/mmr → bloc rang."""
    data = (payload or {}).get("data") or {}
    cur = data.get("current") or {}
    tier = cur.get("tier") or {}
    tid = tier.get("id")
    name, color = tier_info(tid)
    peak = data.get("peak") or {}
    peak_tier = peak.get("tier") or {}
    peak_name, peak_color = tier_info(peak_tier.get("id")) if peak_tier else ("", "")
    return {
        "tier_id": tid,
        "tier": name,
        "color": color,
        "rr": cur.get("rr"),
        "last_change": cur.get("last_change"),
        "elo": cur.get("elo"),
        "leaderboard": cur.get("leaderboard_placement"),
        "peak_tier_id": peak_tier.get("id"),
        "peak_tier": peak_name,
        "peak_color": peak_color,
        "peak_season": (peak.get("season") or {}).get("short"),
    }


def parse_matches(payload: dict, puuid: str) -> list[dict]:
    """Réponse HenrikDev /v4/by-puuid/matches → liste de matchs résumés (du point de vue du joueur)."""
    out = []
    for m in (payload or {}).get("data") or []:
        meta = m.get("metadata") or {}
        me = next((p for p in m.get("players") or [] if p.get("puuid") == puuid), None)
        if me is None:
            continue
        stats = me.get("stats") or {}
        teams = m.get("teams") or []
        mine = next((t for t in teams if t.get("team_id") == me.get("team_id")), None)
        rounds = (mine or {}).get("rounds") or {}
        shots = sum(int(stats.get(k) or 0) for k in ("headshots", "bodyshots", "legshots"))
        queue = meta.get("queue") or {}
        won = mine.get("won") if mine else None
        if mine and won is False and rounds.get("won") is not None and rounds.get("won") == rounds.get("lost"):
            result = "draw"
        elif won is True:
            result = "win"
        elif won is False:
            result = "loss"
        else:
            result = None
        out.append({
            "match_id": meta.get("match_id"),
            "map": map_name((meta.get("map") or {}).get("name")),
            "mode": queue_name(queue.get("id"), queue.get("name") or ""),
            "started_at": meta.get("started_at"),
            "agent": (me.get("agent") or {}).get("name") or "",
            "result": result,
            "rounds_won": rounds.get("won"),
            "rounds_lost": rounds.get("lost"),
            "kills": stats.get("kills"),
            "deaths": stats.get("deaths"),
            "assists": stats.get("assists"),
            "score": stats.get("score"),
            "hs_pct": round(100 * int(stats.get("headshots") or 0) / shots) if shots else None,
        })
    return out


def parse_sessions_region(payload) -> tuple[str, str] | None:
    """/product-session/v1/external-sessions → (région, shard) lus dans -ares-deployment=."""
    for sess in (payload or {}).values() if isinstance(payload, dict) else []:
        if not isinstance(sess, dict) or sess.get("productId") != "valorant":
            continue
        args = ((sess.get("launchConfiguration") or {}).get("arguments")) or []
        for a in args:
            if isinstance(a, str) and a.startswith("-ares-deployment="):
                region = a.split("=", 1)[1].strip().lower()
                if region:
                    shard = "na" if region in ("latam", "br") else region
                    return region, shard
    return None


def parse_pregame_match(payload, puuid: str) -> tuple[str, str] | None:
    """/pregame/v1/matches/<id> → (CharacterID, 'selected'|'locked'|'') pour ce joueur."""
    p = payload or {}
    teams = [p.get("AllyTeam") or {}] + list(p.get("Teams") or [])
    for t in teams:
        for pl in (t or {}).get("Players") or []:
            if pl.get("Subject") == puuid:
                cid = (pl.get("CharacterID") or "").lower()
                st = (pl.get("CharacterSelectionState") or "").lower()
                return (cid, st) if cid else ("", "")
    return None


def parse_coregame_match(payload, puuid: str) -> str:
    """/core-game/v1/matches/<id> → CharacterID de ce joueur."""
    for pl in (payload or {}).get("Players") or []:
        if pl.get("Subject") == puuid:
            return (pl.get("CharacterID") or "").lower()
    return ""


_SIDES = {"red": "attack", "blue": "defense"}   # l'équipe rouge commence en attaque


def parse_party(presences, puuid: str) -> tuple[list[dict], set]:
    """Présences locales → membres de ton groupe (toi en premier) et l'ensemble de leurs puuid."""
    pres = [p for p in presences or [] if p.get("product") == "valorant"]
    mine = next((p for p in pres if p.get("puuid") == puuid), None)
    if mine is None:
        return [], set()
    pid = _pget(decode_presence_private(mine.get("private")), "partyId")
    if not pid:
        return [], {puuid}
    out, ids = [], set()
    for p in pres:
        priv = decode_presence_private(p.get("private"))
        if _pget(priv, "partyId") != pid:
            continue
        try:
            tid = int(_pget(priv, "competitiveTier"))
        except (TypeError, ValueError):
            tid = None
        tier, color = tier_info(tid)
        ids.add(p.get("puuid"))
        out.append({"name": p.get("game_name") or "", "tag": p.get("game_tag") or "",
                    "tier_id": tid, "tier": tier, "color": color,
                    "level": _pget(priv, "accountLevel"), "self": p.get("puuid") == puuid})
    out.sort(key=lambda r: not r["self"])
    return out, ids


def _identity_level(pl: dict):
    ident = pl.get("PlayerIdentity") or {}
    return None if ident.get("HideAccountLevel") else ident.get("AccountLevel")


def parse_pregame_full(payload, puuid: str, party: set | None = None) -> dict:
    """/pregame/v1/matches/<id> → ton équipe en sélection, côté, chrono, verrous adverses."""
    p, party = payload or {}, party or set()
    ally = p.get("AllyTeam") or {}
    team = []
    for pl in ally.get("Players") or []:
        team.append({"cid": (pl.get("CharacterID") or "").lower(),
                     "state": (pl.get("CharacterSelectionState") or "").lower(),
                     "self": pl.get("Subject") == puuid, "party": pl.get("Subject") in party,
                     "level": _identity_level(pl)})
    team.sort(key=lambda r: (not r["self"], not r["party"]))
    ns = p.get("PhaseTimeRemainingNS")
    return {"team": team, "side": _SIDES.get(str(ally.get("TeamID") or "").lower(), ""),
            "phase_left": ns / 1e9 if isinstance(ns, (int, float)) and ns > 0 else None,
            "enemy_locks": p.get("EnemyTeamLockCount"), "enemy_size": p.get("EnemyTeamSize"),
            "ranked": p.get("IsRanked") if isinstance(p.get("IsRanked"), bool) else None}


def parse_coregame_full(payload, puuid: str, party: set | None = None) -> dict:
    """/core-game/v1/matches/<id> → les 10 agents (alliés / adversaires), côté de départ, classée ou non."""
    p, party = payload or {}, party or set()
    players = p.get("Players") or []
    me = next((pl for pl in players if pl.get("Subject") == puuid), None)
    my_team = (me or {}).get("TeamID")
    rows = []
    for pl in players:
        if pl.get("IsCoach"):
            continue
        rows.append({"team": "ally" if pl.get("TeamID") == my_team else "enemy",
                     "cid": (pl.get("CharacterID") or "").lower(),
                     "self": pl.get("Subject") == puuid, "party": pl.get("Subject") in party,
                     "level": _identity_level(pl)})
    rows.sort(key=lambda r: (r["team"] != "ally", not r["self"], not r["party"]))
    mm = p.get("MatchmakingData") or {}
    return {"players": rows, "side": _SIDES.get(str(my_team or "").lower(), ""),
            "ranked": mm.get("IsRanked") if isinstance(mm.get("IsRanked"), bool) else None}


_CEREMONIES = {"ceremonyflawless": "Flawless", "ceremonyace": "Ace", "ceremonyclutch": "Clutch",
               "ceremonythrifty": "Thrifty", "ceremonyteamace": "Team ace", "ceremonycloser": "Closer"}


def _round_kind(result) -> str:
    r = str(result or "").lower()
    if "defus" in r:
        return "defuse"
    if "deton" in r or "explo" in r or "bomb" in r and "defus" not in r:
        return "spike"
    if "elim" in r:
        return "elim"
    if "time" in r or "timer" in r:
        return "time"
    return "other"


def parse_match_recap(m: dict, puuid: str) -> dict | None:
    """Match complet HenrikDev v4 → récap de fin de match.

    Pseudos : seulement le tien et ceux de ton groupe (même party_id). Les autres joueurs
    sont désignés par leur agent, pour respecter le mode incognito.
    """
    if not isinstance(m, dict):
        return None
    meta = m.get("metadata") or {}
    players = [p for p in m.get("players") or [] if isinstance(p, dict)]
    me = next((p for p in players if p.get("puuid") == puuid), None)
    if me is None:
        return None
    my_team, my_party = me.get("team_id"), me.get("party_id")
    team_of = {p.get("puuid"): p.get("team_id") for p in players}
    teams = {t.get("team_id"): t for t in m.get("teams") or []}
    mine_t = teams.get(my_team) or {}
    rw, rl = (mine_t.get("rounds") or {}).get("won"), (mine_t.get("rounds") or {}).get("lost")
    rounds = m.get("rounds") or []
    n_rounds = len(rounds) or ((rw or 0) + (rl or 0)) or 1

    kills = [k for k in m.get("kills") or [] if isinstance(k, dict)]
    by_round: dict = {}
    for k in kills:
        by_round.setdefault(k.get("round"), []).append(k)
    fb, fd = {}, {}
    for ks in by_round.values():
        ks.sort(key=lambda k: k.get("time_in_round_in_ms") or 0)
        first = ks[0]
        kp, vp = (first.get("killer") or {}).get("puuid"), (first.get("victim") or {}).get("puuid")
        fb[kp] = fb.get(kp, 0) + 1
        fd[vp] = fd.get(vp, 0) + 1

    def visible(p):
        return p.get("puuid") == puuid or (my_party and p.get("party_id") == my_party)

    rows = []
    for p in players:
        st = p.get("stats") or {}
        shots = sum(int(st.get(k) or 0) for k in ("headshots", "bodyshots", "legshots"))
        dmg = (st.get("damage") or {}).get("dealt")
        score = st.get("score") or 0
        show = visible(p)
        rows.append({
            "team": "ally" if p.get("team_id") == my_team else "enemy",
            "agent": (p.get("agent") or {}).get("name") or "",
            "name": (p.get("name") or "") if show else "", "tag": (p.get("tag") or "") if show else "",
            "self": p.get("puuid") == puuid, "party": bool(show and p.get("puuid") != puuid),
            "kills": st.get("kills"), "deaths": st.get("deaths"), "assists": st.get("assists"),
            "acs": round(score / n_rounds), "adr": round(dmg / n_rounds) if dmg is not None else None,
            "hs": round(100 * int(st.get("headshots") or 0) / shots) if shots else None,
            "fb": fb.get(p.get("puuid"), 0), "score": score,
        })
    rows.sort(key=lambda r: (r["team"] != "ally", -r["acs"]))
    for key, pool in (("mvp_match", rows), ("mvp_team", [r for r in rows if r["team"] == "ally"])):
        if pool:
            max(pool, key=lambda r: r["score"])[key] = True

    my_kills_by_round: dict = {}
    weapons: dict = {}
    for k in kills:
        if (k.get("killer") or {}).get("puuid") == puuid:
            my_kills_by_round[k.get("round")] = my_kills_by_round.get(k.get("round"), 0) + 1
            w = (k.get("weapon") or {}).get("name") or "Capacité"
            weapons[w] = weapons.get(w, 0) + 1

    rr, clutches = [], []
    for i, r in enumerate(rounds):
        rid = r.get("id", i)
        win = r.get("winning_team") == my_team
        ally_val = enemy_val = 0
        my_weapon = ""
        for s_ in r.get("stats") or []:
            pu = (s_.get("player") or {}).get("puuid")
            eco = s_.get("economy") or {}
            val = int(eco.get("loadout_value") or 0)
            if team_of.get(pu) == my_team:
                ally_val += val
            else:
                enemy_val += val
            if pu == puuid:
                my_weapon = (eco.get("weapon") or {}).get("name") or ""
        # Clutch : tu es le dernier vivant de ton équipe et la manche est gagnée
        alive = {pu for pu, t in team_of.items() if t == my_team}
        enemies = {pu for pu, t in team_of.items() if t != my_team}
        clutch_vs = None
        for k in sorted(by_round.get(rid, []), key=lambda k: k.get("time_in_round_in_ms") or 0):
            v = (k.get("victim") or {}).get("puuid")
            alive.discard(v)
            enemies.discard(v)
            if clutch_vs is None and alive == {puuid} and enemies:
                clutch_vs = len(enemies)
        if win and clutch_vs:
            clutches.append({"round": i + 1, "vs": clutch_vs})
        rr.append({"n": i + 1, "win": win, "kind": _round_kind(r.get("result")),
                   "ceremony": _CEREMONIES.get(str(r.get("ceremony") or "").lower(), ""),
                   "my_kills": my_kills_by_round.get(rid, 0), "weapon": my_weapon,
                   "eco_ally": ally_val, "eco_enemy": enemy_val})

    multis = {"3K": 0, "4K": 0, "ACE": 0}
    for n in my_kills_by_round.values():
        if n >= 5:
            multis["ACE"] += 1
        elif n == 4:
            multis["4K"] += 1
        elif n == 3:
            multis["3K"] += 1
    best = max(rr, key=lambda x: (x["my_kills"], -x["n"])) if rr else None
    myrow = next(r for r in rows if r["self"])
    won = mine_t.get("won")
    result = "draw" if rw is not None and rw == rl else ("win" if won else "loss" if won is False else None)
    q = meta.get("queue") or {}
    return {
        "match_id": meta.get("match_id"), "started_at": meta.get("started_at"),
        "map": map_name((meta.get("map") or {}).get("name")),
        "mode": queue_name(q.get("id"), q.get("name") or ""),
        "duration_s": round((meta.get("game_length_in_ms") or 0) / 1000) or None,
        "agent": myrow["agent"], "result": result, "rounds_won": rw, "rounds_lost": rl,
        "me": {**myrow, "fd": fd.get(puuid, 0), "clutches": clutches, "multis": multis,
               "best_round": {"n": best["n"], "kills": best["my_kills"]} if best and best["my_kills"] else None,
               "weapons": sorted(weapons.items(), key=lambda x: -x[1])[:3]},
        "players": rows, "rounds": rr,
    }


# ── État partagé ────────────────────────────────────────────────────────────

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()
_wake = threading.Event()

_cfg: dict = dict(_DEFAULT_CONFIG)
_cfg_mtime: float | None = None

_state: dict = {
    "account": {"name": "", "tag": "", "puuid": "", "level": None, "region": ""},
    "live": {"running": False, "state": None, "map": "", "mode": "", "queue_id": "",
             "score_ally": None, "score_enemy": None, "party_size": None,
             "tier_id": None, "level": None, "updated": 0.0,
             "agent": "", "agent_slug": "", "agent_state": "",
             "team": [], "players": [], "party": [], "side": "", "phase_end": None,
             "enemy_locks": None, "enemy_size": None, "ranked": None},
    "rank": None,
    "matches": [],
    "matches_comp": [],
    "recap": None,
    "recap_comp": None,
    "stats": None,
    "henrik": {"ok": False, "error": None, "updated": 0.0, "next": 0.0},
}

_LIVE_STALE_S = 12.0


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_config() -> None:
    """Relit valorant_config.json s'il a changé (édition à chaud de la clé)."""
    global _cfg, _cfg_mtime
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        if _cfg_mtime is not None:
            _cfg, _cfg_mtime = dict(_DEFAULT_CONFIG), None
        return
    if mtime == _cfg_mtime:
        return
    try:
        loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        cfg = dict(_DEFAULT_CONFIG)
        cfg.update({k: v for k, v in loaded.items() if k in _DEFAULT_CONFIG})
        if cfg["panel"] not in PANEL_MODES:
            cfg["panel"] = "after_lyrics"
        with _lock:
            _cfg = cfg
        _cfg_mtime = mtime
        print(f"[VALORANT] Config chargée (clé HenrikDev : {'oui' if _api_key() else 'non'})")
    except Exception as e:
        print(f"[VALORANT] Config illisible ({CONFIG_PATH.name}) : {e}")
        _cfg_mtime = mtime


def _api_key() -> str:
    return (os.environ.get("HDEV_API_KEY") or _cfg.get("api_key") or "").strip()


def _load_cache() -> None:
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    with _lock:
        if isinstance(data.get("account"), dict):
            _state["account"].update(data["account"])
        if isinstance(data.get("rank"), dict):
            _state["rank"] = data["rank"]
        if isinstance(data.get("matches"), list):
            _state["matches"] = data["matches"]
        if isinstance(data.get("recap"), dict):
            _state["recap"] = data["recap"]
        if isinstance(data.get("matches_comp"), list):
            _state["matches_comp"] = data["matches_comp"]
        if isinstance(data.get("recap_comp"), dict):
            _state["recap_comp"] = data["recap_comp"]
        if isinstance(data.get("stats"), dict):
            _state["stats"] = data["stats"]
        _state["henrik"]["updated"] = float(data.get("updated") or 0)


def _save_cache() -> None:
    with _lock:
        data = {
            "account": _state["account"],
            "rank": _state["rank"],
            "matches": _state["matches"],
            "recap": _state["recap"],
            "matches_comp": _state["matches_comp"],
            "recap_comp": _state["recap_comp"],
            "stats": _state["stats"],
            "updated": _state["henrik"]["updated"],
        }
    try:
        _atomic_write_json(CACHE_PATH, data)
    except Exception as e:
        print(f"[VALORANT] Cache non écrit : {e}")


# ── Client local (lockfile + présence) ──────────────────────────────────────

def _lockfile_path() -> Path | None:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    return Path(base) / "Riot Games" / "Riot Client" / "Config" / "lockfile"


# Le Riot Client sert l'API locale avec un certificat auto-signé : la vérification
# est désactivée UNIQUEMENT pour 127.0.0.1, jamais pour HenrikDev.
_LOCAL_SSL = ssl.create_default_context()
_LOCAL_SSL.check_hostname = False
_LOCAL_SSL.verify_mode = ssl.CERT_NONE

_local_session: dict = {"port": None, "auth": None, "puuid": None, "checked": 0.0}


def _read_lockfile() -> tuple[int, str] | None:
    path = _lockfile_path()
    if path is None or not path.exists():
        return None
    try:
        name, pid, port, password, proto = path.read_text(encoding="utf-8").strip().split(":")[:5]
        return int(port), password
    except Exception:
        return None


def _local_get(port: int, auth: str, path: str):
    req = urllib.request.Request(
        f"https://127.0.0.1:{port}{path}",
        headers={"Authorization": "Basic " + auth},
    )
    with urllib.request.urlopen(req, timeout=2, context=_LOCAL_SSL) as r:
        return json.loads(r.read().decode("utf-8"))


def _set_live_off() -> None:
    with _lock:
        live = _state["live"]
        live.update({"running": False, "state": None, "map": "", "mode": "", "queue_id": "",
                     "score_ally": None, "score_enemy": None, "party_size": None,
                     "agent": "", "agent_slug": "", "agent_state": "", "party": []})
        live.update(_MATCH_EMPTY)
        live["updated"] = time.time()


def _poll_local() -> str | None:
    """Met à jour _state['live']. Retourne l'état de boucle (MENUS/PREGAME/INGAME) ou None."""
    lf = _read_lockfile()
    if lf is None:
        _set_live_off()
        return None
    port, password = lf
    auth = base64.b64encode(f"riot:{password}".encode()).decode()

    try:
        # Session : qui est connecté (puuid + Riot ID). Relue toutes les 30 s.
        if (_local_session["port"] != port or _local_session["puuid"] is None
                or time.time() - _local_session["checked"] > 30):
            sess = _local_get(port, auth, "/chat/v1/session")
            _local_session.update({"port": port, "auth": auth, "checked": time.time(),
                                   "puuid": sess.get("puuid")})
            if sess.get("puuid"):
                with _lock:
                    acc = _state["account"]
                    if acc.get("puuid") and acc["puuid"] != sess["puuid"]:
                        # Autre compte connecté : on jette les stats de l'ancien
                        _state["rank"], _state["matches"] = None, []
                        _henrik_reschedule(5)
                    acc["puuid"] = sess["puuid"]
                    if sess.get("game_name"):
                        acc["name"] = sess["game_name"]
                        acc["tag"] = sess.get("game_tag") or acc.get("tag", "")

        puuid = _local_session["puuid"]
        pres = _local_get(port, auth, "/chat/v4/presences").get("presences") or []
    except Exception:
        # Riot Client ouvert mais API pas prête, ou client en train de se fermer
        _set_live_off()
        return None

    mine = next((p for p in pres if p.get("puuid") == puuid and p.get("product") == "valorant"), None)
    if mine is None:
        _set_live_off()   # Riot Client ouvert, Valorant pas lancé
        return None

    info = parse_presence(decode_presence_private(mine.get("private")))
    party, party_ids = parse_party(pres, puuid)
    info["party"] = party
    with _lock:
        _state["live"].update(info)
        _state["live"]["running"] = True
        _state["live"]["updated"] = time.time()
        if info.get("level"):
            _state["account"]["level"] = info["level"]
    _poll_agent(port, auth, puuid, info.get("state"), party_ids)
    return info.get("state")


# ── Agent en direct (serveurs de partie glz, lecture seule) ────────────────

AGENTS_PATH = _HERE / "valorant_agents.json"
_PLATFORM_B64 = base64.b64encode(json.dumps({
    "platformType": "PC", "platformOS": "Windows",
    "platformOSVersion": "10.0.19042.1.256.64bit", "platformChipset": "Unknown",
}).encode()).decode()

_glz: dict = {"region": None, "shard": None, "version": None, "access": None, "ent": None,
              "token_at": 0.0, "info_at": 0.0, "core_match": None, "core_full": None, "next": 0.0}
_MATCH_EMPTY = {"team": [], "players": [], "side": "", "phase_end": None,
                "enemy_locks": None, "enemy_size": None, "ranked": None, "match_id": ""}
_agents: dict = {}          # uuid → nom affiché
_roles: dict = {}           # nom d'agent → rôle en français
_agents_at = 0.0
_ROLE_FR = {"duelist": "Duelliste", "controller": "Contrôleur", "initiator": "Initiateur", "sentinel": "Sentinelle"}
_agent_note = {"msg": ""}


def _note(msg: str) -> None:
    """Journal de l'agent en direct : une ligne seulement quand le message change."""
    if msg != _agent_note["msg"]:
        _agent_note["msg"] = msg
        print(f"[VALORANT] Agent : {msg}")
        with _lock:
            _state["live"]["agent_note"] = msg


_GLZ_RE = re.compile(r"https://glz-([a-z]+)-1\.([a-z]+)\.a\.pvp\.net")


def _region_from_log() -> tuple[str, str] | None:
    """Secours : l'adresse glz-<région>-1.<shard>.a.pvp.net apparaît dans ShooterGame.log."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    try:
        with open(Path(base) / "VALORANT" / "Saved" / "Logs" / "ShooterGame.log", encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = _GLZ_RE.search(line)
                if m:
                    return m.group(1), m.group(2)
    except OSError:
        pass
    return None


def _load_agents() -> None:
    """Table uuid → nom : cache disque, rafraîchi depuis valorant-api.com une fois par jour."""
    global _agents, _agents_at, _roles
    if _agents and _roles and time.time() - _agents_at < 86400:
        return
    try:
        data = json.loads(AGENTS_PATH.read_text(encoding="utf-8"))
        if isinstance(data.get("agents"), dict):
            _agents = data["agents"]
            _roles = data.get("roles") or {}
            _agents_at = float(data.get("at") or 0)
    except Exception:
        pass
    if _agents and _roles and time.time() - _agents_at < 86400:
        return
    try:
        req = urllib.request.Request("https://valorant-api.com/v1/agents?isPlayableCharacter=true",
                                     headers={"User-Agent": "BoxScreen/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            res = json.loads(r.read().decode("utf-8"))
        table = {a["uuid"].lower(): a["displayName"] for a in res.get("data") or [] if a.get("uuid")}
        roles = {a["displayName"]: _ROLE_FR.get(str((a.get("role") or {}).get("displayName") or "").lower(), "")
                 for a in res.get("data") or [] if a.get("displayName")}
        if table:
            _agents, _roles, _agents_at = table, roles, time.time()
            _atomic_write_json(AGENTS_PATH, {"at": _agents_at, "agents": table, "roles": roles})
    except Exception as e:
        _agents_at = time.time() - 86400 + 600   # réessaie dans 10 min
        print(f"[VALORANT] Liste des agents indisponible : {e}")


def _client_version() -> str | None:
    """Version du client, lue dans ShooterGame.log (sinon valorant-api.com)."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        log = Path(base) / "VALORANT" / "Saved" / "Logs" / "ShooterGame.log"
        try:
            with open(log, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "CI server version:" in line:
                        v = line.split("CI server version:", 1)[1].strip()   # release-XX.YY-shipping-N-NNNNNNN
                        if v:
                            return v
        except OSError:
            pass
    try:
        req = urllib.request.Request("https://valorant-api.com/v1/version", headers={"User-Agent": "BoxScreen/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return (json.loads(r.read().decode("utf-8")).get("data") or {}).get("riotClientVersion")
    except Exception:
        return None


def _glz_prepare(port: int, auth: str) -> bool:
    now = time.time()
    if now - _glz["token_at"] > 240 or not _glz["access"]:
        try:
            tok = _local_get(port, auth, "/entitlements/v1/token")
        except Exception as e:
            _note(f"jeton du client local illisible ({e})")
            return False
        _glz.update({"access": tok.get("accessToken"), "ent": tok.get("token"), "token_at": now})
        if not (_glz["access"] and _glz["ent"]):
            _note("le client local n'a pas donné de jeton (accessToken / token vides)")
            return False
    if not _glz["region"] or now - _glz["info_at"] > 600:
        rs, src = None, ""
        try:
            rs, src = parse_sessions_region(_local_get(port, auth, "/product-session/v1/external-sessions")), "client"
        except Exception:
            pass
        if not rs:
            rs, src = _region_from_log(), "ShooterGame.log"
        if not rs and _cfg.get("affinity"):
            reg = str(_cfg["affinity"]).lower()
            rs, src = (reg, "na" if reg in ("latam", "br") else reg), "config"
        if rs:
            if rs != (_glz["region"], _glz["shard"]):
                print(f"[VALORANT] Agent : serveur glz-{rs[0]}-1.{rs[1]} (trouvé via {src})")
            _glz["region"], _glz["shard"] = rs
        _glz["version"] = _client_version() or _glz["version"]
        if not _glz["version"]:
            print("[VALORANT] Agent : version du client introuvable (ShooterGame.log et valorant-api.com)")
        _glz["info_at"] = now
    if not _glz["region"]:
        _note("région introuvable (ni dans le client, ni dans ShooterGame.log, ni dans la config)")
        return False
    return True


def _glz_get(path: str):
    url = f"https://glz-{_glz['region']}-1.{_glz['shard']}.a.pvp.net{path}"
    headers = {"Authorization": "Bearer " + _glz["access"], "X-Riot-Entitlements-JWT": _glz["ent"],
               "X-Riot-ClientPlatform": _PLATFORM_B64,
               # Les serveurs de partie refusent souvent l'agent par défaut de Python (Python-urllib)
               "User-Agent": "ShooterGame/13 Windows/10.0.19043.1.256.64bit"}
    if _glz["version"]:
        headers["X-Riot-ClientVersion"] = _glz["version"]
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=4) as r:
        return json.loads(r.read().decode("utf-8"))


def _set_agent(cid: str, state: str) -> None:
    if cid and cid not in _agents:
        _load_agents()
    name = _agents.get(cid, "") if cid else ""
    if cid and not name:
        _note(f"agent {cid} inconnu dans la liste valorant-api.com")
    elif name:
        _note(f"{name} ({state})")
    with _lock:
        _state["live"].update({"agent": name, "agent_slug": agent_slug(name), "agent_state": state if name else ""})


def _agent_name(cid: str) -> str:
    if cid and cid not in _agents:
        _load_agents()
    return _agents.get(cid, "") if cid else ""


def _named(rows: list[dict]) -> list[dict]:
    """Remplace les uuid d'agent par leur nom (aucun pseudo de joueur ici)."""
    out = []
    for r in rows:
        r = dict(r)
        r["agent"] = _agent_name(r.pop("cid", ""))
        out.append(r)
    return out


def _poll_agent(port: int, auth: str, puuid: str, loop: str | None, party: set | None = None) -> None:
    """Sélection (pregame) puis partie (core-game) : ton agent, ton équipe, les 10 agents. Ne lève jamais."""
    if loop in ("PREGAME", "INGAME") and not _cfg.get("live_agent", True):
        _note("désactivé (live_agent=false)")
    if loop not in ("PREGAME", "INGAME") or not _cfg.get("live_agent", True) or not puuid:
        _glz["core_match"], _glz["core_full"] = None, None
        _set_agent("", "")
        with _lock:
            _state["live"].update(_MATCH_EMPTY)
        return
    if time.time() < _glz["next"]:
        return
    try:
        if not _glz_prepare(port, auth):
            return
        if loop == "PREGAME":
            mid = _glz_get(f"/pregame/v1/players/{puuid}").get("MatchID")
            payload = _glz_get(f"/pregame/v1/matches/{mid}") if mid else None
            res = parse_pregame_match(payload, puuid) if payload else None
            if res:
                _set_agent(res[0], res[1] or "selected")
                if not res[0]:
                    _note("sélection en cours, aucun agent survolé")
                full = parse_pregame_full(payload, puuid, party)
                with _lock:
                    _state["live"].update({
                        "team": _named(full["team"]), "players": [], "side": full["side"],
                        "phase_end": time.time() + full["phase_left"] if full["phase_left"] else None,
                        "enemy_locks": full["enemy_locks"], "enemy_size": full["enemy_size"],
                        "ranked": full["ranked"]})
            else:
                _note("sélection : joueur absent de la réponse pregame")
        else:
            mid = _glz_get(f"/core-game/v1/players/{puuid}").get("MatchID")
            if mid and mid != _glz["core_match"]:
                _glz["core_match"] = mid
                _glz["core_full"] = parse_coregame_full(_glz_get(f"/core-game/v1/matches/{mid}"), puuid, party)
            full = _glz["core_full"] or {"players": [], "side": "", "ranked": None}
            me = next((r for r in full["players"] if r["self"]), None)
            _set_agent(me["cid"] if me else "", "ingame")
            with _lock:
                _state["live"].update({"team": [], "players": _named(full["players"]), "side": full["side"],
                                       "phase_end": None, "enemy_locks": None, "enemy_size": None,
                                       "ranked": full["ranked"], "match_id": _glz["core_match"] or ""})
    except urllib.error.HTTPError as e:
        where = "sélection (pregame)" if loop == "PREGAME" else "partie (core-game)"
        try:
            detail = e.read().decode("utf-8", "replace").strip().replace("\n", " ")[:220]
        except Exception:
            detail = ""
        ver = _glz["version"] or "aucune"
        _note(f"HTTP {e.code} sur {where}, serveur glz-{_glz['region']}-1.{_glz['shard']}, version {ver}"
              + (f" : {detail}" if detail else ""))
        if e.code in (400, 401, 403):
            _glz["token_at"] = 0.0          # jeton expiré : on le relit au prochain tour
        _glz["next"] = time.time() + (2 if e.code == 404 else 15)
    except Exception as e:
        _glz["next"] = time.time() + 15
        _note(f"indisponible ({e})")


# ── HenrikDev ───────────────────────────────────────────────────────────────

class _HenrikError(Exception):
    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


def _henrik_get(path: str, params: dict | None = None) -> dict:
    url = HENRIK_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": _api_key(),
        "Accept": "application/json",
        "User-Agent": "BoxScreen/1.0 (+valorant_source.py)",
    })
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            ra = e.headers.get("Retry-After") or e.headers.get("X-RateLimit-Reset")
            try:
                ra = float(ra)
            except (TypeError, ValueError):
                ra = 120.0
            raise _HenrikError("Limite de requêtes HenrikDev atteinte", retry_after=ra)
        if e.code in (401, 403):
            raise _HenrikError("Clé HenrikDev refusée (vérifie api_key)")
        if e.code == 404:
            raise _HenrikError("Compte introuvable chez HenrikDev (Riot ID ou région ?)")
        raise _HenrikError(f"HenrikDev HTTP {e.code}")
    except urllib.error.URLError as e:
        raise _HenrikError(f"HenrikDev injoignable ({e.reason})")


def _henrik_reschedule(delay: float) -> None:
    """Avance le prochain rafraîchissement (n'appeler qu'avec _lock tenu ou depuis le thread)."""
    h = _state["henrik"]
    target = time.time() + delay
    if target < h["next"]:
        h["next"] = target
    _wake.set()


# ── Statistiques sur tout l'historique (HenrikDev stored matches) ──────────

def parse_stored(payload, puuid: str) -> list[dict]:
    """/v1/by-puuid/stored-matches → lignes compactes, une par match, du point de vue du joueur."""
    rows = []
    for m in (payload or {}).get("data") or []:
        if not isinstance(m, dict):
            continue
        meta, st, teams = m.get("meta") or {}, m.get("stats") or {}, m.get("teams") or {}
        if st.get("puuid") and puuid and st.get("puuid") != puuid:
            continue
        team = str(st.get("team") or "").lower()
        mine, other = (teams.get("red"), teams.get("blue")) if team == "red" else (teams.get("blue"), teams.get("red"))
        if mine is None or other is None:
            continue
        shots = st.get("shots") or {}
        rows.append({
            "id": meta.get("id"), "at": meta.get("started_at"),
            "mode": str(meta.get("mode") or "").lower(),
            "map": map_name((meta.get("map") or {}).get("name")),
            "agent": (st.get("character") or {}).get("name") or "",
            "season": (meta.get("season") or {}).get("short") or (meta.get("season") or {}).get("id") or "",
            "season_id": str((meta.get("season") or {}).get("id") or "").lower(),
            "tier": st.get("tier"),
            "k": st.get("kills") or 0, "d": st.get("deaths") or 0, "a": st.get("assists") or 0,
            "score": st.get("score") or 0, "dmg": (st.get("damage") or {}).get("made"),
            "dmg_rcv": (st.get("damage") or {}).get("received"),
            "head": shots.get("head") or 0, "body": shots.get("body") or 0, "leg": shots.get("leg") or 0,
            "rw": int(mine), "rl": int(other),
        })
    rows.sort(key=lambda r: r["at"] or "")
    return rows


def row_from_v4(m: dict, puuid: str) -> dict | None:
    """Match complet HenrikDev v4 → même ligne compacte que parse_stored (pour l'historique local)."""
    if not isinstance(m, dict):
        return None
    meta = m.get("metadata") or {}
    me = next((p for p in m.get("players") or [] if isinstance(p, dict) and p.get("puuid") == puuid), None)
    if me is None or not meta.get("match_id"):
        return None
    if meta.get("is_completed") is False:
        return None
    teams = {str(t.get("team_id") or "").lower(): t for t in m.get("teams") or [] if isinstance(t, dict)}
    mine = teams.get(str(me.get("team_id") or "").lower()) or {}
    rw, rl = (mine.get("rounds") or {}).get("won"), (mine.get("rounds") or {}).get("lost")
    if rw is None or rl is None:
        return None
    st = me.get("stats") or {}
    season = meta.get("season") or {}
    q = meta.get("queue") or {}
    return {
        "id": meta.get("match_id"), "at": meta.get("started_at"),
        "mode": str(q.get("id") or q.get("mode_type") or "").lower(),
        "map": map_name((meta.get("map") or {}).get("name")),
        "agent": (me.get("agent") or {}).get("name") or "",
        "season": season.get("short") or season.get("id") or "",
        "season_id": str(season.get("id") or "").lower(),
        "tier": (me.get("tier") or {}).get("id"),
        "k": st.get("kills") or 0, "d": st.get("deaths") or 0, "a": st.get("assists") or 0,
        "score": st.get("score") or 0, "dmg": (st.get("damage") or {}).get("dealt"),
        "dmg_rcv": (st.get("damage") or {}).get("received"),
        "head": st.get("headshots") or 0, "body": st.get("bodyshots") or 0, "leg": st.get("legshots") or 0,
        "rw": int(rw), "rl": int(rl),
        "detail": detail_from_v4(m, puuid),
    }


_HALF = {"competitive": 12, "unrated": 12, "premier": 12, "custom": 12}


def _side(team: str, idx: int, half: int) -> str:
    """Côté d'une équipe à la manche idx (0 = première). Rouge commence en attaque ; en prolongation, ça alterne."""
    red_atk = idx < half or (idx >= 2 * half and (idx - 2 * half) % 2 == 0)
    is_red = team.lower() == "red"
    return "atk" if red_atk == is_red else "def"


def detail_from_v4(m: dict, puuid: str) -> dict | None:
    """Détail d'un match complet v4, de ton point de vue : duels, côtés, armes, économie, spike, groupe, KAST."""
    try:
        meta = m.get("metadata") or {}
        players = [p for p in m.get("players") or [] if isinstance(p, dict)]
        me = next((p for p in players if p.get("puuid") == puuid), None)
        if me is None:
            return None
        my_team = str(me.get("team_id") or "")
        team_of = {p.get("puuid"): str(p.get("team_id") or "") for p in players}
        rounds = m.get("rounds") or []
        kills = [k for k in m.get("kills") or [] if isinstance(k, dict)]
        by_round: dict = {}
        for k in kills:
            by_round.setdefault(k.get("round"), []).append(k)
        half = _HALF.get(str((meta.get("queue") or {}).get("id") or "").lower())

        fb = fd = kast = plants = defuses = 0
        clutch = {}
        multis = {"3K": 0, "4K": 0, "ACE": 0}
        sides = {"atk": [0, 0], "def": [0, 0]}          # [gagnées, jouées]
        pistol = [0, 0]
        eco = {"eco": [0, 0, 0], "force": [0, 0, 0], "full": [0, 0, 0]}   # [gagnées, jouées, tes kills]
        weapons: dict = {}
        for i, r in enumerate(rounds):
            rid = r.get("id", i)
            win = str(r.get("winning_team") or "") == my_team
            ks = sorted(by_round.get(rid, []), key=lambda k: k.get("time_in_round_in_ms") or 0)
            pu = lambda x: (x or {}).get("puuid")
            if ks:
                if pu(ks[0].get("killer")) == puuid:
                    fb += 1
                if pu(ks[0].get("victim")) == puuid:
                    fd += 1
            mine = [k for k in ks if pu(k.get("killer")) == puuid]
            for k in mine:
                w = (k.get("weapon") or {}).get("name") or "Capacité"
                weapons[w] = weapons.get(w, 0) + 1
            n = len(mine)
            if n >= 5:
                multis["ACE"] += 1
            elif n == 4:
                multis["4K"] += 1
            elif n == 3:
                multis["3K"] += 1
            # KAST : kill, assist, survie ou mort échangée (vengé en moins de 5 s)
            assist = any(any(pu(a) == puuid for a in k.get("assistants") or []) for k in ks)
            death = next((k for k in ks if pu(k.get("victim")) == puuid), None)
            traded = False
            if death:
                killer, t0 = pu(death.get("killer")), death.get("time_in_round_in_ms") or 0
                traded = any(pu(k.get("victim")) == killer and team_of.get(pu(k.get("killer"))) == my_team
                             and 0 <= (k.get("time_in_round_in_ms") or 0) - t0 <= 5000 for k in ks)
            if n or assist or not death or traded:
                kast += 1
            # Clutch : dernier vivant de ton équipe, manche gagnée
            alive = {p for p, t in team_of.items() if t == my_team}
            enemies = {p for p, t in team_of.items() if t != my_team}
            vs = None
            for k in ks:
                v = pu(k.get("victim"))
                alive.discard(v)
                enemies.discard(v)
                if vs is None and alive == {puuid} and enemies:
                    vs = len(enemies)
            if win and vs:
                clutch[str(vs)] = clutch.get(str(vs), 0) + 1
            if pu((r.get("plant") or {}).get("player")) == puuid:
                plants += 1
            if pu((r.get("defuse") or {}).get("player")) == puuid:
                defuses += 1
            if half:
                sd = _side(my_team, i, half)
                sides[sd][1] += 1
                sides[sd][0] += int(win)
                if i in (0, half):
                    pistol[1] += 1
                    pistol[0] += int(win)
                    continue                                # les manches pistolet ne comptent pas dans l'économie
            val = next((int(((s_.get("economy") or {}).get("loadout_value")) or 0) for s_ in r.get("stats") or []
                        if pu(s_.get("player")) == puuid), None)
            if val is not None and not (half and i in (0, half)):
                typ = "eco" if val < 1500 else "force" if val < 3900 else "full"
                eco[typ][0] += int(win)
                eco[typ][1] += 1
                eco[typ][2] += n
        party = me.get("party_id")
        mates = sorted((p.get("name") or "") for p in players
                       if p.get("puuid") != puuid and party and p.get("party_id") == party and p.get("name"))
        return {"rounds": len(rounds), "fb": fb, "fd": fd, "kast": kast, "clutch": clutch, "multis": multis,
                "sides": sides if half else None, "pistol": pistol if half else None, "eco": eco,
                "weapons": weapons, "plants": plants, "defuses": defuses, "mates": mates}
    except Exception:
        return None


def merge_rows(*sources) -> list[dict]:
    """Fusionne des listes de lignes (même match = une seule ligne, la plus complète gagne), triées par date."""
    out: dict = {}
    for rows in sources:
        for r in rows or []:
            if not r or not r.get("id"):
                continue
            old = out.get(r["id"])
            if (old is None or (not old.get("season_id") and r.get("season_id")) or (old.get("dmg") is None and r.get("dmg") is not None)
                    or (not old.get("detail") and r.get("detail")) or (old.get("dmg_rcv") is None and r.get("dmg_rcv") is not None)):
                out[r["id"]] = {**(old or {}), **{k: v for k, v in r.items() if v not in (None, "")}} if old else dict(r)
    return sorted(out.values(), key=lambda r: r.get("at") or "")


# ── Historique local (valorant_history.json) ────────────────────────────────

_hist_lock = threading.Lock()
HISTORY_VERSION = 2              # 2 : détail complet par match (duels, côtés, économie…)
_history: dict = {"puuid": None, "rows": {}, "backfill_at": 0.0, "v": HISTORY_VERSION}
_hist_loaded = False
_stored_rows: list = []
_stored_loaded = False          # l'historique HenrikDev a-t-il été lu depuis le lancement ?
_backfill_running = False


def _hist_load(puuid: str) -> None:
    global _hist_loaded
    if _hist_loaded and _history.get("puuid") == puuid:
        return
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        if data.get("puuid") == puuid and isinstance(data.get("rows"), dict):
            _history.update(data)
            if data.get("v") != HISTORY_VERSION:          # ancien format : on refait le rattrapage pour le détail
                _history.update({"v": HISTORY_VERSION, "backfill_at": 0.0})
        else:
            _history.update({"puuid": puuid, "rows": {}, "backfill_at": 0.0, "v": HISTORY_VERSION})
    except Exception:
        _history.update({"puuid": puuid, "rows": {}, "backfill_at": 0.0, "v": HISTORY_VERSION})
    _hist_loaded = True


def _hist_add(rows, puuid: str) -> int:
    """Ajoute des lignes à l'historique local ; renvoie le nombre de matchs nouveaux."""
    with _hist_lock:
        _hist_load(puuid)
        before = len(_history["rows"])
        for r in merge_rows(list(_history["rows"].values()), rows):
            _history["rows"][r["id"]] = r
        return len(_history["rows"]) - before


def _hist_save() -> None:
    with _hist_lock:
        try:
            _atomic_write_json(HISTORY_PATH, _history)
        except Exception as e:
            print(f"[VALORANT] Historique local non enregistré : {e}")


def _hist_rows() -> list[dict]:
    with _hist_lock:
        return list(_history["rows"].values())


def _recompute_stats(newest=None) -> None:
    rows = merge_rows(_stored_rows, _hist_rows())
    try:
        _load_agents()
    except Exception:
        pass
    stats = build_stats(rows, _current_act(), _roles)
    with _lock:
        prev = _state.get("stats") or {}
        stats.update({"updated": time.time(), "newest": newest if newest is not None else prev.get("newest"),
                      "sources": {"total": len(rows), "henrik_stored": len(_stored_rows), "local": len(_hist_rows())}})
        _state["stats"] = stats


def _backfill(affinity: str, platform: str, puuid: str) -> None:
    """Rattrapage : remonte la liste de matchs v4 page par page (compétition d'abord, puis tous modes)."""
    global _backfill_running
    try:
        total_new = 0
        for mode, max_pages in (("competitive", 30), (None, 15)):
            for page in range(max_pages):
                params = {"size": 10, "start": page * 10}
                if mode:
                    params["mode"] = mode
                try:
                    res = _henrik_get(f"/valorant/v4/by-puuid/matches/{affinity}/{platform}/{puuid}", params)
                except _HenrikError as e:
                    if getattr(e, "retry_after", None):
                        time.sleep(min(90, float(e.retry_after)))
                        continue
                    print(f"[VALORANT] Rattrapage interrompu : {e}")
                    break
                data = (res or {}).get("data") or []
                rows = [r for r in (row_from_v4(m, puuid) for m in data) if r]
                new = _hist_add(rows, puuid)
                total_new += new
                if len(data) < 10:
                    break                       # plus rien plus loin
                time.sleep(2.5)                 # ménage la limite de HenrikDev (30 requêtes / min)
        with _hist_lock:
            _history["backfill_at"] = time.time()
        _hist_save()
        _recompute_stats()
        print(f"[VALORANT] Rattrapage terminé : {total_new} matchs ajoutés, {len(_hist_rows())} dans l'historique local")
    except Exception as e:
        print(f"[VALORANT] Rattrapage impossible : {e}")
    finally:
        _backfill_running = False


def _local_slot(iso: str) -> str | None:
    try:
        dt = _dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone()
    except (ValueError, TypeError):
        return None
    h = dt.hour
    return "matin" if 6 <= h < 12 else "après-midi" if 12 <= h < 18 else "soir" if 18 <= h < 24 else "nuit"


def aggregate_stats(rows: list[dict], min_games: int | None = None, roles: dict | None = None) -> dict:
    """Lignes de parse_stored → mains, meilleurs agents, meilleures / pires maps, records, habitudes."""
    n = len(rows)
    if not n:
        return {"games": 0}
    if min_games is None:
        min_games = 3 if n < 60 else 5

    def res(r):
        return "win" if r["rw"] > r["rl"] else "loss" if r["rw"] < r["rl"] else "draw"

    def summary(rs):
        g = len(rs)
        w = sum(1 for r in rs if res(r) == "win")
        k, d, a = (sum(r[x] for r in rs) for x in ("k", "d", "a"))
        rounds = sum(r["rw"] + r["rl"] for r in rs) or 1
        shots = sum(r["head"] + r["body"] + r["leg"] for r in rs)
        dmg = [r for r in rs if r["dmg"] is not None]
        return {"games": g, "wins": w, "losses": sum(1 for r in rs if res(r) == "loss"),
                "winrate": round(100 * w / g), "kd": round(k / max(1, d), 2), "kda": round((k + a) / max(1, d), 2),
                "acs": round(sum(r["score"] for r in rs) / rounds),
                "adr": round(sum(r["dmg"] for r in dmg) / (sum(r["rw"] + r["rl"] for r in dmg) or 1)) if dmg else None,
                "hs": round(100 * sum(r["head"] for r in rs) / shots) if shots else None,
                "rounds_diff": sum(r["rw"] - r["rl"] for r in rs)}

    def group(key):
        out: dict = {}
        for r in rows:
            if r[key]:
                out.setdefault(r[key], []).append(r)
        return [{"name": name, **summary(rs), "share": round(100 * len(rs) / n)} for name, rs in out.items()]

    agents, maps = group("agent"), group("map")
    qual = lambda lst: [x for x in lst if x["games"] >= min_games]
    by_wr = lambda x: (x["winrate"], x["kd"], x["games"])
    agents.sort(key=lambda x: (-x["games"], -x["winrate"]))
    maps.sort(key=lambda x: (-x["games"], -x["winrate"]))
    best_maps = sorted(qual(maps), key=by_wr, reverse=True)[:3]
    worst_maps = sorted(qual(maps), key=by_wr)[:3]
    if len(qual(maps)) < 6:                          # pas assez de maps : pas de doublon entre meilleures et pires
        worst_maps = [m for m in worst_maps if m not in best_maps]

    best_k = max(rows, key=lambda r: (r["k"], r["score"]))
    best_acs = max(rows, key=lambda r: r["score"] / max(1, r["rw"] + r["rl"]))
    streak = {"win": 0, "loss": 0}
    cur, run = None, 0
    for r in rows:
        x = res(r)
        run = run + 1 if x == cur else 1
        cur = x
        if x in streak:
            streak[x] = max(streak[x], run)
    slots: dict = {}
    for r in rows:
        sl = _local_slot(r["at"])
        if sl:
            slots.setdefault(sl, []).append(r)
    order = ["matin", "après-midi", "soir", "nuit"]
    hours = [{"slot": sl, **{k: v for k, v in summary(slots[sl]).items() if k in ("games", "winrate", "kd")}}
             for sl in order if sl in slots]
    ranked = [r for r in rows if r["mode"] == "competitive" and r.get("tier")]
    return {
        **summary(rows), "min_games": min_games,
        "first": rows[0]["at"], "last": rows[-1]["at"],
        "mains": agents[:3],
        "best_agents": sorted(qual(agents), key=by_wr, reverse=True)[:3],
        "best_maps": best_maps, "worst_maps": worst_maps,
        "agents_count": len(agents), "maps_count": len(maps),
        "records": {
            "kills": {"value": best_k["k"], "agent": best_k["agent"], "map": best_k["map"], "at": best_k["at"]},
            "acs": {"value": round(best_acs["score"] / max(1, best_acs["rw"] + best_acs["rl"])),
                    "agent": best_acs["agent"], "map": best_acs["map"], "at": best_acs["at"]},
            "win_streak": streak["win"], "loss_streak": streak["loss"],
        },
        "hours": hours,
        "rank_history": [r["tier"] for r in ranked][-60:],
        **extra_stats(rows, min_games, roles or {}),
    }


STATS_VERSION = 3


def extra_stats(rows: list[dict], min_games: int, roles: dict) -> dict:
    """Combos agent × map, rôles, type de partie, dégâts, et tout ce qui vient du détail des matchs."""
    def res(r):
        return "win" if r["rw"] > r["rl"] else "loss" if r["rw"] < r["rl"] else "draw"

    def wr(rs):
        return round(100 * sum(1 for r in rs if res(r) == "win") / len(rs)) if rs else None

    def kd(rs):
        return round(sum(r["k"] for r in rs) / max(1, sum(r["d"] for r in rs)), 2) if rs else None

    out: dict = {}
    # Combos agent × map
    combos: dict = {}
    for r in rows:
        if r["agent"] and r["map"]:
            combos.setdefault((r["agent"], r["map"]), []).append(r)
    cmin = max(2, min_games - 1)
    cl = [{"agent": a, "map": m, "games": len(rs), "winrate": wr(rs), "kd": kd(rs)}
          for (a, m), rs in combos.items() if len(rs) >= cmin]
    best = sorted(cl, key=lambda x: (x["winrate"], x["kd"], x["games"]), reverse=True)[:5]
    worst = [x for x in sorted(cl, key=lambda x: (x["winrate"], x["kd"], -x["games"]))[:3] if x not in best]
    out["combos"] = {"best": best, "worst": worst, "min_games": cmin}
    # Rôles
    rl: dict = {}
    for r in rows:
        role = roles.get(r["agent"]) or ""
        if role:
            rl.setdefault(role, []).append(r)
    out["roles"] = sorted([{"role": k, "games": len(v), "winrate": wr(v), "kd": kd(v),
                            "share": round(100 * len(v) / len(rows))} for k, v in rl.items()], key=lambda x: -x["games"])
    # Type de partie
    ot = [r for r in rows if r["rw"] + r["rl"] > 24]
    close = [r for r in rows if abs(r["rw"] - r["rl"]) <= 2 and max(r["rw"], r["rl"]) >= 13]
    stomp = [r for r in rows if abs(r["rw"] - r["rl"]) >= 7]
    out["game_types"] = {
        "close": {"games": len(close), "winrate": wr(close)},
        "stomp_won": sum(1 for r in stomp if res(r) == "win"), "stomp_lost": sum(1 for r in stomp if res(r) == "loss"),
        "overtime": {"games": len(ot), "wins": sum(1 for r in ot if res(r) == "win"), "winrate": wr(ot)},
        "flawless_13_0": sum(1 for r in rows if r["rw"] == 13 and r["rl"] == 0),
        "avg_rounds": round(sum(r["rw"] + r["rl"] for r in rows) / len(rows), 1),
    }
    # Dégâts
    dd = [r for r in rows if r.get("dmg") is not None and r.get("dmg_rcv") is not None]
    rounds = sum(r["rw"] + r["rl"] for r in dd) or 1
    out["damage"] = {"ddelta": round((sum(r["dmg"] for r in dd) - sum(r["dmg_rcv"] for r in dd)) / rounds) if dd else None,
                     "dealt": round(sum(r["dmg"] for r in dd) / rounds) if dd else None,
                     "received": round(sum(r["dmg_rcv"] for r in dd) / rounds) if dd else None, "games": len(dd)}
    # Détail (matchs vus en entier)
    det = [r for r in rows if isinstance(r.get("detail"), dict)]
    out["detail_games"] = len(det)
    if not det:
        return out
    D = [r["detail"] for r in det]
    rnd = sum(d.get("rounds") or 0 for d in D) or 1
    clutch: dict = {}
    multis = {"3K": 0, "4K": 0, "ACE": 0}
    weapons: dict = {}
    sides = {"atk": [0, 0], "def": [0, 0]}
    pistol = [0, 0]
    eco = {"eco": [0, 0, 0], "force": [0, 0, 0], "full": [0, 0, 0]}
    for d in D:
        for k, v in (d.get("clutch") or {}).items():
            clutch[k] = clutch.get(k, 0) + v
        for k in multis:
            multis[k] += (d.get("multis") or {}).get(k, 0)
        for k, v in (d.get("weapons") or {}).items():
            weapons[k] = weapons.get(k, 0) + v
        for sd in ("atk", "def"):
            x = (d.get("sides") or {}).get(sd)
            if x:
                sides[sd][0] += x[0]
                sides[sd][1] += x[1]
        if d.get("pistol"):
            pistol[0] += d["pistol"][0]
            pistol[1] += d["pistol"][1]
        for t in eco:
            x = (d.get("eco") or {}).get(t)
            if x:
                for j in range(3):
                    eco[t][j] += x[j]
    fb, fd = sum(d.get("fb", 0) for d in D), sum(d.get("fd", 0) for d in D)
    total_k = sum(weapons.values()) or 1
    pct = lambda a, b: round(100 * a / b) if b else None
    out["duels"] = {"fb": fb, "fd": fd, "fb_per_game": round(fb / len(D), 1), "fd_per_game": round(fd / len(D), 1),
                    "fb_ratio": round(fb / max(1, fd), 2), "clutch": clutch, "clutches": sum(clutch.values()),
                    "multis": multis}
    out["kast"] = pct(sum(d.get("kast", 0) for d in D), rnd)
    out["sides"] = {"atk": pct(*sides["atk"]), "def": pct(*sides["def"]), "atk_rounds": sides["atk"][1],
                    "def_rounds": sides["def"][1], "pistol": pct(*pistol), "pistol_rounds": pistol[1]}
    out["weapons"] = [{"name": k, "kills": v, "share": round(100 * v / total_k)}
                      for k, v in sorted(weapons.items(), key=lambda x: -x[1])[:5]]
    out["economy"] = {t: {"rounds": eco[t][1], "winrate": pct(eco[t][0], eco[t][1]),
                          "kpr": round(eco[t][2] / eco[t][1], 2) if eco[t][1] else None} for t in eco}
    out["spike"] = {"plants": sum(d.get("plants", 0) for d in D), "defuses": sum(d.get("defuses", 0) for d in D),
                    "plants_per_game": round(sum(d.get("plants", 0) for d in D) / len(D), 2)}
    solo = [r for r in det if not r["detail"].get("mates")]
    group = [r for r in det if r["detail"].get("mates")]
    mates: dict = {}
    for r in group:
        for nm in r["detail"]["mates"]:
            mates.setdefault(nm, []).append(r)
    out["party"] = {"solo": {"games": len(solo), "winrate": wr(solo), "kd": kd(solo)},
                    "group": {"games": len(group), "winrate": wr(group), "kd": kd(group)},
                    "mates": sorted([{"name": k, "games": len(v), "winrate": wr(v)} for k, v in mates.items()],
                                    key=lambda x: -x["games"])[:5]}
    return out


def build_stats(rows: list[dict], act: dict | None = None, roles: dict | None = None) -> dict:
    """Les 4 vues de l'écran Stats : tout l'historique / saison en cours, tous modes / compétition.

    Saison en cours : l'acte en cours d'après valorant-api.com (act = {"id", "start", "label"}),
    sinon la saison du match le plus récent.
    """
    label = ""
    if act and act.get("id") and any(r.get("season_id") == act["id"] for r in rows):
        cur = [r for r in rows if r.get("season_id") == act["id"]]
        season, label = act["id"], act.get("label") or ""
    elif act and act.get("start"):
        cur = [r for r in rows if (r["at"] or "") >= act["start"]]
        season, label = act.get("id") or "", act.get("label") or ""
    else:
        season = rows[-1]["season"] if rows else ""      # saison du match le plus récent
        cur = [r for r in rows if season and r["season"] == season]
        if cur and cur[-1].get("season") and not label:
            label = cur[-1]["season"]
    if label == season or (season and len(season) > 20 and not label):
        label = ""                                      # pas d'uuid brut à l'écran
    short = next((r["season"] for r in reversed(cur) if r["season"] and len(r["season"]) <= 12), "")
    comp = lambda rs: [r for r in rs if r["mode"] == "competitive"]
    return {"v": STATS_VERSION, "season": short, "season_label": label,
            "all": aggregate_stats(rows, roles=roles), "competitive": aggregate_stats(comp(rows), roles=roles),
            "season_all": aggregate_stats(cur, roles=roles), "season_competitive": aggregate_stats(comp(cur), roles=roles)}


# ── Écran de sélection d'agent : ton bilan sur la map de la partie qui commence ──

MAP_TIPS = {        # repères généraux, volontairement simples (les maps récentes n'en ont pas)
    "Ascent": "Le mid se dispute beaucoup, et les portes de A et B se ferment avec un bouton.",
    "Bind": "Pas de mid : deux téléporteurs relient le côté A et le côté B.",
    "Haven": "Trois sites (A, B, C) : la défense est étalée, les rotations comptent.",
    "Split": "Des cordes pour monter partout : la hauteur et le contrôle du mid comptent.",
    "Icebox": "Des tyroliennes et beaucoup d'angles en hauteur : vérifie au-dessus de toi.",
    "Breeze": "Grande map ouverte avec de longues lignes de tir : l'Operator y est très fort.",
    "Fracture": "L'attaque peut arriver des deux côtés de la map en même temps.",
    "Pearl": "Pas de mécanique spéciale : un long mid et deux sites classiques.",
    "Lotus": "Trois sites (A, B, C) et des portes tournantes à surveiller.",
    "Sunset": "Trois couloirs classiques, avec un mid qui donne sur les deux sites.",
    "Abyss": "Pas de murs au bord de la map : on peut tomber dans le vide.",
}


def _local_date(iso: str):
    try:
        return _dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone().date()
    except (ValueError, TypeError):
        return None


def map_report(rows: list[dict], map_name: str, roles: dict | None = None, competitive: bool = False,
               today=None) -> dict | None:
    """Ton bilan sur une map, tes agents dessus, ta forme du moment : pour l'écran de sélection d'agent.

    competitive=True : seulement les matchs classés (repli sur tous les modes s'il y en a moins de 3 sur la map).
    """
    if not map_name:
        return None
    roles = roles or {}

    def res(r):
        return "V" if r["rw"] > r["rl"] else "D" if r["rw"] < r["rl"] else "N"

    def summ(rs):
        g = len(rs)
        w = sum(1 for r in rs if res(r) == "V")
        rounds = sum(r["rw"] + r["rl"] for r in rs) or 1
        return {"games": g, "wins": w, "losses": sum(1 for r in rs if res(r) == "D"),
                "winrate": round(100 * w / g) if g else None,
                "kd": round(sum(r["k"] for r in rs) / max(1, sum(r["d"] for r in rs)), 2) if g else None,
                "acs": round(sum(r["score"] for r in rs) / rounds) if g else None}

    def by_agent(rs):
        out: dict = {}
        for r in rs:
            if r["agent"] and r["agent"] != "Unknown":
                out.setdefault(r["agent"], []).append(r)
        lst = [{"name": a, "role": roles.get(a, ""), **summ(x)} for a, x in out.items()]
        for a in lst:                                   # score prudent : 1 victoire sur 1 ne passe pas devant 6 sur 8
            a["score"] = round((a["wins"] + 2) / (a["games"] + 4), 3)
        lst.sort(key=lambda a: (-a["score"], -a["games"]))
        return lst

    base = [r for r in rows if r["mode"] == "competitive"] if competitive else list(rows)
    scope = "Compétition" if competitive else "Tous les modes"
    on = [r for r in base if r["map"] == map_name]
    if competitive and len(on) < 3:
        base, scope = list(rows), "Tous les modes"
        on = [r for r in base if r["map"] == map_name]
    out = {"map": map_name, "scope": scope, "tip": MAP_TIPS.get(map_name, ""),
           "overall": summ(base), **summ(on), "rounds_diff": sum(r["rw"] - r["rl"] for r in on)}
    # Place de cette map parmi les tiennes (3 matchs minimum)
    maps: dict = {}
    for r in base:
        if r["map"]:
            maps.setdefault(r["map"], []).append(r)
    ranked = sorted(((m, summ(x)) for m, x in maps.items() if len(x) >= 3), key=lambda t: (-t[1]["winrate"], -t[1]["games"]))
    names = [m for m, _ in ranked]
    out["map_rank"] = names.index(map_name) + 1 if map_name in names else None
    out["maps_ranked"] = len(names)
    out["agents"] = by_agent(on)                         # tes agents sur cette map
    out["agents_all"] = by_agent(base)                   # tes agents partout (pour les rôles manquants)
    role_on: dict = {}
    for r in on:
        if roles.get(r["agent"]):
            role_on.setdefault(roles[r["agent"]], []).append(r)
    out["roles"] = sorted(({"role": k, **summ(v)} for k, v in role_on.items()), key=lambda x: -x["games"])
    out["recent"] = [{"res": res(r), "score": f"{r['rw']}-{r['rl']}", "agent": r["agent"], "at": r["at"]}
                     for r in sorted(on, key=lambda r: r["at"] or "")[-5:]]
    # Côtés sur cette map (matchs vus en entier)
    sides = {"atk": [0, 0], "def": [0, 0]}
    for r in on:
        d = r.get("detail") if isinstance(r.get("detail"), dict) else None
        for sd in ("atk", "def"):
            x = ((d or {}).get("sides") or {}).get(sd)
            if x:
                sides[sd][0] += x[0]
                sides[sd][1] += x[1]
    pct = lambda a, b: round(100 * a / b) if b else None
    out["sides"] = {"atk": pct(*sides["atk"]), "def": pct(*sides["def"]),
                    "atk_rounds": sides["atk"][1], "def_rounds": sides["def"][1]}
    # Ta forme du moment (tous modes confondus, du plus ancien au plus récent)
    seq = sorted(rows, key=lambda r: r["at"] or "")
    last10 = [res(r) for r in seq[-10:]]
    streak, kind = 0, (last10[-1] if last10 else "")
    for x in reversed(last10):
        if x != kind:
            break
        streak += 1
    today = today or _dt.date.today()
    tod = [r for r in seq if _local_date(r["at"]) == today]
    out["form"] = {"last": last10, "streak": streak, "streak_kind": kind,
                   "today": {"games": len(tod), "wins": sum(1 for r in tod if res(r) == "V"),
                             "losses": sum(1 for r in tod if res(r) == "D")}}
    return out


_select_cache = {"key": None, "data": None}


def _select_report(live: dict) -> dict | None:
    """Bilan de la map pendant la sélection d'agent et la partie (recalculé seulement si la map ou l'historique change)."""
    if live.get("state") not in ("PREGAME", "INGAME") or not live.get("map"):
        return None
    comp = bool(live.get("ranked")) or live.get("queue_id") == "competitive"
    hist = _hist_rows()
    key = (live["map"], comp, len(_stored_rows), len(hist), _dt.date.today())
    if _select_cache["key"] != key:
        try:
            _load_agents()
        except Exception:
            pass
        rep = map_report(merge_rows(_stored_rows, hist), live["map"], _roles, comp)
        if rep is not None:
            rep["role_of"] = dict(_roles)                # nom d'agent → rôle, pour la compo de ton équipe
        _select_cache.update(key=key, data=rep)
    return copy.deepcopy(_select_cache["data"])


_acts_cache = {"at": 0.0, "data": None}


def _current_act() -> dict | None:
    """Acte en cours d'après valorant-api.com/v1/seasons (relu une fois par jour)."""
    if time.time() - _acts_cache["at"] > 86400:
        try:
            req = urllib.request.Request("https://valorant-api.com/v1/seasons", headers={"User-Agent": "BoxScreen/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                _acts_cache["data"] = json.loads(r.read().decode("utf-8")).get("data") or []
            _acts_cache["at"] = time.time()
        except Exception as e:
            _acts_cache["at"] = time.time() - 86400 + 600
            print(f"[VALORANT] Liste des saisons indisponible : {e}")
    return pick_current_act(_acts_cache["data"] or [], _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


def pick_current_act(seasons: list[dict], now_iso: str) -> dict | None:
    """Liste valorant-api /v1/seasons → {"id", "start", "label"} de l'acte en cours."""
    by_id = {str(x.get("uuid") or "").lower(): x for x in seasons if isinstance(x, dict)}
    for x in seasons:
        if not isinstance(x, dict) or "act" not in str(x.get("type") or "").lower():
            continue
        start, end = str(x.get("startTime") or ""), str(x.get("endTime") or "")
        if start[:19] <= now_iso[:19] <= (end[:19] or "9999"):
            parent = by_id.get(str(x.get("parentUuid") or "").lower()) or {}
            title = lambda t: " ".join(w.capitalize() if not any(c.isdigit() for c in w) and w.upper() not in ("I", "II", "III", "IV", "V", "VI") else w for w in str(t or "").split())
            label = " · ".join(t for t in (title(parent.get("displayName")), title(x.get("displayName"))) if t)
            return {"id": str(x.get("uuid") or "").lower(), "start": start, "label": label}
    return None


def _first_recap(payload, puuid: str) -> dict | None:
    for m in (payload or {}).get("data") or []:
        try:
            r = parse_match_recap(m, puuid)
        except Exception as e:
            print(f"[VALORANT] Récap du match illisible : {e}")
            r = None
        if r:
            return r
    return None


def _henrik_refresh() -> None:
    global _stored_rows, _stored_loaded, _backfill_running
    with _lock:
        acc = dict(_state["account"])
        cfg = dict(_cfg)
    affinity = (cfg.get("affinity") or acc.get("region") or "eu").lower()
    platform = (cfg.get("platform") or "pc").lower()
    puuid = acc.get("puuid")

    if not puuid:
        name = cfg.get("name") or acc.get("name")
        tag = cfg.get("tag") or acc.get("tag")
        if not (name and tag):
            raise _HenrikError("Riot ID inconnu : lance Valorant une fois ou remplis name/tag dans valorant_config.json")
        res = _henrik_get("/valorant/v2/account/{}/{}".format(
            urllib.parse.quote(name, safe=""), urllib.parse.quote(tag, safe="")))
        d = res.get("data") or {}
        puuid = d.get("puuid")
        if not puuid:
            raise _HenrikError("Réponse compte HenrikDev sans puuid")
        with _lock:
            _state["account"].update({
                "puuid": puuid, "name": d.get("name") or name, "tag": d.get("tag") or tag,
                "region": d.get("region") or "", "level": d.get("account_level") or acc.get("level"),
            })
        if not cfg.get("affinity") and d.get("region"):
            affinity = d["region"].lower()

    mmr = _henrik_get(f"/valorant/v3/by-puuid/mmr/{affinity}/{platform}/{puuid}")
    rank = parse_mmr(mmr)
    acc_from_mmr = (mmr.get("data") or {}).get("account") or {}

    matches_payload = _henrik_get(f"/valorant/v4/by-puuid/matches/{affinity}/{platform}/{puuid}",
                                  {"size": 5})
    matches = parse_matches(matches_payload, puuid)
    recap = _first_recap(matches_payload, puuid)

    # Option « compétition uniquement » du dashboard : les 5 derniers matchs classés, à part
    matches_comp, recap_comp, comp_payload = None, None, None
    try:
        comp_payload = _henrik_get(f"/valorant/v4/by-puuid/matches/{affinity}/{platform}/{puuid}",
                                   {"size": 5, "mode": "competitive"})
        matches_comp = parse_matches(comp_payload, puuid)
        recap_comp = _first_recap(comp_payload, puuid)
    except Exception as e:
        print(f"[VALORANT] Matchs compétitifs indisponibles : {e}")

    with _lock:
        _state["rank"] = rank
        _state["matches"] = matches
        if recap:
            _state["recap"] = recap
        if matches_comp is not None:
            _state["matches_comp"] = matches_comp
        if recap_comp:
            _state["recap_comp"] = recap_comp
        if acc_from_mmr.get("name") and not _state["account"].get("name"):
            _state["account"]["name"] = acc_from_mmr["name"]
            _state["account"]["tag"] = acc_from_mmr.get("tag", "")
    print(f"[VALORANT] HenrikDev OK : {rank['tier']} {rank.get('rr')} RR, {len(matches)} matchs")

    # Historique local : chaque match complet vu est gardé, même si HenrikDev ne le stocke pas
    seen = [row_from_v4(m, puuid) for m in (matches_payload or {}).get("data") or []]
    if matches_comp is not None:
        seen += [row_from_v4(m, puuid) for m in (comp_payload or {}).get("data") or []]
    if _hist_add([r for r in seen if r], puuid):
        _hist_save()
    with _hist_lock:
        need_backfill = time.time() - (_history.get("backfill_at") or 0) > 86400
    if need_backfill and not _backfill_running:
        _backfill_running = True
        threading.Thread(target=_backfill, args=(affinity, platform, puuid), daemon=True, name="valorant_backfill").start()
        print("[VALORANT] Rattrapage de l'historique lancé en arrière-plan")

    # Statistiques sur tout l'historique : toutes les 30 min, ou dès qu'un nouveau match apparaît
    newest = matches[0].get("match_id") if matches else None
    with _lock:
        st = _state.get("stats") or {}
        due = (not _stored_loaded or st.get("v") != STATS_VERSION
               or time.time() - (st.get("updated") or 0) > 1800 or (newest and newest != st.get("newest")))
    if due:
        try:
            _stored_rows = parse_stored(_henrik_get(f"/valorant/v1/by-puuid/stored-matches/{affinity}/{puuid}"), puuid)
            _stored_loaded = True
            _recompute_stats(newest)
            with _lock:
                src = (_state.get("stats") or {}).get("sources") or {}
            print(f"[VALORANT] Stats : {src.get('total')} matchs (HenrikDev {src.get('henrik_stored')}, historique local {src.get('local')})")
        except Exception as e:
            print(f"[VALORANT] Stats indisponibles : {e}")


def _maybe_henrik(running: bool) -> None:
    if not _api_key():
        with _lock:
            _state["henrik"].update({"ok": False, "error": "Pas de clé HenrikDev dans valorant_config.json"})
        return
    with _lock:
        due = time.time() >= _state["henrik"]["next"]
    if not due:
        return
    try:
        _henrik_refresh()
        with _lock:
            _state["henrik"].update({"ok": True, "error": None, "updated": time.time(),
                                     "next": time.time() + (180 if running else 600)})
        _save_cache()
    except _HenrikError as e:
        delay = e.retry_after if e.retry_after else 300
        with _lock:
            _state["henrik"].update({"ok": False, "error": str(e), "next": time.time() + delay})
        print(f"[VALORANT] {e}")
    except Exception as e:
        with _lock:
            _state["henrik"].update({"ok": False, "error": f"Erreur inattendue : {e}",
                                     "next": time.time() + 300})
        print(f"[VALORANT] Erreur HenrikDev : {e}")


# ── Boucle ──────────────────────────────────────────────────────────────────

_pending_refresh: list[float] = []


def _loop() -> None:
    prev_loop: str | None = None
    prev_running = False
    while not _stop.is_set():
        _load_config()
        enabled = bool(_cfg.get("enabled", True))
        loop_state = None
        if enabled:
            try:
                loop_state = _poll_local()
            except Exception as e:
                print(f"[VALORANT] Client local : {e}")
            running = loop_state is not None
            now = time.time()
            with _lock:
                if running and not prev_running:
                    _henrik_reschedule(5)          # jeu lancé : rang frais
                if prev_loop == "INGAME" and loop_state != "INGAME":
                    # Fin de partie : HenrikDev met un peu de temps à avoir le match
                    _henrik_reschedule(45)
                    _pending_refresh.append(now + 150)
                while _pending_refresh and _pending_refresh[0] <= now:
                    _pending_refresh.pop(0)
                    _henrik_reschedule(0)
            _maybe_henrik(running)
            prev_loop, prev_running = loop_state, running
        else:
            _set_live_off()
            prev_loop, prev_running = None, False
        _wake.clear()
        _wake.wait(2.0 if prev_running else 5.0)


def start() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _load_config()
    _load_cache()
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="valorant_source")
    _thread.start()
    print("[VALORANT] Source démarrée")


def stop() -> None:
    _stop.set()
    _wake.set()


def refresh_now() -> None:
    with _lock:
        _state["henrik"]["next"] = 0.0
        if isinstance(_state.get("stats"), dict):
            _state["stats"]["updated"] = 0.0          # « Actualiser » recalcule aussi les stats
    _wake.set()


def set_panel(mode: str) -> bool:
    """Change la place de Valorant dans la cascade du panel et l'enregistre."""
    global _cfg_mtime
    if mode not in PANEL_MODES:
        return False
    with _lock:
        _cfg["panel"] = mode
        cfg = dict(_cfg)
    try:
        current = {}
        if CONFIG_PATH.exists():
            current = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        current.update({"panel": mode})
        for k, v in cfg.items():
            current.setdefault(k, v)
        _atomic_write_json(CONFIG_PATH, current)
        _cfg_mtime = CONFIG_PATH.stat().st_mtime
    except Exception as e:
        print(f"[VALORANT] Réglage panel non enregistré : {e}")
    return True


def _live_is_fresh(live: dict) -> bool:
    return bool(live.get("running")) and time.time() - float(live.get("updated") or 0) < _LIVE_STALE_S


def panel_mode() -> str | None:
    """Ce que le panel peut afficher : 'live' (sélection d'agent / partie), 'menu' (jeu ouvert), None."""
    if _thread is None:
        start()
    with _lock:
        if not _cfg.get("enabled", True) or _cfg.get("panel") == "off":
            return None
        live = _state["live"]
        if not _live_is_fresh(live):
            return None
        return "live" if live.get("state") in ("PREGAME", "INGAME") else "menu"


def panel_priority() -> str:
    with _lock:
        p = _cfg.get("panel", "after_lyrics")
    return p if p in PANEL_MODES else "after_lyrics"


def snapshot() -> dict:
    if _thread is None:
        start()
    with _lock:
        snap = copy.deepcopy(_state)
        snap["enabled"] = bool(_cfg.get("enabled", True))
        snap["panel"] = _cfg.get("panel", "after_lyrics")
        snap["configured"] = bool(_api_key())
    snap["live"]["running"] = _live_is_fresh(snap["live"])
    if not snap["live"]["running"]:
        snap["live"]["state"] = None
    try:
        snap["select"] = _select_report(snap["live"]) if snap["live"]["running"] else None
    except Exception as e:
        snap["select"] = None
        _note(f"bilan de map indisponible ({e})")
    # Sans HenrikDev, on garde au moins le rang vu dans la présence locale (sans RR)
    if snap["rank"] is None and snap["live"].get("tier_id"):
        name, color = tier_info(snap["live"]["tier_id"])
        snap["rank"] = {"tier_id": snap["live"]["tier_id"], "tier": name, "color": color,
                        "rr": None, "last_change": None, "source": "local"}
    return snap
