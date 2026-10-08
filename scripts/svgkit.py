"""Общая база для всех SVG профиля: палитра, вшитые шрифты, замер текста.

Только стандартная библиотека — модуль импортируется и в GitHub Actions.
GitHub показывает SVG из README через <img>, поэтому внешние ресурсы
(шрифты по ссылке, картинки, скрипты) там не грузятся. Шрифты вшиваются
прямо в файл как base64 woff2.
"""

import base64
import json
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"
GEN = ASSETS / "gen"

# Две темы. light повторяет переменные сайта ilovevodkaa.github.io,
# dark — его зеркало: чёрная плита, белые буквы.
PALETTE = {
    "dark": {
        "bg": "#0a0a0a",      # плита карточки
        "panel": "#111112",   # вложенные блоки, плашки
        "ink": "#ffffff",     # заголовки, главное
        "text": "#d4d4d8",    # основной текст
        "muted": "#8a8a92",   # подписи, мета
        "line": "#26262a",    # рамки и разделители
        "faint": "#18181b",   # сетка, пустые ячейки
    },
    "light": {
        "bg": "#ffffff",
        "panel": "#f6f6f7",
        "ink": "#0b0b0c",
        "text": "#2b2b30",
        "muted": "#74747c",
        "line": "#e6e6ea",
        "faint": "#f1f1f3",
    },
}
THEMES = ("dark", "light")

# Имя CSS-семейства -> (файл woff2, вес). Семейства с разными весами
# объявляются одним font-family, браузер сам выберет по font-weight.
FONT_FILES = {
    ("Inter Tight", 500): "InterTight-Medium",
    ("Inter Tight", 600): "InterTight-SemiBold",
    ("Inter Tight", 800): "InterTight-ExtraBold",
    ("JetBrains Mono", 400): "JetBrainsMono-Regular",
    ("JetBrains Mono", 700): "JetBrainsMono-Bold",
}
SANS = "'Inter Tight', 'Inter', 'Segoe UI', system-ui, sans-serif"
MONO = "'JetBrains Mono', ui-monospace, Consolas, monospace"


@lru_cache(maxsize=None)
def _b64(stem):
    return base64.b64encode((FONTS / f"{stem}.woff2").read_bytes()).decode("ascii")


def font_css(*faces):
    """@font-face для перечисленных начертаний: font_css(("Inter Tight", 800), ("JetBrains Mono", 400)).
    Без аргументов — все пять (тяжело, ~165 КБ base64; лучше перечислять)."""
    faces = faces or tuple(FONT_FILES)
    out = []
    for family, weight in faces:
        stem = FONT_FILES[(family, weight)]
        out.append(
            f"@font-face{{font-family:'{family}';font-weight:{weight};font-style:normal;"
            f"src:url(data:font/woff2;base64,{_b64(stem)}) format('woff2');}}"
        )
    return "".join(out)


@lru_cache(maxsize=None)
def _metrics():
    return json.loads((FONTS / "metrics.json").read_text(encoding="utf-8"))


def text_width(text, family="Inter Tight", weight=500, size=16, letter_spacing=0.0):
    """Ширина строки в px по реальным advance width шрифта (без кернинга — ошибка < 2%)."""
    m = _metrics()[FONT_FILES[(family, weight)]]
    upm, adv = m["upm"], m["adv"]
    fallback = adv.get("110", upm // 2)  # 'n'
    units = sum(adv.get(str(ord(ch)), fallback) for ch in text)
    return units / upm * size + letter_spacing * max(len(text) - 1, 0)


def mono_width(text, size):
    """JetBrains Mono моноширинный: каждый символ ровно 0.6 em."""
    return len(text) * 0.6 * size


def wrap(text, max_width, family="Inter Tight", weight=500, size=16, max_lines=None):
    """Перенос по словам под ширину. Если строк больше max_lines — обрезает с «…»."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if text_width(trial, family, weight, size) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and text_width(last + "…", family, weight, size) > max_width:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    return lines


def esc(s):
    """Экранирование текста и атрибутов для XML."""
    return escape(str(s), {'"': "&quot;"})


def svg_doc(width, height, body, title, css="", desc=None):
    """Корневой <svg> с role/title для скринридеров и CSS в <style>."""
    d = f"<desc>{esc(desc)}</desc>" if desc else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="t">'
        f'<title id="t">{esc(title)}</title>{d}'
        f"<style>{css}</style>{body}</svg>"
    )


def write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return path
