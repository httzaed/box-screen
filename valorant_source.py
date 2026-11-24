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
"""

from __future__ import annotations

import base64
import copy
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
             "tier_id": None, "level": None, "updated": 0.0},
    "rank": None,
    "matches": [],
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
        _state["henrik"]["updated"] = float(data.get("updated") or 0)


def _save_cache() -> None:
    with _lock:
        data = {
            "account": _state["account"],
            "rank": _state["rank"],
            "matches": _state["matches"],
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
                     "score_ally": None, "score_enemy": None, "party_size": None})
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
    with _lock:
        _state["live"].update(info)
        _state["live"]["running"] = True
        _state["live"]["updated"] = time.time()
        if info.get("level"):
            _state["account"]["level"] = info["level"]
    return info.get("state")


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


def _henrik_refresh() -> None:
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

    with _lock:
        _state["rank"] = rank
        _state["matches"] = matches
        if acc_from_mmr.get("name") and not _state["account"].get("name"):
            _state["account"]["name"] = acc_from_mmr["name"]
            _state["account"]["tag"] = acc_from_mmr.get("tag", "")
    print(f"[VALORANT] HenrikDev OK : {rank['tier']} {rank.get('rr')} RR, {len(matches)} matchs")


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
    # Sans HenrikDev, on garde au moins le rang vu dans la présence locale (sans RR)
    if snap["rank"] is None and snap["live"].get("tier_id"):
        name, color = tier_info(snap["live"]["tier_id"])
        snap["rank"] = {"tier_id": snap["live"]["tier_id"], "tier": name, "color": color,
                        "rr": None, "last_change": None, "source": "local"}
    return snap
