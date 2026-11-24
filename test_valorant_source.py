"""Tests du parsing Valorant et de la cascade du panel.

Lancer : python test_valorant_source.py   (aucun réseau, aucun jeu nécessaire)
"""
import base64
import json
import sys
import types

import valorant_source as vs


def _b64(d: dict) -> str:
    return base64.b64encode(json.dumps(d).encode()).decode().rstrip("=")


def test_tiers():
    assert vs.tier_info(0)[0] == "Non classé"
    assert vs.tier_info(3)[0] == "Fer 1"
    assert vs.tier_info(14)[0] == "Or 3"
    assert vs.tier_info(21)[0] == "Ascendant 1"
    assert vs.tier_info(26)[0] == "Immortel 3"
    assert vs.tier_info(27)[0] == "Radiant"
    assert vs.tier_info(None)[0] == "Non classé"


def test_maps_and_queues():
    assert vs.map_name("/Game/Maps/Triad/Triad") == "Haven"
    assert vs.map_name("/Game/Maps/Bonsai/Bonsai") == "Split"
    assert vs.map_name("/Game/Maps/HURM/HURM_Alley/HURM_Alley") == "District"
    assert vs.map_name("/Game/Maps/NewMap/NewMap") == "NewMap"
    assert vs.map_name("Ascent") == "Ascent"
    assert vs.map_name("") == ""
    assert vs.queue_name("competitive") == "Compétition"
    assert vs.queue_name("", "Custom") == "Custom"


def test_presence_old_flat_format():
    private = _b64({
        "sessionLoopState": "INGAME", "matchMap": "/Game/Maps/Ascent/Ascent",
        "queueId": "competitive", "provisioningFlow": "Matchmaking",
        "partyOwnerMatchScoreAllyTeam": 7, "partyOwnerMatchScoreEnemyTeam": 5,
        "partySize": 2, "competitiveTier": 14, "accountLevel": 152,
    })
    info = vs.parse_presence(vs.decode_presence_private(private))
    assert info["state"] == "INGAME"
    assert info["map"] == "Ascent"
    assert info["mode"] == "Compétition"
    assert (info["score_ally"], info["score_enemy"]) == (7, 5)
    assert info["tier_id"] == 14 and info["level"] == 152


def test_presence_nested_format():
    private = _b64({
        "isValid": True,
        "matchPresenceData": {"sessionLoopState": "PREGAME", "matchMap": "/Game/Maps/Jam/Jam",
                              "queueId": "swiftplay", "provisioningFlow": "Matchmaking"},
        "partyPresenceData": {"partySize": 1, "partyOwnerMatchScoreAllyTeam": 0,
                              "partyOwnerMatchScoreEnemyTeam": 0},
        "playerPresenceData": {"competitiveTier": 9, "accountLevel": 40},
    })
    info = vs.parse_presence(vs.decode_presence_private(private))
    assert info["state"] == "PREGAME"
    assert info["map"] == "Lotus"
    assert info["mode"] == "Swiftplay"
    assert info["score_ally"] is None  # pas de score en sélection d'agent
    assert info["tier_id"] == 9


def test_presence_menus_and_garbage():
    info = vs.parse_presence(vs.decode_presence_private(_b64({"sessionLoopState": "MENUS", "queueId": ""})))
    assert info["state"] == "MENUS" and info["map"] == ""
    assert vs.decode_presence_private("pas du base64 !!") == {}
    assert vs.decode_presence_private(None) == {}


def test_mmr():
    rank = vs.parse_mmr({"status": 200, "data": {
        "account": {"name": "Hedji", "tag": "ST93", "puuid": "p1"},
        "current": {"tier": {"id": 14, "name": "Gold 3"}, "rr": 67, "last_change": 18, "elo": 1167},
        "peak": {"season": {"id": "x", "short": "e9a3"}, "tier": {"id": 15, "name": "Platinum 1"}, "rr": 12},
    }})
    assert rank["tier"] == "Or 3" and rank["rr"] == 67 and rank["last_change"] == 18
    assert rank["peak_tier"] == "Platine 1" and rank["peak_season"] == "e9a3"
    empty = vs.parse_mmr({"data": {"current": {}, "peak": None}})
    assert empty["tier"] == "Non classé" and empty["peak_tier"] == ""


def test_matches():
    payload = {"data": [
        {"metadata": {"match_id": "m1", "map": {"name": "Ascent"}, "queue": {"id": "competitive", "name": "Competitive"}},
         "players": [
             {"puuid": "p1", "team_id": "Red", "agent": {"name": "Jett"},
              "stats": {"kills": 21, "deaths": 12, "assists": 4, "headshots": 12, "bodyshots": 30, "legshots": 8}},
             {"puuid": "p2", "team_id": "Blue", "agent": {"name": "Sage"}, "stats": {}}],
         "teams": [{"team_id": "Red", "rounds": {"won": 13, "lost": 9}, "won": True},
                   {"team_id": "Blue", "rounds": {"won": 9, "lost": 13}, "won": False}]},
        {"metadata": {"match_id": "m2", "map": {"name": "Bind"}, "queue": {"id": "deathmatch"}},
         "players": [{"puuid": "p1", "team_id": "p1", "agent": {"name": "Reyna"}, "stats": {"kills": 40}}],
         "teams": []},
        {"metadata": {"match_id": "m3"}, "players": [{"puuid": "autre"}], "teams": []},
    ]}
    ms = vs.parse_matches(payload, "p1")
    assert len(ms) == 2
    assert ms[0]["result"] == "win" and ms[0]["rounds_won"] == 13 and ms[0]["agent"] == "Jett"
    assert ms[0]["hs_pct"] == 24 and ms[0]["mode"] == "Compétition"
    assert ms[1]["result"] is None and ms[1]["mode"] == "Deathmatch"


def test_cascade():
    """Ordre : paroles → Valorant → viz → ascii, ou Valorant d'abord en mode prioritaire."""
    lyr = types.SimpleNamespace(state={"lines": [(0, "la")]})
    bpm = types.SimpleNamespace(level=0.5)
    sys.modules["lyrics_source"] = types.SimpleNamespace(get_state=lambda: lyr.state)
    sys.modules["bpm_source"] = types.SimpleNamespace(get_level=lambda: bpm.level)
    import render
    orig = (vs.panel_mode, vs.panel_priority)
    try:
        v = {"mode": "menu", "prio": "after_lyrics"}
        vs.panel_mode = lambda: v["mode"]
        vs.panel_priority = lambda: v["prio"]
        render.set_force_viz(False)
        assert render._get_lyrics_cascade_mode() == "lyrics"
        lyr.state = {"lines": []}
        assert render._get_lyrics_cascade_mode() == "valorant"
        lyr.state = None
        assert render._get_lyrics_cascade_mode() == "valorant"
        render.set_force_viz(True)
        assert render._get_lyrics_cascade_mode() == "audio"
        render.set_force_viz(False)
        v["mode"] = None
        assert render._get_lyrics_cascade_mode() == "audio"
        v["mode"], v["prio"] = "live", "priority"
        lyr.state = {"lines": [(0, "la")]}
        assert render._get_lyrics_cascade_mode() == "valorant"
    finally:
        vs.panel_mode, vs.panel_priority = orig
        render.set_force_viz(False)


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"OK    {name}")
            except Exception as e:
                fails += 1
                print(f"ECHEC {name} : {e!r}")
    sys.exit(1 if fails else 0)
