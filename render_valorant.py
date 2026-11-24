"""
render_valorant.py : écran Valorant du panel 1920×462.

Deux vues, choisies par valorant_source.panel_mode() :
    live : sélection d'agent ou partie en cours (map, mode, score, rang)
    menu : jeu ouvert dans les menus (Riot ID, rang + RR, dernier match, série)

API : build_frame(bg=None, width=1920, height=462) -> Image
"""

from __future__ import annotations

import math
import time

from PIL import Image, ImageDraw, ImageFont

RED = (255, 70, 85)
ALLY = (90, 255, 200)
WIN = (90, 255, 160)
TEXT = (236, 232, 225)
DIM = (150, 150, 158)
TRACK = (40, 40, 52)

_fonts: dict = {}
_deco_cache: dict = {}


def _f(size: int):
    if size not in _fonts:
        try:
            from render import _font
            _fonts[size] = _font(size)
        except Exception:
            try:
                _fonts[size] = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", size)
            except OSError:
                _fonts[size] = ImageFont.load_default()
    return _fonts[size]


def _hex(color: str, fallback=(138, 143, 153)) -> tuple:
    try:
        c = color.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return fallback


def _text(od, xy, s, size, fill, anchor="la"):
    if not s:
        return
    try:
        od.text(xy, str(s), font=_f(size), fill=fill, anchor=anchor)
    except (ValueError, TypeError):
        od.text(xy, str(s), font=_f(size), fill=fill)


def _width(od, s, size) -> int:
    bb = od.textbbox((0, 0), str(s), font=_f(size))
    return bb[2] - bb[0]


def _fit(od, s, size, max_w, min_size=24) -> int:
    """Plus grande taille ≤ size qui fait tenir s dans max_w."""
    while size > min_size and _width(od, s, size) > max_w:
        size = int(size * 0.9)
    return size


def _decorations(w: int, h: int) -> Image.Image:
    """Barre rouge à gauche + rayures diagonales discrètes (calque mis en cache)."""
    key = (w, h)
    if key in _deco_cache:
        return _deco_cache[key]
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer, "RGBA")
    for x in range(w - 520, w + h, 34):
        d.line([(x, h), (x + h // 2, 0)], fill=(*RED, 16), width=2)
    for x in range(-h, 360, 34):
        d.line([(x, h), (x + h // 2, 0)], fill=(*RED, 9), width=2)
    d.rectangle([0, 0, 7, h], fill=(*RED, 235))
    _deco_cache[key] = layer
    return layer


def _diamond(od, cx, cy, r, color):
    od.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=(*color, 255))
    ri = int(r * 0.45)
    od.polygon([(cx, cy - ri), (cx + ri, cy), (cx, cy + ri), (cx - ri, cy)], fill=(12, 12, 18, 200))


def _rr_bar(od, x, y, w, rr, color):
    od.rectangle([x, y, x + w, y + 9], fill=(*TRACK, 255))
    if rr is None:
        return
    frac = max(0.0, min(1.0, float(rr) / 100.0))
    if frac > 0:
        od.rectangle([x, y, x + int(w * frac), y + 9], fill=(*color, 255))


def _change_str(change):
    if change is None:
        return "", DIM
    try:
        c = int(change)
    except (TypeError, ValueError):
        return "", DIM
    if c > 0:
        return f"+{c}", WIN
    if c < 0:
        return f"{c}", RED
    return "±0", DIM


def _rank_block(od, right_x, cy, rank, scale=1.0):
    """Bloc rang aligné à droite sur right_x, centré verticalement sur cy."""
    if not rank:
        _text(od, (right_x, cy), "Rang indisponible", int(30 * scale), (*DIM, 220), "rm")
        return
    color = _hex(rank.get("color") or "")
    tier = rank.get("tier") or "Non classé"
    rr = rank.get("rr")
    r = int(46 * scale)
    tsize = _fit(od, tier, int(54 * scale), int(430 * scale))
    tw = _width(od, tier, tsize)
    bar_w = max(int(300 * scale), tw)
    text_x = right_x - bar_w
    _diamond(od, text_x - r - 28, cy, r, color)
    _text(od, (text_x, cy - 6), tier, tsize, (*color, 255), "ls")
    if rr is not None:
        _rr_bar(od, text_x, cy + 18, bar_w, rr, color)
        rr_txt = f"{rr} RR"
        rr_size = int(30 * scale)
        _text(od, (text_x, cy + 64), rr_txt, rr_size, (*TEXT, 235), "ls")
        ch, ch_color = _change_str(rank.get("last_change"))
        if ch:
            _text(od, (text_x + _width(od, rr_txt, rr_size) + 18, cy + 64), ch, rr_size, (*ch_color, 240), "ls")


def _header(od, label):
    _text(od, (40, 46), "VALORANT", 28, (*RED, 255), "ls")
    _text(od, (40 + _width(od, "VALORANT", 28) + 18, 46), label, 24, (*DIM, 230), "ls")


def _draw_live(od, snap, w, h):
    live = snap.get("live") or {}
    state = live.get("state")
    _header(od, "SÉLECTION D’AGENT" if state == "PREGAME" else "EN PARTIE")

    # Gauche : map + mode
    map_txt = live.get("map") or "Map inconnue"
    msize = _fit(od, map_txt, 92, 560)
    _text(od, (40, 200), map_txt, msize, (*TEXT, 255), "ls")
    _text(od, (44, 252), live.get("mode") or "", 34, (*DIM, 240), "ls")
    party = live.get("party_size") or 0
    if party > 1:
        _text(od, (44, 300), f"Groupe de {party}", 26, (*DIM, 200), "ls")

    # Centre : score ou sélection d'agent
    cx, cy = w // 2, h // 2 + 10
    if state == "INGAME" and live.get("score_ally") is not None:
        a, e = live.get("score_ally") or 0, live.get("score_enemy") or 0
        a_col = ALLY if a >= e else tuple(int(c * 0.7) for c in ALLY)
        e_col = RED if e >= a else tuple(int(c * 0.7) for c in RED)
        _text(od, (cx - 70, cy), str(a), 210, (*a_col, 255), "rm")
        _text(od, (cx, cy - 8), ":", 150, (*DIM, 200), "mm")
        _text(od, (cx + 70, cy), str(e), 210, (*e_col, 255), "lm")
        _text(od, (cx - 70, cy + 128), "NOUS", 24, (*ALLY, 200), "rm")
        _text(od, (cx + 70, cy + 128), "EUX", 24, (*RED, 200), "lm")
    elif state == "INGAME":
        _text(od, (cx, cy), "EN JEU", 96, (*TEXT, 240), "mm")
    else:
        pulse = 0.65 + 0.35 * math.sin(time.monotonic() * 3.0)
        _text(od, (cx, cy - 10), "SÉLECTION D’AGENT", 76, (*TEXT, int(255 * pulse)), "mm")
        _text(od, (cx, cy + 64), "choisis ton agent", 30, (*DIM, 220), "mm")

    # Droite : rang
    _rank_block(od, w - 48, h // 2 - 20, snap.get("rank"), scale=0.9)


def _draw_menu(od, snap, w, h):
    _header(od, "MENU")
    acc = snap.get("account") or {}

    # Gauche : Riot ID + niveau
    name = acc.get("name") or "Joueur"
    tag = acc.get("tag") or ""
    nsize = _fit(od, name, 70, 470)
    _text(od, (40, 200), name, nsize, (*TEXT, 255), "ls")
    if tag:
        _text(od, (44 + _width(od, name, nsize) + 6, 200), f"#{tag}", int(nsize * 0.6), (*DIM, 230), "ls")
    level = acc.get("level") or (snap.get("live") or {}).get("level")
    if level:
        _text(od, (44, 250), f"Niveau {level}", 28, (*DIM, 220), "ls")
    rank = snap.get("rank") or {}
    if rank.get("peak_tier"):
        season = f" ({rank['peak_season']})" if rank.get("peak_season") else ""
        _text(od, (44, 296), f"Peak : {rank['peak_tier']}{season}", 26,
              (*_hex(rank.get('peak_color') or ''), 220), "ls")

    # Centre : rang en grand
    if snap.get("rank"):
        _rank_block(od, w // 2 + 260, h // 2 - 10, snap.get("rank"), scale=1.15)
    else:
        _text(od, (w // 2, h // 2), "Rang indisponible", 40, (*DIM, 220), "mm")

    # Droite : dernier match + série
    matches = snap.get("matches") or []
    right = w - 48
    if not matches:
        henrik = snap.get("henrik") or {}
        if not snap.get("configured"):
            msg = "Clé HenrikDev manquante"
        else:
            msg = henrik.get("error") or "Pas encore de match"
        _text(od, (right, h // 2), msg, _fit(od, msg, 28, 520, 18), (*DIM, 220), "rm")
        return
    m = matches[0]
    result = m.get("result")
    word, color = {"win": ("VICTOIRE", WIN), "loss": ("DÉFAITE", RED), "draw": ("ÉGALITÉ", DIM)}.get(result, ("TERMINÉ", DIM))
    _text(od, (right, 140), "DERNIER MATCH", 22, (*DIM, 210), "rs")
    _text(od, (right, 200), word, 54, (*color, 255), "rs")
    score = ""
    if m.get("rounds_won") is not None and m.get("rounds_lost") is not None:
        score = f"{m['rounds_won']}-{m['rounds_lost']} · "
    _text(od, (right, 246), f"{score}{m.get('map') or ''}", 32, (*TEXT, 240), "rs")
    kda = ""
    if m.get("kills") is not None:
        kda = f"{m.get('kills')}/{m.get('deaths')}/{m.get('assists')}"
    hs = f" · HS {m['hs_pct']} %" if m.get("hs_pct") is not None else ""
    _text(od, (right, 288), f"{m.get('agent') or ''} · {kda}{hs}".strip(" ·"), 28, (*DIM, 230), "rs")

    # Série : un carré par match, le plus récent à droite
    sq, gap = 22, 8
    x = right - sq
    for mm in matches[:5]:
        c = {"win": WIN, "loss": RED}.get(mm.get("result"), DIM)
        od.rectangle([x, 318, x + sq, 318 + sq], fill=(*c, 230))
        x -= sq + gap


def build_frame(bg=None, width: int = 1920, height: int = 462, snap: dict | None = None,
                mode: str | None = None) -> Image.Image:
    if snap is None:
        import valorant_source
        snap = valorant_source.snapshot()
        mode = mode or valorant_source.panel_mode()
    if mode is None:
        st = (snap.get("live") or {}).get("state")
        mode = "live" if st in ("PREGAME", "INGAME") else "menu"

    if bg is not None and bg.size == (width, height):
        img = bg.convert("RGB").copy()
    else:
        img = Image.new("RGB", (width, height), (8, 8, 12))
    deco = _decorations(width, height)
    img.paste(deco, (0, 0), deco)

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay, "RGBA")
    if mode == "live":
        _draw_live(od, snap, width, height)
    else:
        _draw_menu(od, snap, width, height)
    img.paste(overlay, (0, 0), overlay)
    return img
