"""
ASCII VHS renderer — génération de frames animées style VHS pour mode ascii_vhs.

Module avec état persistant (stamps pré-rendus, offsets animation).
Initialisation paresseuse : les stamps sont créés au premier appel.

API principale:
    build_frame(bg: Image | None = None, y_offset: int = 0) -> Image
"""

from __future__ import annotations
import math
import random
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ── Configuration (valeurs par défaut, peuvent être surchargées) ──────────────
DEFAULT_ASCII_SIZE = 13
DEFAULT_FPS = 14
DEFAULT_INTENSITY = 0.4

# Couleurs du dégradé horizontal (violet/rose → orange-rouge)
DEFAULT_START_COLOR = (221, 0, 255)
DEFAULT_END_COLOR = (255, 40, 0)

# Fichier ASCII par défaut
_HERE = Path(__file__).parent
DEFAULT_ASCII_FILE = _HERE / "ascii_bg.txt"


# ── Fonctions utilitaires ──────────────────────────────────────────────────────

def _mono(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Charge une fonte monospace (cross-platform)."""
    candidates = [
        "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/cour.ttf",
        "C:/Windows/Fonts/lucon.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/usr/share/fonts/truetype/courier-prime/CourierPrime-Regular.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


# ── Classe principale ───────────────────────────────────────────────────────────

class ASCIIRenderer:
    """
    Renderer ASCII VHS avec état persistant.

    Attributs:
        ascii_file: Path vers le fichier ASCII (ascii_bg.txt)
        ascii_size: Taille de la fonte
        intensity: Intensité des effets VHS (0.0 à 1.0)
        start_color: Couleur de début du dégradé (R, G, B)
        end_color: Couleur de fin du dégradé (R, G, B)
        width: Largeur de sortie
        height: Hauteur de sortie

    État persistant (initialisé paresseusement):
        _stamps: dict char -> np.ndarray (mask 0..1)
        _base_lines: list[str] lignes ASCII
        _cols: nombre de colonnes
        _cw, _ch: dimensions d'un caractère
        _grad: dégradé horizontal (cols, 3)
        _x_off, _y_off: offset pour centrer
        _frame_n: numéro de frame pour animation
    """

    def __init__(
        self,
        width: int = 320,
        height: int = 240,
        ascii_file: Path | str = DEFAULT_ASCII_FILE,
        ascii_size: int = DEFAULT_ASCII_SIZE,
        intensity: float = DEFAULT_INTENSITY,
        start_color: tuple[int, int, int] = DEFAULT_START_COLOR,
        end_color: tuple[int, int, int] = DEFAULT_END_COLOR,
    ):
        self.width = width
        self.height = height
        self.ascii_file = Path(ascii_file)
        self.ascii_size = ascii_size
        self.intensity = intensity
        self.start_color = start_color
        self.end_color = end_color

        # État initialisé paresseusement
        self._initialized: bool = False
        self._stamps: dict[str, np.ndarray] = {}
        self._base_lines: list[str] = []
        self._cols: int = 0
        self._cw: int = 0
        self._ch: int = 0
        self._grad: np.ndarray  # (cols, 3)
        self._x_off: int = 0
        self._y_off: int = 0
        self._frame_n: int = 0

    def _initialize(self) -> None:
        """Initialise les structures paresseusement."""
        if self._initialized:
            return

        # Charger la fonte
        font = _mono(self.ascii_size)

        # Calculer les dimensions d'un caractère
        dummy = Image.new("RGB", (1, 1))
        d = ImageDraw.Draw(dummy)
        b = d.textbbox((0, 0), "@", font=font)
        self._cw = max(1, b[2] - b[0])
        self._ch = max(1, b[3] - b[1] + 2)

        # Charger les lignes ASCII
        raw_lines = self.ascii_file.read_text(encoding="utf-8").split("\n")
        self._cols = max(len(l) for l in raw_lines) if raw_lines else 0
        self._base_lines = [l[:self._cols].ljust(self._cols) for l in raw_lines]

        # Pré-render les stamps
        unique_chars = set(c for l in self._base_lines for c in l if c not in (" ", "\r"))
        for c in unique_chars:
            s_img = Image.new("RGB", (self._cw, self._ch), (0, 0, 0))
            ImageDraw.Draw(s_img).text((0, 0), c, font=font, fill=(255, 255, 255))
            self._stamps[c] = np.array(s_img, dtype=np.float32) / 255.0

        # Pré-calculer le gradient horizontal
        t_arr = np.linspace(0, 1, self._cols, dtype=np.float32)
        self._grad = np.stack([
            self.start_color[0] + (self.end_color[0] - self.start_color[0]) * t_arr,
            self.start_color[1] + (self.end_color[1] - self.start_color[1]) * t_arr,
            self.start_color[2] + (self.end_color[2] - self.start_color[2]) * t_arr,
        ], axis=1).astype(np.float32)  # (cols, 3)

        # Calculer les offsets pour centrer
        self._x_off = (self.width - self._cw * self._cols) // 2
        self._y_off = (self.height - self._ch * len(self._base_lines)) // 2

        self._initialized = True

    def _vhs_effect(self, lines: list[str]) -> list[str]:
        """Applique les effets VHS à une liste de lignes."""
        t = self._frame_n / 30.0
        out = []
        amp = 2 + 6 * self.intensity
        freq = 0.18 + 0.25 * self.intensity

        for idx, ln in enumerate(lines):
            # Décalage horizontal ondulant
            dx = int(amp * math.sin(t * 2 * math.pi * freq + idx * 0.12))
            s = (" " * dx + ln) if dx > 0 else ln[abs(dx):]
            s = s[:self._cols].ljust(self._cols)

            # Remplacement aléatoire de caractères (glitch)
            arr = list(s)
            for i, c in enumerate(arr):
                if c != " " and random.random() < 0.006 + 0.02 * self.intensity:
                    arr[i] = random.choice(["`", "'", ".", ",", "-"])
            out.append("".join(arr))

        # Glitch: décalage de quelques lignes
        if random.random() < 0.05 * self.intensity:
            sh = random.randint(1, 2)
            st = random.randint(0, max(0, len(out) - sh))
            dx = random.choice([-6, 6])
            for i in range(st, st + sh):
                s = (" " * dx + out[i]) if dx > 0 else out[i][abs(dx):]
                out[i] = s[:self._cols].ljust(self._cols)

        return out

    def build_frame(
        self,
        bg: Optional[Image.Image] = None,
        y_offset: int = 0,
        advance_frame: bool = True,
    ) -> Image.Image:
        """
        Génère une frame ASCII VHS.

        Args:
            bg: Image de fond (optionnelle). Si None, fond noir.
            y_offset: Décalage vertical additionnel (en pixels).
            advance_frame: Si True, incrémente le compteur de frame.

        Returns:
            Image PIL (RGB, width×height).
        """
        self._initialize()

        # Appliquer l'effet VHS aux lignes de base
        vhs_lines = self._vhs_effect(self._base_lines)

        # Créer le canvas numpy
        canvas = np.zeros((self.height, self.width, 3), dtype=np.float32)

        # Dessiner les caractères
        y_base = self._y_off + y_offset
        for row, ln in enumerate(vhs_lines):
            y = y_base + row * self._ch
            if y < 0 or y + self._ch > self.height:
                continue

            # Alternance d'intensité (effet scanline)
            dim = 0.65 if row % 2 == 1 else 1.0

            for col, char in enumerate(ln):
                if char in (" ", "\r"):
                    continue
                if char not in self._stamps:
                    continue

                x = self._x_off + col * self._cw
                if x < 0 or x + self._cw > self.width:
                    continue

                color = self._grad[col] * dim
                stamp = self._stamps[char]
                canvas[y:y+self._ch, x:x+self._cw] += stamp * color

        # Clip et convertir
        np.clip(canvas, 0, 255, out=canvas)
        img = Image.fromarray(canvas.astype(np.uint8))

        # Compositer avec le fond si fourni
        if bg is not None:
            bg = bg.resize((self.width, self.height), Image.Resampling.BILINEAR)
            bg_arr = np.array(bg, dtype=np.float32)
            # Alpha blending simple (canvas a un fond noir transparent)
            alpha = np.clip(canvas / 255.0, 0, 1)
            result = bg_arr * (1 - alpha) + canvas * alpha
            img = Image.fromarray(result.astype(np.uint8))

        # Avancer la frame
        if advance_frame:
            self._frame_n += 1

        return img

    def reset_frame(self) -> None:
        """Réinitialise le compteur de frame à 0."""
        self._frame_n = 0


# ── Instance singleton pour usage direct ───────────────────────────────────────

_default_renderer: Optional[ASCIIRenderer] = None


def build_frame(
    bg: Optional[Image.Image] = None,
    y_offset: int = 0,
    width: int = 320,
    height: int = 240,
    ascii_file: Path | str = DEFAULT_ASCII_FILE,
    ascii_size: int = DEFAULT_ASCII_SIZE,
    intensity: float = DEFAULT_INTENSITY,
    start_color: tuple[int, int, int] = DEFAULT_START_COLOR,
    end_color: tuple[int, int, int] = DEFAULT_END_COLOR,
) -> Image.Image:
    """
    Fonction helper utilisant un renderer singleton.
    Au premier appel, le renderer est créé avec les paramètres fournis.
    Aux appels suivants, seuls bg et y_offset sont pris en compte
    (les autres paramètres sont ignorés pour conserver la cohérence).

    Pour changer les paramètres, utilisez directement ASCIIRenderer.
    """
    global _default_renderer

    if _default_renderer is None:
        _default_renderer = ASCIIRenderer(
            width=width,
            height=height,
            ascii_file=ascii_file,
            ascii_size=ascii_size,
            intensity=intensity,
            start_color=start_color,
            end_color=end_color,
        )

    return _default_renderer.build_frame(bg=bg, y_offset=y_offset)


def reset() -> None:
    """Réinitialise le renderer singleton (nouvelle initialisation au prochain appel)."""
    global _default_renderer
    _default_renderer = None
