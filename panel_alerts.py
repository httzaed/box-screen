"""Alertes « commentaire » en grand sur l'écran du boîtier (1920 × 462).

Le dashboard envoie chaque alerte en POST /api/valorant/alert ; ce module la dessine par-dessus
toutes les frames envoyées au boîtier, quel que soit le mode (paroles, image, audio…),
le temps de l'alerte (carte qui entre par la gauche), puis la retire. Rien ne change quand il n'y a pas d'alerte.

    push(text, label, kind, life_ms)   met une alerte en file (2 en attente au plus)
    wrap(send_frame)                    enveloppe panel._send_frame pour dessiner l'alerte active
"""

from __future__ import annotations

import io
import threading
import time
from collections import deque

from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 462
SLIDE = 0.35                     # secondes d'entrée et de sortie
GAP = 0.8                        # pause entre deux alertes

_lock = threading.Lock()
_queue: deque = deque(maxlen=3)
_current: dict | None = None
_fonts: dict = {}

COLORS = {"good": (240, 237, 232), "bad": (255, 77, 94), "pc": (255, 179, 71), "": (208, 2, 27)}
BG = (12, 6, 9)


def _font(size: int, bold: bool = True):
    key = (size, bold)
    if key not in _fonts:
        for path in (("C:/Windows/Fonts/bahnschrift.ttf", "C:/Windows/Fonts/arialbd.ttf", "DejaVuSans-Bold.ttf")
                     if bold else ("C:/Windows/Fonts/bahnschrift.ttf", "C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf")):
            try:
                f = ImageFont.truetype(path, size)
                if "bahnschrift" in path:
                    try:
                        f.set_variation_by_name("Bold" if bold else "SemiBold")
                    except Exception:
                        pass
                _fonts[key] = f
                break
            except Exception:
                continue
        else:
            _fonts[key] = ImageFont.load_default()
    return _fonts[key]


def push(text: str, label: str = "Commentaire", kind: str = "", life_ms: int = 6000) -> None:
    text = " ".join(str(text or "").split())[:180]
    if not text:
        return
    life = max(3.0, min(12.0, (life_ms or 6000) / 1000))
    with _lock:
        _queue.append({"text": text, "label": str(label or "Commentaire")[:30], "kind": kind if kind in COLORS else "",
                       "life": life})


def _tick(now: float) -> dict | None:
    """Alerte à dessiner maintenant (avec sa progression), en avançant la file si besoin."""
    global _current
    with _lock:
        if _current and now > _current["start"] + _current["life"] + GAP:
            _current = None
        if _current is None and _queue:
            _current = dict(_queue.popleft(), start=now)
        return dict(_current) if _current else None


def active() -> bool:
    return _tick(time.time()) is not None


def _wrap_text(draw, text: str, font, max_w: int, max_lines: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font) <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and draw.textlength(lines[-1] + "…", font=font) > max_w:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1].rstrip() + "…"
    return lines


def overlay(img: Image.Image, now: float | None = None) -> Image.Image:
    """Dessine l'alerte active sur une frame 1920 × 462 (renvoie une copie)."""
    now = time.time() if now is None else now
    a = _tick(now)
    if a is None:
        return img
    t = now - a["start"]
    life = a["life"]
    if t > life:
        return img
    # Entrée et sortie : glisse depuis la droite (courbe douce)
    if t < SLIDE:
        k = 1 - (1 - t / SLIDE) ** 3
    elif t > life - SLIDE:
        k = max(0.0, (life - t) / SLIDE) ** 2
    else:
        k = 1.0
    base = img.convert("RGB")
    if base.size != (W, H):
        base = base.resize((W, H))
    out = base.copy()
    col = COLORS.get(a["kind"], COLORS[""])
    card_w = 1320
    x1 = int(card_w * k)             # bord droit de la carte : elle entre par la gauche
    if x1 <= 0:
        return base
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    skew = 70
    # Carte : grand aplat sombre en biais, liseré de couleur, bande d'accent à droite
    d.polygon([(0, 0), (x1, 0), (x1 - skew, H), (0, H)], fill=BG + (238,))
    d.polygon([(x1 - 22, 0), (x1, 0), (x1 - skew, H), (x1 - skew - 22, H)], fill=col + (255,))
    d.line([(0, 3), (x1 - 22, 3)], fill=col + (255,), width=6)
    # Barre de vie en bas
    left = max(0.0, 1 - t / life)
    d.rectangle([0, H - 10, int((x1 - skew - 22) * left), H], fill=col + (255,))
    # Texte (il glisse avec la carte)
    tx = x1 - card_w + 60
    max_w = card_w - skew - 130
    label_f = _font(34, bold=False)
    d.text((tx, 40), a["label"].upper(), font=label_f, fill=col + (255,))
    # La plus grande taille où toute la phrase tient, sans couper
    for size, max_lines in ((84, 2), (72, 3), (60, 4), (50, 5)):
        f = _font(size)
        full = _wrap_text(d, a["text"], f, max_w, 99)
        line_h = int(size * 1.12)
        if len(full) <= max_lines:
            lines = full
            break
    else:
        lines = _wrap_text(d, a["text"], f, max_w, max_lines)
    y = 100 + max(0, (H - 140 - len(lines) * line_h) // 2)
    for ln in lines:
        d.text((tx, y), ln, font=f, fill=(240, 237, 232, 255))
        y += line_h
    out.paste(layer, (0, 0), layer)
    return out


def wrap(send_frame):
    """Enveloppe panel._send_frame(ep_out, ep_in, jpeg) : ajoute l'alerte active, sinon ne touche à rien."""
    if getattr(send_frame, "_with_alerts", False):
        return send_frame

    def _send(ep_out, ep_in, jpeg_data: bytes):
        if active():
            try:
                img = Image.open(io.BytesIO(jpeg_data))
                buf = io.BytesIO()
                overlay(img).save(buf, format="JPEG", quality=85)
                jpeg_data = buf.getvalue()
            except Exception as e:
                print(f"[ALERTES] Dessin impossible : {e}")
        return send_frame(ep_out, ep_in, jpeg_data)

    _send._with_alerts = True
    return _send
