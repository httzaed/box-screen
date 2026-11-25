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


def test_agent_slug():
    assert vs.agent_slug("Reyna") == "reyna"
    assert vs.agent_slug("KAY/O") == "kay-o"
    assert vs.agent_slug("Hush") == "hush"
    assert vs.agent_slug("") == ""


def test_sessions_region():
    payload = {"abc": {"productId": "valorant", "launchConfiguration": {"arguments": [
        "-ares-deployment=eu", "-config-endpoint=https://shared.eu.a.pvp.net"]}}}
    assert vs.parse_sessions_region(payload) == ("eu", "eu")
    payload["abc"]["launchConfiguration"]["arguments"] = ["-ares-deployment=br"]
    assert vs.parse_sessions_region(payload) == ("br", "na")
    assert vs.parse_sessions_region({"x": {"productId": "riot_client"}}) is None
    assert vs.parse_sessions_region(None) is None


def test_pregame_and_coregame_agent():
    me, other = "me-puuid", "other"
    pre = {"AllyTeam": {"Players": [
        {"Subject": other, "CharacterID": "AAA", "CharacterSelectionState": "locked"},
        {"Subject": me, "CharacterID": "A3BFB853-43B2", "CharacterSelectionState": "selected"}]}}
    assert vs.parse_pregame_match(pre, me) == ("a3bfb853-43b2", "selected")
    pre["AllyTeam"]["Players"][1].update({"CharacterID": "", "CharacterSelectionState": ""})
    assert vs.parse_pregame_match(pre, me) == ("", "")
    assert vs.parse_pregame_match({"AllyTeam": {"Players": []}}, me) is None
    core = {"Players": [{"Subject": other, "CharacterID": "x"}, {"Subject": me, "CharacterID": "BBB"}]}
    assert vs.parse_coregame_match(core, me) == "bbb"
    assert vs.parse_coregame_match({}, me) == ""


def _recap_match():
    def pl(pu, team, agent, party, k, d, a, score, hs=5, bs=10, dmg=1500):
        return {"puuid": pu, "name": "Nom" + pu, "tag": "T" + pu, "team_id": team, "party_id": party,
                "agent": {"name": agent},
                "stats": {"kills": k, "deaths": d, "assists": a, "score": score, "headshots": hs,
                          "bodyshots": bs, "legshots": 0, "damage": {"dealt": dmg}}}
    players = [pl("me", "Red", "Reyna", "P1", 3, 1, 0, 900), pl("ami", "Red", "Sage", "P1", 0, 1, 2, 300),
               pl("x", "Red", "Omen", "P9", 0, 1, 0, 200),
               pl("e1", "Blue", "Jett", "P5", 1, 1, 0, 400), pl("e2", "Blue", "Sova", "P5", 0, 1, 0, 100),
               pl("e3", "Blue", "Neon", "P6", 1, 1, 0, 300)]
    def k(rnd, t, killer, victim, weapon="Vandal"):
        return {"round": rnd, "time_in_round_in_ms": t, "killer": {"puuid": killer},
                "victim": {"puuid": victim}, "weapon": {"name": weapon}}
    kills = [
        # manche 0 : ami et x meurent, tu restes seul contre 3 et tu fais un 3K → clutch 1v3
        k(0, 1000, "e1", "ami"), k(0, 2000, "e3", "x"), k(0, 3000, "me", "e1"),
        k(0, 4000, "me", "e2", "Sheriff"), k(0, 5000, "me", "e3"),
        # manche 1 : perdue
        k(1, 1500, "e1", "me"),
    ]
    rounds = [
        {"id": 0, "winning_team": "Red", "result": "Elimination", "ceremony": "CeremonyClutch",
         "stats": [{"player": {"puuid": "me"}, "economy": {"loadout_value": 3900, "weapon": {"name": "Vandal"}}},
                   {"player": {"puuid": "e1"}, "economy": {"loadout_value": 4700}}]},
        {"id": 1, "winning_team": "Blue", "result": "Bomb detonated", "ceremony": "",
         "stats": [{"player": {"puuid": "me"}, "economy": {"loadout_value": 1200}}]},
    ]
    return {"metadata": {"match_id": "m1", "map": {"name": "Ascent"}, "queue": {"id": "competitive"},
                         "game_length_in_ms": 1800000, "started_at": "2026-11-24T16:00:00Z"},
            "players": players, "kills": kills, "rounds": rounds,
            "teams": [{"team_id": "Red", "won": False, "rounds": {"won": 1, "lost": 1}},
                      {"team_id": "Blue", "won": False, "rounds": {"won": 1, "lost": 1}}]}


def test_match_recap():
    r = vs.parse_match_recap(_recap_match(), "me")
    assert r["result"] == "draw" and r["map"] == "Ascent" and r["mode"] == "Compétition"
    me = r["me"]
    assert me["agent"] == "Reyna" and me["kills"] == 3 and me["fb"] == 0 and me["fd"] == 1
    assert me["clutches"] == [{"round": 1, "vs": 3}]
    assert me["multis"]["3K"] == 1 and me["best_round"] == {"n": 1, "kills": 3}
    assert me["weapons"][0] == ("Vandal", 2)
    assert [x["kind"] for x in r["rounds"]] == ["elim", "spike"]
    assert r["rounds"][0]["ceremony"] == "Clutch" and r["rounds"][0]["eco_ally"] == 3900
    assert r["rounds"][0]["eco_enemy"] == 4700 and r["rounds"][0]["weapon"] == "Vandal"
    names = {p["agent"]: p["name"] for p in r["players"]}
    # Pseudos : toi et ton groupe seulement, jamais les autres (incognito respecté)
    assert names["Reyna"] == "Nomme" and names["Sage"] == "Nomami"
    assert names["Omen"] == "" and names["Jett"] == "" and names["Neon"] == ""
    assert r["players"][0]["team"] == "ally" and r["players"][-1]["team"] == "enemy"
    assert any(p.get("mvp_match") for p in r["players"])
    assert vs.parse_match_recap(_recap_match(), "inconnu") is None


def test_pregame_full_and_coregame_full():
    pre = {"AllyTeam": {"TeamID": "Red", "Players": [
        {"Subject": "a", "CharacterID": "X", "CharacterSelectionState": "locked",
         "PlayerIdentity": {"AccountLevel": 80, "HideAccountLevel": True}},
        {"Subject": "me", "CharacterID": "Y", "CharacterSelectionState": "selected",
         "PlayerIdentity": {"AccountLevel": 459}}]},
        "PhaseTimeRemainingNS": 42_000_000_000, "EnemyTeamLockCount": 3, "EnemyTeamSize": 5, "IsRanked": True}
    f = vs.parse_pregame_full(pre, "me", {"me"})
    assert f["side"] == "attack" and f["phase_left"] == 42.0 and f["enemy_locks"] == 3 and f["ranked"] is True
    assert f["team"][0]["self"] and f["team"][0]["level"] == 459 and f["team"][1]["level"] is None
    core = {"Players": [{"Subject": "me", "TeamID": "Blue", "CharacterID": "A", "PlayerIdentity": {"AccountLevel": 1}},
                        {"Subject": "e", "TeamID": "Red", "CharacterID": "B", "PlayerIdentity": {}},
                        {"Subject": "c", "TeamID": "Red", "CharacterID": "", "IsCoach": True}],
            "MatchmakingData": {"QueueID": "unrated", "IsRanked": False}}
    c = vs.parse_coregame_full(core, "me")
    assert c["side"] == "defense" and c["ranked"] is False and len(c["players"]) == 2
    assert c["players"][0]["team"] == "ally" and c["players"][1]["team"] == "enemy"


def test_party():
    def pres(pu, party, tier, name):
        return {"puuid": pu, "product": "valorant", "game_name": name, "game_tag": "EUW",
                "private": _b64({"partyPresenceData": {"partyId": party},
                                 "playerPresenceData": {"competitiveTier": tier, "accountLevel": 50}})}
    rows, ids = vs.parse_party([pres("x", "P1", 14, "Pote"), pres("me", "P1", 13, "Salt"),
                                pres("z", "P2", 20, "Inconnu")], "me")
    assert [r["name"] for r in rows] == ["Salt", "Pote"] and ids == {"me", "x"}
    assert rows[1]["tier"] == "Or 3"

def _stored(i, agent, mp, team, red, blue, k=15, d=12, mode="competitive", tier=13):
    return {"meta": {"id": f"m{i}", "map": {"name": mp}, "mode": mode, "started_at": f"2026-11-{10 + i:02d}T19:00:00Z",
                     "season": {"short": "e9a3"}},
            "stats": {"puuid": "me", "team": team, "character": {"name": agent}, "tier": tier,
                      "score": 3000, "kills": k, "deaths": d, "assists": 4,
                      "shots": {"head": 10, "body": 30, "leg": 0}, "damage": {"made": 2600}},
            "teams": {"red": red, "blue": blue}}


def test_stored_stats():
    data = [_stored(0, "Reyna", "Ascent", "Red", 13, 7), _stored(1, "Reyna", "Ascent", "Blue", 5, 13),
            _stored(2, "Reyna", "Bind", "Red", 13, 11), _stored(3, "Jett", "Bind", "Red", 9, 13),
            _stored(4, "Jett", "Ascent", "Blue", 13, 10, mode="unrated"), _stored(5, "Sage", "Lotus", "Red", 4, 13),
            {"meta": {}, "stats": {"puuid": "autre"}, "teams": {"red": 1, "blue": 2}}]
    rows = vs.parse_stored({"data": data}, "me")
    assert len(rows) == 6 and rows[0]["map"] == "Ascent" and rows[0]["rw"] == 13
    assert rows[1]["rw"] == 13 and rows[1]["rl"] == 5          # équipe bleue : 13 manches à 5
    s = vs.aggregate_stats(rows, min_games=2)
    assert s["games"] == 6 and s["wins"] == 3 and s["winrate"] == 50       # m4 : bleu 10 manches à 13, perdu
    assert s["mains"][0]["name"] == "Reyna" and s["mains"][0]["games"] == 3 and s["mains"][0]["winrate"] == 100
    assert [a["name"] for a in s["best_agents"]] == ["Reyna", "Jett"]
    assert s["best_maps"][0]["name"] == "Ascent" and s["best_maps"][0]["winrate"] == 67
    assert all(m["games"] >= 2 for m in s["best_maps"] + s["worst_maps"])
    assert not set(m["name"] for m in s["best_maps"]) & set(m["name"] for m in s["worst_maps"])
    assert s["records"]["win_streak"] == 3 and s["records"]["loss_streak"] == 3 and s["hs"] == 25 and s["acs"] > 0
    assert s["rank_history"] == [13, 13, 13, 13, 13]
    assert vs.aggregate_stats([]) == {"games": 0}


def test_build_stats_seasons():
    rows = vs.parse_stored({"data": [_stored(0, "Reyna", "Ascent", "Red", 13, 7), _stored(1, "Jett", "Bind", "Red", 13, 9, mode="unrated"),
                                     _stored(2, "Sage", "Lotus", "Red", 5, 13), _stored(3, "Sage", "Lotus", "Red", 13, 5)]}, "me")
    rows[0]["season"] = "e9a2"                          # le plus ancien appartient à la saison précédente
    b = vs.build_stats(rows)
    assert b["season"] == "e9a3"
    assert b["all"]["games"] == 4 and b["competitive"]["games"] == 3
    assert b["season_all"]["games"] == 3 and b["season_competitive"]["games"] == 2
    assert vs.build_stats([])["season"] == ""


def test_current_act_and_season_fallback():
    seasons = [{"uuid": "EP9", "displayName": "EPISODE 9", "type": None},
               {"uuid": "A2", "displayName": "ACT II", "type": "EAresSeasonType::Act", "parentUuid": "EP9",
                "startTime": "2026-08-01T00:00:00Z", "endTime": "2026-10-15T00:00:00Z"},
               {"uuid": "A3", "displayName": "ACT III", "type": "EAresSeasonType::Act", "parentUuid": "EP9",
                "startTime": "2026-10-15T00:00:00Z", "endTime": "2027-01-07T00:00:00Z"}]
    act = vs.pick_current_act(seasons, "2026-11-25T10:00:00Z")
    assert act == {"id": "a3", "start": "2026-10-15T00:00:00Z", "label": "Episode 9 · Act III"}
    assert vs.pick_current_act(seasons, "2030-01-01T00:00:00Z") is None
    # Saison absente des matchs : on prend les matchs depuis le début de l'acte
    data = [_stored(i, "Reyna", "Ascent", "Red", 13, 7) for i in range(4)]
    for m in data:
        m["meta"]["season"] = {}
    rows = vs.parse_stored({"data": data}, "me")
    b = vs.build_stats(rows, {"id": "a3", "start": "2026-11-12T00:00:00Z", "label": "Episode 9 · ACT III"})
    assert b["v"] == vs.STATS_VERSION and b["season_label"] == "Episode 9 · ACT III"
    assert b["season_all"]["games"] == 2 and b["all"]["games"] == 4
    # Saison donnée par son uuid dans les matchs : on filtre par uuid
    for m in data:
        m["meta"]["season"] = {"id": "A3"}
    data[0]["meta"]["season"] = {"id": "A2"}
    b = vs.build_stats(vs.parse_stored({"data": data}, "me"), {"id": "a3", "start": "2026-10-15T00:00:00Z", "label": "X"})
    assert b["season_all"]["games"] == 3
    # Sans valorant-api : saison du dernier match
    b = vs.build_stats(vs.parse_stored({"data": [_stored(0, "Sage", "Bind", "Red", 13, 2)]}, "me"))
    assert b["season"] == "e9a3" and b["season_all"]["games"] == 1


def _v4(i, mode="competitive", won=13, lost=7, season="A3"):
    return {"metadata": {"match_id": f"v{i}", "started_at": f"2026-11-{1 + i % 28:02d}T20:00:{i % 60:02d}Z",
                         "map": {"name": "Ascent"}, "queue": {"id": mode}, "season": {"id": season, "short": "e9a3"},
                         "is_completed": True},
            "players": [{"puuid": "me", "team_id": "Red", "agent": {"name": "Hush"}, "tier": {"id": 13},
                         "stats": {"kills": 20, "deaths": 15, "assists": 3, "score": 4200, "headshots": 10,
                                   "bodyshots": 30, "legshots": 2, "damage": {"dealt": 3000}}}],
            "teams": [{"team_id": "Red", "rounds": {"won": won, "lost": lost}}, {"team_id": "Blue", "rounds": {"won": lost, "lost": won}}]}


def test_row_from_v4_and_merge():
    r = vs.row_from_v4(_v4(1), "me")
    assert r["id"] == "v1" and r["mode"] == "competitive" and r["agent"] == "Hush" and r["rw"] == 13
    assert r["season_id"] == "a3" and r["dmg"] == 3000 and r["tier"] == 13
    assert vs.row_from_v4(_v4(1), "autre") is None
    stored = vs.parse_stored({"data": [_stored(0, "Reyna", "Bind", "Red", 13, 7)]}, "me")
    same = dict(stored[0], id="v1")                      # le même match vu des deux côtés
    rows = vs.merge_rows([same], [r], [None, {"id": None}])
    assert len(rows) == 1 and rows[0]["season_id"] == "a3"
    assert len(vs.merge_rows(stored, [r])) == 2


def test_backfill_with_fake_henrik(tmp_path=None):
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp())
    old_path, old_get, old_sleep, old_act = vs.HISTORY_PATH, vs._henrik_get, vs.time.sleep, vs._current_act
    calls = []

    def fake_get(path, params=None):
        calls.append(dict(params or {}))
        start = (params or {}).get("start", 0)
        n = 25 if (params or {}).get("mode") == "competitive" else 12
        return {"data": [_v4(i, (params or {}).get("mode") or "unrated") for i in range(start, min(n, start + 10))]}
    try:
        vs.HISTORY_PATH = d / "h.json"
        vs._henrik_get, vs._current_act = fake_get, (lambda: None)
        vs.time.sleep = lambda s: None
        vs._history.update({"puuid": None, "rows": {}, "backfill_at": 0.0})
        vs._hist_loaded = False
        vs._stored_rows = []
        vs._backfill("eu", "pc", "me")
        rows = vs._hist_rows()
        assert len(rows) == 25                         # 25 compétitifs ; les « tous modes » ont les mêmes ids
        assert [c.get("start") for c in calls][:3] == [0, 10, 20]
        saved = vs.json.loads((d / "h.json").read_text(encoding="utf-8"))
        assert saved["puuid"] == "me" and len(saved["rows"]) == 25 and saved["backfill_at"] > 0
        st = vs._state["stats"]
        assert st["sources"]["local"] == 25 and st["all"]["games"] == 25
    finally:
        vs.HISTORY_PATH, vs._henrik_get, vs.time.sleep, vs._current_act = old_path, old_get, old_sleep, old_act
        vs._history.update({"puuid": None, "rows": {}, "backfill_at": 0.0})
        vs._hist_loaded = False


def _detail_match():
    pl = lambda pu, team, party, name: {"puuid": pu, "team_id": team, "party_id": party, "name": name,
                                         "agent": {"name": "Hush"}, "stats": {}}
    players = [pl("me", "Red", "P", "Salt"), pl("ami", "Red", "P", "Pote"), pl("x", "Red", "Q", "Inconnu"),
               pl("e1", "Blue", "B", "A"), pl("e2", "Blue", "B", "B")]
    k = lambda rnd, t, ki, vi, w="Vandal", ass=(): {"round": rnd, "time_in_round_in_ms": t, "killer": {"puuid": ki},
                                                  "victim": {"puuid": vi}, "weapon": {"name": w},
                                                  "assistants": [{"puuid": a} for a in ass]}
    kills = [k(0, 1000, "me", "e1", "Ghost"), k(0, 2000, "e2", "ami"), k(0, 3000, "e2", "x"), k(0, 4000, "me", "e2", "Ghost"),
             k(1, 1000, "e1", "me"), k(1, 3000, "ami", "e1"),
             k(12, 500, "e1", "ami", ass=()), k(12, 900, "x", "e1", ass=("me",))]
    eco = lambda v: [{"player": {"puuid": "me"}, "economy": {"loadout_value": v}}]
    rounds = [{"id": 0, "winning_team": "Red", "stats": eco(800), "plant": {"player": {"puuid": "me"}}},
              {"id": 1, "winning_team": "Blue", "stats": eco(1000)},
              {"id": 2, "winning_team": "Red", "stats": eco(4700)}] + \
             [{"id": i, "winning_team": "Blue", "stats": eco(3000)} for i in range(3, 12)] + \
             [{"id": 12, "winning_team": "Red", "stats": eco(800), "defuse": {"player": {"puuid": "me"}}}]
    return {"metadata": {"match_id": "d1", "queue": {"id": "competitive"}}, "players": players,
            "kills": kills, "rounds": rounds}


def test_detail_from_v4():
    d = vs.detail_from_v4(_detail_match(), "me")
    assert d["rounds"] == 13 and d["fb"] == 1 and d["fd"] == 1
    assert d["clutch"] == {"1": 1}                                  # seul contre e2 en manche 1, manche gagnée
    assert d["weapons"] == {"Ghost": 2} and d["plants"] == 1 and d["defuses"] == 1
    assert d["pistol"] == [2, 2]                                     # manches 1 et 13 gagnées
    assert d["sides"]["atk"] == [2, 12] and d["sides"]["def"] == [1, 1]   # rouge attaque 12 manches puis défend
    assert d["eco"]["eco"] == [0, 1, 0] and d["eco"]["full"] == [1, 1, 0] and d["eco"]["force"] == [0, 9, 0]
    # KAST : M1 kills, M2 mort vengée en 2 s (échange), M3 à M12 survie, M13 assist → 13 / 13
    assert d["kast"] == 13
    assert d["mates"] == ["Pote"]
    assert vs.detail_from_v4(_detail_match(), "personne") is None


def test_extra_stats():
    base = vs.parse_stored({"data": [_stored(i, "Hush" if i % 2 else "Reyna", "Ascent" if i < 4 else "Bind", "Red",
                                             13 if i % 3 else 14, 11 if i % 3 else 12) for i in range(6)]}, "me")
    for r in base:
        r["dmg_rcv"] = 2000
        r["detail"] = vs.detail_from_v4(_detail_match(), "me")
    s = vs.aggregate_stats(base, min_games=2, roles={"Hush": "Duelliste", "Reyna": "Duelliste"})
    assert s["roles"][0]["role"] == "Duelliste" and s["roles"][0]["games"] == 6
    assert s["game_types"]["close"]["games"] == 6 and s["game_types"]["overtime"]["games"] == 2
    assert s["damage"]["ddelta"] == round((6 * 2600 - 6 * 2000) / sum(r["rw"] + r["rl"] for r in base))
    assert s["combos"]["best"][0]["games"] >= 2
    assert s["detail_games"] == 6 and s["duels"]["fb"] == 6 and s["duels"]["clutches"] == 6
    assert s["kast"] == 100 and s["sides"]["pistol"] == 100 and s["weapons"][0]["name"] == "Ghost"
    assert s["party"]["group"]["games"] == 6 and s["party"]["mates"][0]["name"] == "Pote"


def test_map_report():
    import datetime as dt
    data = [_stored(0, "Reyna", "Ascent", "Red", 13, 7), _stored(1, "Reyna", "Ascent", "Red", 13, 9),
            _stored(2, "Omen", "Ascent", "Red", 5, 13), _stored(3, "Omen", "Bind", "Red", 13, 5),
            _stored(4, "Jett", "Ascent", "Red", 13, 11, mode="unrated"), _stored(5, "Sage", "Ascent", "Red", 4, 13)]
    rows = vs.parse_stored({"data": data}, "me")
    roles = {"Reyna": "Duelliste", "Jett": "Duelliste", "Omen": "Contrôleur", "Sage": "Sentinelle"}
    r = vs.map_report(rows, "Ascent", roles, competitive=True)
    assert r["scope"] == "Compétition" and r["games"] == 4 and r["wins"] == 2 and r["winrate"] == 50
    assert r["agents"][0]["name"] == "Reyna" and r["agents"][0]["role"] == "Duelliste"
    assert r["recent"][-1]["res"] == "D" and r["recent"][-1]["score"] == "4-13"
    assert r["tip"] and r["overall"]["games"] == 5 and r["map_rank"] == 1 and r["maps_ranked"] == 1
    assert r["form"]["streak"] == 1 and r["form"]["streak_kind"] == "D" and len(r["form"]["last"]) == 6
    today = vs._local_date(rows[-1]["at"])
    assert vs.map_report(rows, "Ascent", roles, today=today)["form"]["today"]["games"] >= 1
    b = vs.map_report(rows, "Bind", roles, competitive=True)            # 1 seul match classé : repli tous modes
    assert b["scope"] == "Tous les modes" and b["games"] == 1 and b["tip"]
    assert vs.map_report(rows, "", roles) is None
    assert vs.map_report(rows, "Inconnue", roles)["games"] == 0

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
