from __future__ import annotations

from pathlib import Path
import re

from PIL import Image, ImageTk


class AvatarRenderer:
    """Compone el avatar con capas completas y alineadas en el mismo lienzo."""

    EXPRESSIONS = ("neutral", "happy", "angry", "sad", "surprised", "sleepy")
    MOUTHS = ("closed", "half", "open")

    def __init__(self, sprite_dir: Path, width: int = 360):
        self.sprite_dir = Path(sprite_dir)
        clean_base = self.sprite_dir / "base_limpia.png"
        self.base_path = clean_base if clean_base.exists() else self.sprite_dir / "base.png"
        self.base = Image.open(self.base_path).convert("RGBA")
        self.eyes = {
            expression: self._load_layer(f"eyes_{expression}.png")
            for expression in self.EXPRESSIONS
        }
        self.mouths = {
            index: self._load_layer(f"mouth_{name}.png")
            for index, name in enumerate(self.MOUTHS)
        }
        self.width = width
        self._cache: dict[tuple[str, int], ImageTk.PhotoImage] = {}

    def _load_layer(self, filename: str) -> Image.Image:
        layer = Image.open(self.sprite_dir / filename).convert("RGBA")
        if layer.size != self.base.size:
            raise ValueError(f"{filename} mide {layer.size}; debería medir {self.base.size}.")
        return layer

    def _compose(self, expression: str = "neutral", mouth: int = 0) -> Image.Image:
        expression = expression if expression in self.eyes else "neutral"
        mouth = mouth if mouth in self.mouths else 0
        composed = Image.alpha_composite(self.base, self.eyes[expression])
        composed = Image.alpha_composite(composed, self.mouths[mouth])
        height = round(composed.height * self.width / composed.width)
        return composed.resize((self.width, height), Image.Resampling.LANCZOS)

    def frame(self, expression: str = "neutral", mouth: int = 0) -> ImageTk.PhotoImage:
        key = (expression if expression in self.eyes else "neutral", mouth if mouth in self.mouths else 0)
        if key not in self._cache:
            self._cache[key] = ImageTk.PhotoImage(self._compose(*key))
        return self._cache[key]


def infer_expression(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.lower())
    rules = (
        ("sad", ("triste", "perdió", "perdimos", "murió", "moriste", "pena", "llor", "mal ahí")),
        ("angry", ("bronca", "enojo", "enoj", "puta", "mierda", "carajo", "boludo", "robaron", "falta")),
        ("surprised", ("sorpresa", "increíble", "no puede ser", "golazo", "wow", "de golpe")),
        ("sleepy", ("sueño", "dormir", "cansad", "bostezo", "tarde", "madrugada")),
        ("happy", ("feliz", "ganó", "ganamos", "gol", "bien", "jaja", "vamos", "genial", "listo")),
    )
    for expression, words in rules:
        if any(word in normalized for word in words):
            return expression
    return "neutral"
