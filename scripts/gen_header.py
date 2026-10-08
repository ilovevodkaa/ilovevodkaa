"""Шапка и футер профиля.

    python scripts/gen_header.py

Пишет assets/header-{dark,light}.svg (1000x300) и assets/footer-{dark,light}.svg
(1000x120). Только stdlib + svgkit.

Сквозной мотив — 1-битный дизеринг (ordered Bayer 8x8, пиксель 4px): ч/б-замена
«волн» capsule-render. Полоса футера — та же матрица, повёрнутая на 180°,
так что шапка и футер «закрывают» страницу с двух концов.

Шапка: мета-строка, вордмарк «_ilovevodka» (брусок-курсор с фавикона сайта
мигает, буквы «печатаются» один раз при загрузке), печатная машинка с фразами
из био и указатель 01–04 справа, который подсвечивается синхронно с машинкой.

Все анимации — CSS @keyframes, поэтому prefers-reduced-motion выключает их
одной строкой. Без анимации виден осмысленный финальный кадр: вордмарк целиком,
первая фраза напечатана, курсор стоит после неё, в указателе выбран пункт 01.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import svgkit as K  # noqa: E402

# ---------------------------------------------------------------- данные

PHRASES = [
    "пишу код.",
    "играю в игры.",
    "не сплю по ночам.",
    "делаю сайты и автоматизации на ИИ.",
]
INDEX = ["код", "игры", "ночь", "сайты + ИИ"]  # пункты указателя справа, по фразе на пункт
META_LEFT = "_ilovevodka / ( bio )"
META_RIGHT = "ООО «_ilovevodka» · SG · с 06.2025"
COORDS = "1°17′N  103°50′E"  # Сингапур — локация из профиля
FOOT_RIGHT = "сделано ночью · 2026 · ilovevodkaa.github.io"

# ---------------------------------------------------------------- сетка шапки

W, H = 1000, 300
PAD = 24            # поля мета-строки и линеек
X0 = 40             # левый край вордмарка и машинки
META_Y = 34         # базовая линия мета-строки
RULE_Y = 48.5       # волосяная линия под ней (полпикселя — чёткий 1px)

WM_SIZE = 120       # вордмарк Inter Tight 800
WM_LS = -0.04 * WM_SIZE
WM_BASE = 172
BAR_W = round(0.46 * WM_SIZE)   # брусок-курсор «_»
BAR_H = round(0.115 * WM_SIZE)
BAR_GAP = round(0.06 * WM_SIZE)

TW_SIZE = 18        # машинка JetBrains Mono
TW_BASE = 220
CW = 0.6 * TW_SIZE  # ширина знака моноширинного шрифта

COL_X = 744         # колонка указателя (10-я колонка сетки 12 x 79.3)
IX_SIZE = 12
IX_STEP = 24

PX = 4              # «пиксель» дизеринга
STRIP_ROWS = 10     # высота полосы: 10 x 4 = 40px
COLS = W // PX

# футер
FH = 120
F_BASE = 88
F_WM = 22

# тайминги (секунды)
WM_START, WM_STEP = 0.35, 0.075     # буквы вордмарка
BAR_BLINK_AT, BLINK = 1.2, 1.06     # брусок начинает мигать после набора
TW_DELAY = 1.3                      # машинка стартует, когда вордмарк набран
TYPE, DEL, GAP = 0.07, 0.028, 0.45  # шаг набора, шаг стирания, пауза между фразами


def hold(n):
    """Сколько фраза висит целиком: длинной — дольше, чтобы успеть прочитать."""
    return 1.8 + 0.035 * n


# ---------------------------------------------------------------- дизеринг

def bayer(n=8):
    """Матрица Байера n x n рекурсией M2k = [[4M, 4M+2], [4M+3, 4M+1]]."""
    m = [[0]]
    base = [[0, 2], [3, 1]]
    while len(m) < n:
        k = len(m)
        m = [[4 * m[y % k][x % k] + base[y // k][x // k] for x in range(2 * k)]
             for y in range(2 * k)]
    return m


BAYER = bayer(8)


def smooth(e0, e1, x):
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def strip_density(c, r):
    """Плотность полосы шапки: ink слева -> bg справа. Изолинии чуть наклонены
    (нижние ряды тянутся дальше вправо), поэтому верхний край полосы растворяется."""
    u = (c + 0.5) / COLS                  # 0 слева, 1 справа
    v = (r + 0.5) / STRIP_ROWS            # 0 сверху, 1 снизу
    return 1 - smooth(-0.04, 1.0, u + 0.2 * (1 - v))


def strip_grid():
    return [[strip_density(c, r) > (BAYER[r % 8][c % 8] + 0.5) / 64 for c in range(COLS)]
            for r in range(STRIP_ROWS)]


def grid_path(grid, y0):
    """Один path: по подпути на каждый горизонтальный отрезок горящих пикселей."""
    out = []
    for r, row in enumerate(grid):
        y = y0 + r * PX
        c, n = 0, len(row)
        while c < n:
            if not row[c]:
                c += 1
                continue
            s = c
            while c < n and row[c]:
                c += 1
            w = (c - s) * PX
            out.append(f"M{s * PX} {y}h{w}v{PX}h-{w}z")
    return "".join(out)


# ---------------------------------------------------------------- общие куски

def fmt(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def pct(t, total):
    return f"{t / total * 100:.3f}".rstrip("0").rstrip(".") + "%"


def keyframes(name, frames, total):
    """frames: [(t, 'decl;decl')] -> @keyframes; одинаковые моменты схлопываются (побеждает последний)."""
    seen = {}
    for t, decl in frames:
        seen[pct(t, total)] = decl
    body = "".join(f"{k}{{{v}}}" for k, v in seen.items())
    return f"@keyframes {name}{{{body}}}"


def plate(w, h, pal):
    return (f'<rect width="{w}" height="{h}" rx="10" fill="{pal["bg"]}"/>')


def border(w, h, pal):
    return (f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="9.5" '
            f'fill="none" stroke="{pal["line"]}"/>')


def crop(x, y, dx, dy, color):
    """Метка обрезки: уголок из двух штрихов, (dx, dy) — куда смотрят плечи."""
    return (f'<path d="M{x} {y + 9 * dy}V{y}H{x + 9 * dx}" fill="none" '
            f'stroke="{color}" stroke-width="1"/>')


REDUCED = "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"


def at(t, seek):
    """Задержка анимации. seek — кадр на момент seek секунд (для превью): все анимации
    сдвигаются назад и ставятся на паузу."""
    return fmt(t - (seek or 0))


def frozen(seek):
    return "" if seek is None else "*{animation-play-state:paused!important}"


def base_css(pal):
    return (
        f".s{{font-family:{K.SANS};font-weight:800}}"
        f".m{{font-family:{K.MONO};font-weight:400}}"
        f".meta{{font-size:12px;fill:{pal['muted']}}}"
    )


# ---------------------------------------------------------------- шапка

def wordmark_letters():
    """[(x, ch)] — позиции букв вордмарка по метрикам шрифта, трекинг -0.04em."""
    x = X0 + BAR_W + BAR_GAP
    out = []
    for ch in "ilovevodka":
        out.append((x, ch))
        x += K.text_width(ch, "Inter Tight", 800, WM_SIZE) + WM_LS
    return out, x - WM_LS


def typewriter_timeline():
    """[(t0 начало набора, t1 набрано, t2 начало стирания, t3 стёрто, t4 конец паузы, n)]."""
    t, segs = 0.0, []
    for p in PHRASES:
        n = len(p)
        t0 = t
        t1 = t0 + n * TYPE
        t2 = t1 + hold(n)
        t3 = t2 + n * DEL
        t4 = t3 + GAP
        segs.append((t0, t1, t2, t3, t4, n))
        t = t4
    return segs, t


def header(theme, seek=None):
    pal = K.PALETTE[theme]
    ink, bg, muted, text, line = pal["ink"], pal["bg"], pal["muted"], pal["text"], pal["line"]
    letters, wm_end = wordmark_letters()
    assert wm_end < COL_X - 40, f"вордмарк наезжает на колонку: {wm_end:.0f}"
    assert X0 + 2 * CW + max(map(len, PHRASES)) * CW < COL_X - 16, "фраза длиннее левой колонки"

    segs, T = typewriter_timeline()
    strip_y = H - STRIP_ROWS * PX

    css = [K.font_css(("Inter Tight", 800), ("JetBrains Mono", 400)), base_css(pal)]
    css.append(f".wm{{font-size:{WM_SIZE}px;fill:{ink}}}")
    css.append(f".tw{{font-size:{TW_SIZE}px;fill:{text}}}.pr{{font-size:{TW_SIZE}px;fill:{muted}}}")
    css.append(f".ix{{font-size:{IX_SIZE}px;fill:{muted}}}.ix.on{{fill:{bg}}}")

    # буквы вордмарка появляются по одной, один раз
    css.append("@keyframes in{from{opacity:0}to{opacity:1}}"
               ".l{animation:in .01s steps(1,end) backwards}")
    # брусок: горит, пока печатаются буквы, потом мигает
    css.append("@keyframes bl{0%{opacity:1}50%{opacity:0}}"
               f".bar{{animation:bl {BLINK}s step-end {at(BAR_BLINK_AT, seek)}s infinite}}")

    # машинка: каретка (заслонка цвета плиты + курсор) едет по знакам
    run = f"{fmt(T)}s step-end {at(TW_DELAY, seek)}s infinite backwards"
    fr = []
    for t0, t1, t2, t3, t4, n in segs:
        x = fmt(n * CW)
        fr += [(t0, f"transform:translateX(0px);animation-timing-function:steps({n},end)"),
               (t1, f"transform:translateX({x}px);animation-timing-function:step-end"),
               (t2, f"transform:translateX({x}px);animation-timing-function:steps({n},end)"),
               (t3, "transform:translateX(0px);animation-timing-function:step-end")]
    fr.append((T, "transform:translateX(0px)"))
    css.append(keyframes("car", fr, T) + f".car{{animation:car {run}}}")

    # курсор: горит при наборе/стирании, мигает на паузах
    fr = []
    for t0, t1, t2, t3, t4, n in segs:
        fr.append((t0, "opacity:1"))
        for a, b in ((t1, t2), (t3, t4)):
            t, on = a, True
            while t < b - 0.05:
                fr.append((t, f"opacity:{1 if on else 0}"))
                t += 0.5
                on = not on
            fr.append((b, "opacity:1"))
    css.append(keyframes("cur", fr, T) + f".cur{{animation:cur {run}}}")

    # фразы видны только в своём окне; указатель подсвечивает текущий пункт
    for i, (t0, t1, t2, t3, t4, n) in enumerate(segs):
        end = segs[i + 1][0] if i + 1 < len(segs) else T
        vis = [(0, "opacity:0"), (t0, "opacity:1"), (t3, "opacity:0"), (T, "opacity:0")]
        sel = [(0, "opacity:0"), (t0, "opacity:1"), (end, "opacity:0"), (T, "opacity:0")]
        col = [(0, f"fill:{muted}"), (t0, f"fill:{bg}"), (end, f"fill:{muted}"), (T, f"fill:{muted}")]
        css.append(keyframes(f"p{i}", vis, T) + f".p{i}{{animation:p{i} {run}}}")
        css.append(keyframes(f"h{i}", sel, T) + f".h{i}{{animation:h{i} {run}}}")
        css.append(keyframes(f"c{i}", col, T) + f".c{i}{{animation:c{i} {run}}}")
    css.append(REDUCED + frozen(seek))

    b = [f'<defs><clipPath id="pl"><rect width="{W}" height="{H}" rx="10"/></clipPath></defs>',
         plate(W, H, pal), border(W, H, pal)]

    # полоса дизеринга — поверх рамки, чтобы пиксели доходили до самого края плиты
    b.append(f'<g clip-path="url(#pl)"><path fill="{ink}" shape-rendering="crispEdges" '
             f'd="{grid_path(strip_grid(), strip_y)}"/></g>')

    # мета-строка и линейка с делениями сетки 12 колонок
    b.append(f'<text class="m meta" x="{PAD}" y="{META_Y}">{K.esc(META_LEFT)}</text>')
    b.append(f'<text class="m meta" x="{W - PAD}" y="{META_Y}" text-anchor="end">'
             f'{K.esc(META_RIGHT)}</text>')
    ticks = "".join(f"M{fmt(PAD + i * (W - 2 * PAD) / 12)} {RULE_Y}v4" for i in range(13))
    b.append(f'<path d="M{PAD} {RULE_Y}H{W - PAD}{ticks}" stroke="{line}" fill="none"/>')

    # вордмарк: брусок + буквы
    b.append(f'<rect class="bar" x="{X0}" y="{WM_BASE - BAR_H}" width="{BAR_W}" '
             f'height="{BAR_H}" rx="2" fill="{ink}"/>')
    for i, (x, ch) in enumerate(letters):
        b.append(f'<text class="s wm l" x="{fmt(x)}" y="{WM_BASE}" '
                 f'style="animation-delay:{at(WM_START + i * WM_STEP, seek)}s">{ch}</text>')

    # машинка
    tx = X0 + 2 * CW
    b.append(f'<text class="m pr" x="{X0}" y="{TW_BASE}">&gt;</text>')
    for i, p in enumerate(PHRASES):
        hide = "" if i == 0 else ' opacity="0"'
        b.append(f'<text class="m tw p{i}" x="{fmt(tx)}" y="{TW_BASE}"{hide}>{K.esc(p)}</text>')
    cover_w = max(map(len, PHRASES)) * CW + 6
    first = fmt(len(PHRASES[0]) * CW)
    b.append(f'<g class="car" transform="translate({first} 0)">'
             f'<rect x="{fmt(tx)}" y="{TW_BASE - 19}" width="{fmt(cover_w)}" height="27" fill="{bg}"/>'
             f'<rect class="cur" x="{fmt(tx + 0.5)}" y="{TW_BASE + 2}" width="{fmt(CW - 1)}" '
             f'height="3" fill="{ink}"/></g>')

    # указатель 01–04 справа: выбранный пункт — инверсия, как строка меню в терминале
    sel_w = W - PAD - COL_X + 8
    top = WM_BASE - (len(INDEX) - 1) * IX_STEP
    for i, item in enumerate(INDEX):
        y = top + i * IX_STEP
        on = i == 0
        hide = "" if on else ' opacity="0"'
        b.append(f'<rect class="h{i}" x="{COL_X - 8}" y="{y - 13}" width="{sel_w}" height="18" '
                 f'rx="2" fill="{ink}"{hide}/>')
        b.append(f'<text class="m ix c{i}{" on" if on else ""}" x="{COL_X}" y="{y}" '
                 f'xml:space="preserve">{i + 1:02d}  {K.esc(item)}</text>')
    b.append(f'<path d="M{COL_X - 24.5} {top - 18}V{TW_BASE + 6}" stroke="{line}"/>')
    b.append(f'<text class="m meta" x="{COL_X}" y="{TW_BASE}" xml:space="preserve">'
             f'{K.esc(COORDS)}</text>')

    # метки обрезки по верхним углам рабочего поля
    b.append(crop(PAD - 12, 12, 1, 1, muted).replace("/>", ' opacity=".5"/>'))
    b.append(crop(W - PAD + 12, 12, -1, 1, muted).replace("/>", ' opacity=".5"/>'))


    desc = ("_ilovevodka — " + " ".join(PHRASES) +
            " Полоса 1-битного дизеринга внизу.")
    return K.svg_doc(W, H, "".join(b), "_ilovevodka", "".join(css), desc)


# ---------------------------------------------------------------- футер

def footer(theme, seek=None):
    pal = K.PALETTE[theme]
    ink, muted = pal["ink"], pal["muted"]
    grid = [row[::-1] for row in strip_grid()[::-1]]  # шапка, повёрнутая на 180°

    css = [K.font_css(("Inter Tight", 800), ("JetBrains Mono", 400)), base_css(pal)]
    css.append(f".fw{{font-size:{F_WM}px;fill:{ink}}}")
    css.append("@keyframes bl{0%{opacity:1}50%{opacity:0}}"
               f".bar{{animation:bl {BLINK}s step-end {at(0, seek)}s infinite}}")
    css.append(REDUCED + frozen(seek))

    bw, bh, gap = round(0.46 * F_WM), max(3, round(0.13 * F_WM)), round(0.07 * F_WM)
    xs, x = [], X0 + bw + gap
    for ch in "ilovevodka":
        xs.append(fmt(x))
        x += K.text_width(ch, "Inter Tight", 800, F_WM) - 0.04 * F_WM

    b = [f'<defs><clipPath id="pl"><rect width="{W}" height="{FH}" rx="10"/></clipPath></defs>',
         plate(W, FH, pal), border(W, FH, pal)]
    b.append(f'<g clip-path="url(#pl)"><path fill="{ink}" shape-rendering="crispEdges" '
             f'd="{grid_path(grid, 0)}"/></g>')
    b.append(f'<rect class="bar" x="{X0}" y="{F_BASE - bh}" width="{bw}" height="{bh}" '
             f'rx="1" fill="{ink}"/>')
    b.append(f'<text class="s fw" x="{" ".join(xs)}" y="{F_BASE}">ilovevodka</text>')
    b.append(f'<text class="m meta" x="{W - PAD}" y="{F_BASE}" text-anchor="end">'
             f'{K.esc(FOOT_RIGHT)}</text>')
    return K.svg_doc(W, FH, "".join(b), "_ilovevodka — подвал",
                     "".join(css), "сделано ночью · 2026 · ilovevodkaa.github.io")


# ---------------------------------------------------------------- запуск

def main(argv):
    # python scripts/gen_header.py                  — ассеты в assets/
    # python scripts/gen_header.py --seek 6.5 DIR   — замороженный кадр на 6.5 с в DIR (превью)
    if argv[:1] == ["--seek"]:
        seek, out = float(argv[1]), Path(argv[2])
        for theme in K.THEMES:
            for name, fn in (("header", header), ("footer", footer)):
                K.write(out / f"{name[0]}{theme[0]}.svg", fn(theme, seek))
        return
    for theme in K.THEMES:
        for name, fn in (("header", header), ("footer", footer)):
            p = K.write(K.ASSETS / f"{name}-{theme}.svg", fn(theme))
            print(f"{p.relative_to(K.ROOT)}  {p.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main(sys.argv[1:])
