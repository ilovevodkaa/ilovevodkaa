#!/usr/bin/env python3
"""Живые карточки профиля: проекты, статистика, лента коммитов.

Запуск: python scripts/build.py  (GitHub Actions, каждые 6 часов).
Пишет в assets/gen/ по два файла на вид — *-dark.svg и *-light.svg.

Окружение:
  GITHUB_TOKEN   токен (необязателен; без него лимит 60 запросов в час)
  GH_USER        чей профиль (по умолчанию ilovevodkaa)
  GH_CACHE_DIR   папка кеша ответов API: читаем оттуда, недостающее докачиваем
  GH_OFFLINE=1   только кеш, без сети
  BUILD_NOW      «сейчас» в ISO 8601 — для воспроизводимых проверок

Только стандартная библиотека и svgkit. Любая ошибка сети, лимита или
пустой репозиторий не валят сборку: блок рисуется с прочерком, а если
данные неполные и прошлая версия файла уже есть — она остаётся как была.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import svgkit as K  # noqa: E402
from svgkit import MONO, PALETTE, SANS, THEMES, esc, font_css, mono_width, svg_doc, text_width, wrap  # noqa: E402

# ---------------------------------------------------------------- настройки

USER = (os.environ.get("GH_USER") or "ilovevodkaa").strip()
TOKEN = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
CACHE_DIR = os.environ.get("GH_CACHE_DIR") or ""
OFFLINE = os.environ.get("GH_OFFLINE", "").strip().lower() in ("1", "true", "yes")
API = "https://api.github.com"
GEN = K.GEN

# Чьи коммиты считать в статистике. Пусто — все коммиты в своих репо.
# Часть коммитов автора привязана GitHub'ом к другому аккаунту (по почте
# из git config), поэтому фильтр author=<login> сильно занижал бы число.
# Можно задать через env: GH_COMMIT_AUTHORS="login1,login2".
COMMIT_AUTHORS = tuple(a.strip() for a in os.environ.get("GH_COMMIT_AUTHORS", "").split(",") if a.strip())

# Избранные проекты: порядок = порядок карточек. Описание своё (оно точнее
# API), звёзды, дата и язык — из API; lang — запасной, если API отдал null.
FEATURED = [
    {
        "repo": "Telegradus",
        "title": "Telegradus",
        "kind": "клиент telegram",
        "desc": "Нативный клиент Telegram на Rust для Linux и Windows: без Electron, "
                "GPU-отрисовка, режим призрака, мультиаккаунт.",
        "lang": "Rust",
        "status": "в разработке",
    },
    {
        "repo": "Pax-Pixelia",
        "title": "Pax Pixelia",
        "kind": "стратегия",
        "desc": "Пиксельная стратегия в реальном времени на Godot 4 и C#: "
                "от кочевого рода у костра до колоний в космосе.",
        "lang": "C#",
        "status": "ранняя разработка",
    },
    {
        "repo": "Twilight-of-the-Union",
        "title": "Twilight of the Union",
        "kind": "мод hoi4",
        "desc": "Мод для Hearts of Iron IV о закате СССР: старт 1 января 1990, "
                "157 стран, интерфейс позднесоветского эфира.",
        "lang": "Python",
        "status": "v0.1",
    },
    {
        "repo": "margyt-screentime",
        "title": "margyt-screentime",
        "kind": "плагин",
        "desc": "Плагин для MargyT: считает минуты в TikTok за день, "
                "ставит дневной лимит, напоминает и ведёт статистику за неделю.",
        "lang": "Java",
        "status": "без телеметрии",
    },
]

ACTIVITY_ROWS = 7

# Язык -> заливка. Одно соответствие для карточек и статистики.
LANG_KIND = {
    "C#": "solid",
    "Python": "hatch",
    "Rust": "dots",
    "Java": "grid",
    "GLSL": "lines",
    "GDShader": "lines",
    "HTML": "outline",
}
REST = "rest"  # «другое» в статистике


def lang_kind(lang):
    return LANG_KIND.get(lang or "", "outline")


def warn(msg):
    # в Actions — аннотация ::warning::, видна на странице прогона
    prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") == "true" else ""
    print(f"{prefix}build.py: {msg}", file=sys.stderr)


# ---------------------------------------------------------------- GitHub API

def cache_stem(path):
    """Имя файла кеша: путь без ведущего '/', '/'->'__', '?'->'_q_', '&'->'_and_', '='->'-'."""
    return (path.lstrip("/").replace("/", "__").replace("?", "_q_")
            .replace("&", "_and_").replace("=", "-"))


class Resp:
    """status: ok | empty (409, пустой репо) | missing (404) | error (сеть, лимит, мусор)."""

    def __init__(self, status, body=None, link=None):
        self.status, self.body, self.link = status, body, link

    @property
    def ok(self):
        return self.status == "ok"


class GitHub:
    def __init__(self):
        self.cache = Path(CACHE_DIR) if CACHE_DIR else None
        self.live = 0
        self.blocked = OFFLINE  # после исчерпания лимита сеть больше не трогаем

    # кеш: обычный ответ лежит в <stem>.json (тело как есть);
    # если нужен заголовок Link — в <stem>.link.json обёрткой {"_link", "body"}
    def _cache_read(self, stem, want_link):
        if not self.cache:
            return None
        if want_link:
            f = self.cache / f"{stem}.link.json"
            if f.is_file():
                try:
                    d = json.loads(f.read_text(encoding="utf-8"))
                    return Resp("ok", d.get("body"), d.get("_link"))
                except (OSError, ValueError) as e:
                    warn(f"битый кеш {f.name}: {e}")
        f = self.cache / f"{stem}.json"
        if f.is_file():
            try:
                return Resp("ok", json.loads(f.read_text(encoding="utf-8")), None)
            except (OSError, ValueError) as e:
                warn(f"битый кеш {f.name}: {e}")
        return None

    def _cache_write(self, stem, resp, want_link):
        if not self.cache:
            return
        try:
            self.cache.mkdir(parents=True, exist_ok=True)
            if want_link:
                data = {"_link": resp.link, "body": resp.body}
                (self.cache / f"{stem}.link.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                (self.cache / f"{stem}.json").write_text(
                    json.dumps(resp.body, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            warn(f"не удалось записать кеш {stem}: {e}")

    def get(self, path, want_link=False):
        stem = cache_stem(path)
        hit = self._cache_read(stem, want_link)
        if hit is not None:
            return hit
        if self.blocked:
            return Resp("error")
        resp = self._fetch(path)
        if resp.ok:
            self._cache_write(stem, resp, want_link)
        return resp

    def _fetch(self, path):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"{USER}-profile-build",
        }
        if TOKEN:
            headers["Authorization"] = f"Bearer {TOKEN}"
        url = API + "/" + path.lstrip("/")
        for attempt in (1, 2):
            self.live += 1
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=20) as r:
                    body = json.loads(r.read().decode("utf-8"))
                    return Resp("ok", body, r.headers.get("Link"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    warn(f"404 {path}")
                    return Resp("missing")
                if e.code == 409:  # «Git Repository is empty»
                    return Resp("empty")
                remaining = e.headers.get("X-RateLimit-Remaining") if e.headers else None
                if e.code in (403, 429) and (remaining == "0" or e.code == 429):
                    warn(f"лимит API исчерпан на {path} — дальше только кеш")
                    self.blocked = True
                    return Resp("error")
                if e.code >= 500 and attempt == 1:
                    time.sleep(2)
                    continue
                warn(f"HTTP {e.code} {path}")
                return Resp("error")
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
                if attempt == 1:
                    time.sleep(2)
                    continue
                warn(f"сеть: {path}: {e}")
                return Resp("error")
        return Resp("error")


def last_page(link):
    """Номер страницы из rel="last" заголовка Link, иначе None."""
    for part in (link or "").split(","):
        if 'rel="last"' in part:
            m = re.search(r"[?&]page=(\d+)", part)
            if m:
                return int(m.group(1))
    return None


def parse_dt(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- сбор данных

def collect(gh, now):
    data = {"errors": set()}  # имена блоков, где данные неполные из-за ошибок

    u = gh.get(f"users/{USER}")
    data["user"] = u.body if u.ok and isinstance(u.body, dict) else None
    if not data["user"]:
        data["errors"].add("stats")

    repos, page = [], 1
    while True:
        q = f"users/{USER}/repos?per_page=100&type=owner&sort=pushed" + (f"&page={page}" if page > 1 else "")
        r = gh.get(q)
        if not r.ok or not isinstance(r.body, list):
            if page == 1:
                data["repos"] = None
                return data
            data["errors"].add("stats")
            break
        repos.extend(x for x in r.body if isinstance(x, dict))
        if len(r.body) < 100 or page >= 10:
            break
        page += 1
    data["repos"] = repos

    own = [r for r in repos
           if not r.get("fork") and str(r.get("name", "")).lower() != USER.lower()
           and str((r.get("owner") or {}).get("login", USER)).lower() == USER.lower()]
    own.sort(key=lambda r: r["name"].lower())
    data["own"] = own

    # языки по байтам
    langs = {}
    for r in own:
        res = gh.get(f"repos/{USER}/{r['name']}/languages")
        if res.ok and isinstance(res.body, dict):
            for k, v in res.body.items():
                if isinstance(v, int) and v > 0:
                    langs[k] = langs.get(k, 0) + v
        elif res.status == "error":
            data["errors"].add("stats")
    data["langs"] = langs

    # коммиты: per_page=1 + Link rel="last" = общее число
    total, any_count = 0, False
    authors = COMMIT_AUTHORS or (None,)
    for r in own:
        for a in authors:
            q = f"repos/{USER}/{r['name']}/commits?per_page=1" + (f"&author={a}" if a else "")
            res = gh.get(q, want_link=True)
            if res.ok:
                n = last_page(res.link)
                if n is None:
                    n = len(res.body) if isinstance(res.body, list) else 0
                total += n
                any_count = True
            elif res.status in ("empty", "missing"):
                any_count = True
            else:
                data["errors"].add("stats")
    data["commits_total"] = total if any_count else None

    # лента: свежие не-merge коммиты по всем своим репо
    feed = []
    by_push = sorted(own, key=lambda r: (r.get("pushed_at") or "", r["name"]), reverse=True)
    for r in by_push:
        pushed = parse_dt(r.get("pushed_at"))
        if len(feed) >= ACTIVITY_ROWS and pushed and pushed < feed[ACTIVITY_ROWS - 1]["date"]:
            break  # в этом и следующих репо всё старше уже набранного
        res = gh.get(f"repos/{USER}/{r['name']}/commits?per_page=10")
        if res.status == "error":
            data["errors"].add("activity")
            continue
        if not res.ok or not isinstance(res.body, list):
            continue
        for c in res.body:
            if not isinstance(c, dict) or len(c.get("parents") or []) > 1:
                continue
            cm = c.get("commit") or {}
            date = parse_dt((cm.get("committer") or {}).get("date")) or parse_dt((cm.get("author") or {}).get("date"))
            msg = (cm.get("message") or "").strip().splitlines()
            if not date or not c.get("sha"):
                continue
            feed.append({"sha": c["sha"][:7], "repo": r["name"], "msg": msg[0].strip() if msg else "", "date": date})
        feed.sort(key=lambda x: (-x["date"].timestamp(), x["repo"].lower(), x["sha"]))
    data["feed"] = feed[:ACTIVITY_ROWS]
    return data


# ---------------------------------------------------------------- русские формы

def plural(n, forms):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def _span(dt, now):
    s = max((now - dt).total_seconds(), 0)
    m, h, d = int(s // 60), int(s // 3600), int(s // 86400)
    if s < 60:
        return "now", 0
    if m < 60:
        return "min", m
    if h < 24:
        return "h", h
    if d < 14:
        return "d", d
    if d < 60:
        return "w", d // 7
    if d < 365:
        return "mo", d // 30
    return "y", d // 365


LONG = {
    "min": ("минуту", "минуты", "минут"),
    "h": ("час", "часа", "часов"),
    "d": ("день", "дня", "дней"),
    "w": ("неделю", "недели", "недель"),
    "mo": ("месяц", "месяца", "месяцев"),
    "y": ("год", "года", "лет"),
}
SHORT = {"min": "мин", "h": "ч", "d": "д", "w": "нед", "mo": "мес", "y": "г"}


def ago(dt, now):
    """«только что», «1 час назад», «3 дня назад», «2 недели назад», «5 месяцев назад»."""
    unit, n = _span(dt, now)
    if unit == "now":
        return "только что"
    return f"{n} {plural(n, LONG[unit])} назад"


def ago_short(dt, now):
    """«сейчас», «12 мин», «3 ч», «2 д», «1 нед», «4 мес», «1 г»."""
    unit, n = _span(dt, now)
    return "сейчас" if unit == "now" else f"{n} {SHORT[unit]}"


def fmt_int(n):
    if n is None:
        return "—"
    s = f"{int(n):,}".replace(",", " ")
    return s


def fmt_pct(p):
    if p < 0.1:
        return "<0,1%"
    return f"{p:.1f}".replace(".", ",") + "%"


# ---------------------------------------------------------------- SVG-примитивы

def n2(v):
    """Короткая запись числа для атрибутов."""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def plate(w, h, pal):
    return (f'<rect x="0.5" y="0.5" width="{n2(w - 1)}" height="{n2(h - 1)}" rx="10" '
            f'fill="{pal["bg"]}" stroke="{pal["line"]}" stroke-width="1"/>')


def pattern_defs(prefix, pal, kinds):
    """<pattern> для языковых заливок. Плитка 4px, якорь — начало координат."""
    ink, bg = pal["ink"], pal["bg"]
    out = []
    for k in sorted(set(kinds)):
        pid = f"{prefix}-{k}"
        head = f'<pattern id="{pid}" width="4" height="4" patternUnits="userSpaceOnUse"'
        base = f'<rect width="4" height="4" fill="{bg}"/>'
        if k == "hatch":
            out.append(f'{head} patternTransform="rotate(45)">{base}'
                       f'<rect x="0" y="0" width="1.6" height="4" fill="{ink}"/></pattern>')
        elif k == "dots":
            out.append(f'{head}>{base}<circle cx="2" cy="2" r="1.05" fill="{ink}"/></pattern>')
        elif k == "grid":
            out.append(f'{head}>{base}<rect x="0" y="0" width="4" height="1" fill="{ink}"/>'
                       f'<rect x="0" y="0" width="1" height="4" fill="{ink}"/></pattern>')
        elif k == "lines":
            out.append(f'{head}>{base}<rect x="0" y="1.5" width="4" height="1.3" fill="{ink}"/></pattern>')
    return "".join(out)


def kind_fill(kind, prefix, pal):
    if kind == "solid":
        return pal["ink"]
    if kind == REST:
        return pal["muted"]
    if kind == "outline":
        return pal["bg"]
    return f"url(#{prefix}-{kind})"


def swatch(x, y, w, h, kind, prefix, pal, rx=0):
    """Прямоугольник с языковой заливкой; у узорных — рамка ink внутрь."""
    fill = kind_fill(kind, prefix, pal)
    r = f' rx="{n2(rx)}"' if rx else ""
    if kind in ("solid", REST):
        return f'<rect x="{n2(x)}" y="{n2(y)}" width="{n2(w)}" height="{n2(h)}"{r} fill="{fill}"/>'
    return (f'<rect x="{n2(x + .5)}" y="{n2(y + .5)}" width="{n2(max(w - 1, .5))}" height="{n2(h - 1)}"{r} '
            f'fill="{fill}" stroke="{pal["ink"]}" stroke-width="1"/>')


def star(x, y, size, fill):
    """Пятиконечная звезда в квадрате size x size с левым верхним углом (x, y)."""
    cx, cy, ro = x + size / 2, y + size / 2 + size * 0.04, size / 2
    ri = ro * 0.47
    pts = []
    for i in range(10):
        r = ro if i % 2 == 0 else ri
        a = math.radians(-90 + i * 36)
        pts.append(f"{n2(cx + r * math.cos(a))},{n2(cy + r * math.sin(a))}")
    return f'<polygon points="{" ".join(pts)}" fill="{fill}"/>'


def fit(text, max_w, measure):
    """Обрезать с «…», чтобы measure(text) <= max_w."""
    if measure(text) <= max_w:
        return text
    while text and measure(text + "…") > max_w:
        text = text[:-1]
    return text.rstrip(" ,.;:—-") + "…"


_MONO_CPS = None


def mono_safe(s):
    """Оставить только символы, которые есть в JetBrains Mono: иначе фолбэк-шрифт
    сломает моноширинные колонки (эмодзи в сообщениях коммитов и т.п.)."""
    global _MONO_CPS
    if _MONO_CPS is None:
        try:
            m = json.loads((K.FONTS / "metrics.json").read_text(encoding="utf-8"))
            _MONO_CPS = {int(c) for c in m["JetBrainsMono-Regular"]["adv"]}
        except (OSError, ValueError, KeyError):
            _MONO_CPS = set(range(32, 127))
    out = "".join(ch if ord(ch) in _MONO_CPS else ("" if ord(ch) > 0x2000 else " ") for ch in s)
    return re.sub(r"\s+", " ", out).strip()


def css_text(pal, extra=""):
    return (f".mono{{font-family:{MONO};font-weight:400}}"
            f".sans{{font-family:{SANS}}}"
            f".ink{{fill:{pal['ink']}}}.txt{{fill:{pal['text']}}}.mut{{fill:{pal['muted']}}}" + extra)


# ---------------------------------------------------------------- карточка проекта

CW, CH, CP = 492, 160, 20


def render_card(idx, cfg, info, theme, now):
    pal = PALETTE[theme]
    pre = f"c{idx}"
    lang = (info or {}).get("language") or cfg.get("lang") or "—"
    stars = (info or {}).get("stargazers_count")
    pushed = parse_dt((info or {}).get("pushed_at"))
    kind = lang_kind(lang)

    body = [plate(CW, CH, pal), f"<defs>{pattern_defs(pre, pal, [kind])}</defs>"]

    # мета: «01 · клиент telegram» и стрелка
    body.append(f'<text x="{CP}" y="34" class="mono" font-size="12">'
                f'<tspan class="txt">{idx:02d}</tspan><tspan class="mut"> · {esc(cfg["kind"])}</tspan></text>')
    body.append(f'<text x="{CW - CP}" y="35" class="mono mut" font-size="15" text-anchor="end">↗</text>')

    # заголовок
    title = fit(cfg["title"], CW - 2 * CP - 24, lambda s: text_width(s, "Inter Tight", 600, 20))
    body.append(f'<text x="{CP}" y="64" class="sans ink" font-size="20" font-weight="600">{esc(title)}</text>')

    # описание, максимум 2 строки
    lines = wrap(cfg["desc"], CW - 2 * CP, "Inter Tight", 500, 13.5, max_lines=2)
    for i, ln in enumerate(lines):
        body.append(f'<text x="{CP}" y="{88 + i * 19}" class="sans txt" font-size="13.5" '
                    f'font-weight="500">{esc(ln)}</text>')

    # низ: язык · звёзды · обновлено ........ [статус]
    by = 140  # базовая линия нижней строки
    pill_txt = cfg.get("status") or ""
    pill_w = mono_width(pill_txt, 11) + 18 if pill_txt else 0
    pill_x = CW - CP - pill_w
    right_limit = (pill_x - 14) if pill_txt else CW - CP

    x = CP
    body.append(swatch(x, by - 9.5, 10, 10, kind, pre, pal))
    x += 16
    lang_s = fit(lang, 120, lambda s: mono_width(s, 12))
    body.append(f'<text x="{n2(x)}" y="{by}" class="mono txt" font-size="12">{esc(lang_s)}</text>')
    x += mono_width(lang_s, 12) + 18

    body.append(star(x, by - 10.5, 11, pal["muted"]))
    x += 15
    st = fmt_int(stars) if stars is not None else "—"
    body.append(f'<text x="{n2(x)}" y="{by}" class="mono mut" font-size="12">{esc(st)}</text>')
    x += mono_width(st, 12) + 18

    if pushed:
        for cand in (f"обн. {ago(pushed, now)}", ago(pushed, now), f"обн. {ago_short(pushed, now)}", ago_short(pushed, now)):
            if x + mono_width(cand, 12) <= right_limit:
                body.append(f'<text x="{n2(x)}" y="{by}" class="mono mut" font-size="12">{esc(cand)}</text>')
                break

    if pill_txt:
        body.append(f'<rect x="{n2(pill_x + .5)}" y="{by - 14.5}" width="{n2(pill_w - 1)}" height="21" rx="4" '
                    f'fill="none" stroke="{pal["line"]}" stroke-width="1"/>')
        body.append(f'<text x="{n2(pill_x + pill_w / 2)}" y="{by - .5}" class="mono mut" font-size="11" '
                    f'text-anchor="middle">{esc(pill_txt)}</text>')

    css = font_css(("Inter Tight", 500), ("Inter Tight", 600), ("JetBrains Mono", 400)) + css_text(pal)
    meta = [lang]
    if stars is not None:
        meta.append(f"{stars} {plural(stars, ('звезда', 'звезды', 'звёзд'))}")
    if pushed:
        meta.append(f"обновлён {ago(pushed, now)}")
    if pill_txt:
        meta.append(pill_txt)
    return svg_doc(CW, CH, "".join(body), f"{cfg['title']} — {cfg['kind']}",
                   css, desc=f"{cfg['desc']} ({', '.join(meta)})")


# ---------------------------------------------------------------- статистика

SW, SH = 1000, 240


def lang_rows(langs, top=5):
    items = sorted(langs.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    total = sum(v for _, v in items)
    if not total:
        return []
    rows = [(k, v / total * 100, lang_kind(k)) for k, v in items[:top]]
    rest = sum(v for _, v in items[top:])
    if rest:
        rows.append(("другое", rest / total * 100, REST))
    return rows


def bar_widths(pcts, total_w, gap, min_w=3.0):
    """Ширины сегментов: пропорционально, но не уже min_w; излишек забираем у крупнейшего."""
    avail = total_w - gap * (len(pcts) - 1)
    ws = [max(p / 100 * avail, min_w) for p in pcts]
    over = sum(ws) - avail
    if ws and over:
        i = max(range(len(ws)), key=lambda j: ws[j])
        ws[i] -= over
    return ws


def render_stats(data, theme, now):
    pal = PALETTE[theme]
    pre = "s"
    user = data.get("user") or {}
    own = data.get("own") or []
    repos = data.get("repos") or []

    created = parse_dt(user.get("created_at"))
    days = (now.date() - created.date()).days if created else None
    public = user.get("public_repos") if isinstance(user.get("public_repos"), int) else (len(repos) if repos else None)
    stars = sum(int(r.get("stargazers_count") or 0) for r in repos if not r.get("fork")) if repos else None
    commits = data.get("commits_total")

    nums = [
        (public, ("публичный репо", "публичных репо", "публичных репо")),
        (stars, ("звезда", "звезды", "звёзд")),
        (commits, ("коммит", "коммита", "коммитов")),
        (days, ("день на github", "дня на github", "дней на github")),
    ]
    rows = lang_rows(data.get("langs") or {})

    b = [plate(SW, SH, pal)]
    b.append(f"<defs>{pattern_defs(pre, pal, [k for _, _, k in rows])}</defs>")

    # шапки колонок
    b.append(f'<text x="24" y="40" class="mono mut" font-size="12">( github )</text>')
    b.append(f'<text x="524" y="40" class="mono mut" font-size="12">( языки )</text>')
    b.append(f'<text x="976" y="40" class="mono mut" font-size="11" text-anchor="end">'
             f'обновлено {now:%d.%m.%Y}</text>')
    b.append(f'<rect x="500" y="24" width="1" height="{SH - 48}" fill="{pal["line"]}"/>')

    # 2x2 крупных числа
    cells = [(24, 104), (262, 104), (24, 190), (262, 190)]
    for (x, y), (val, forms) in zip(cells, nums):
        label = plural(val, forms) if val is not None else forms[2]
        b.append(f'<g class="num"><text x="{x}" y="{y}" class="sans ink" font-size="44" font-weight="800" '
                 f'letter-spacing="-1">{esc(fmt_int(val))}</text>'
                 f'<text x="{x + 1}" y="{y + 22}" class="mono mut" font-size="12">{esc(label)}</text></g>')

    # полоса языков
    bx, by, bw, bh, gap = 524, 62, 452, 22, 3
    if rows:
        ws = bar_widths([p for _, p, _ in rows], bw, gap)
        x = bx
        segs = []
        for (name, p, kind), w in zip(rows, ws):
            segs.append(swatch(x, by, w, bh, kind, pre, pal, rx=2))
            x += w + gap
        b.append(f'<g class="bar">{"".join(segs)}</g>')
        # легенда: 2 колонки по 3 строки
        for i, (name, p, kind) in enumerate(rows):
            col, row = divmod(i, 3)
            lx = bx + col * 238
            ly = by + bh + 40 + row * 30
            nm = fit(name, 150, lambda s: mono_width(s, 13))
            b.append(swatch(lx, ly - 10, 10, 10, kind, pre, pal))
            b.append(f'<text x="{lx + 18}" y="{ly}" class="mono txt" font-size="13">{esc(nm)}</text>')
            b.append(f'<text x="{lx + 214}" y="{ly}" class="mono mut" font-size="13" '
                     f'text-anchor="end">{esc(fmt_pct(p))}</text>')
    else:
        b.append(f'<rect x="{bx + .5}" y="{by + .5}" width="{bw - 1}" height="{bh - 1}" rx="2" fill="none" '
                 f'stroke="{pal["line"]}" stroke-dasharray="3 3"/>')
        b.append(f'<text x="{bx}" y="{by + bh + 40}" class="mono mut" font-size="13">—</text>')

    anim = ("@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}"
            "@keyframes up{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}"
            ".bar{transform-box:fill-box;transform-origin:0 50%;animation:grow 1.1s cubic-bezier(.2,.7,.2,1) .15s both}"
            ".num{animation:up .6s ease-out both}"
            "@media (prefers-reduced-motion: reduce){*{animation:none!important}}")
    css = font_css(("Inter Tight", 800), ("JetBrains Mono", 400)) + css_text(pal, anim)
    desc_parts = [f"{fmt_int(v)} {plural(v, f) if v is not None else f[2]}" for v, f in nums]
    desc_parts += [f"{n} {fmt_pct(p)}" for n, p, _ in rows]
    return svg_doc(SW, SH, "".join(b), "Статистика GitHub", css, desc="; ".join(desc_parts))


# ---------------------------------------------------------------- лента коммитов

AW = 1000
AFS = 13            # кегль
ACW = 0.6 * AFS     # ширина символа JetBrains Mono
AX = 24             # левый край текста
COL_SHA, COL_REPO, COL_MSG = 2, 11, 34   # колонки в символах от AX
REPO_CH, AGE_CH = 21, 6


def render_activity(feed, theme, now):
    pal = PALETTE[theme]
    top = 40
    cmd_y = top + 34
    row0 = cmd_y + 30
    step = 26
    n = len(feed)
    last_y = row0 + max(n - 1, 0) * step
    prompt_y = last_y + 32
    H = prompt_y + 26

    col = lambda c: AX + c * ACW  # noqa: E731
    age_right = AW - AX
    msg_max = (age_right - (AGE_CH + 2) * ACW) - col(COL_MSG)

    b = [plate(AW, H, pal)]
    # заголовок окна
    for i in range(3):
        b.append(f'<circle cx="{AX + 6 + i * 18}" cy="20" r="5" fill="none" stroke="{pal["muted"]}" stroke-width="1"/>')
    b.append(f'<text x="{AW // 2}" y="24" class="mono mut" font-size="12" text-anchor="middle">'
             f'~/{esc(USER)} — git log</text>')
    b.append(f'<rect x="1" y="{top}" width="{AW - 2}" height="1" fill="{pal["line"]}"/>')

    # команда
    b.append(f'<g class="r" style="animation-delay:.05s"><text x="{AX}" y="{cmd_y}" class="mono" font-size="{AFS}">'
             f'<tspan class="mut">$</tspan><tspan class="ink" x="{n2(col(COL_SHA))}">git log --oneline '
             f'--no-merges -{ACTIVITY_ROWS}</tspan></text></g>')

    gx = AX + ACW / 2
    if n:
        if n > 1:
            b.append(f'<rect x="{n2(gx - .5)}" y="{row0 - 4.5}" width="1" height="{(n - 1) * step}" '
                     f'fill="{pal["muted"]}" fill-opacity=".45"/>')
        for i, c in enumerate(feed):
            y = row0 + i * step
            repo = fit(mono_safe(c["repo"]), REPO_CH * ACW, lambda s: mono_width(s, AFS))
            msg = fit(mono_safe(c["msg"]) or "—", msg_max, lambda s: mono_width(s, AFS))
            age = ago_short(c["date"], now)
            dot_fill = pal["ink"] if i == 0 else pal["bg"]
            b.append(
                f'<g class="r" style="animation-delay:{n2(.15 + i * .09)}s">'
                f'<circle cx="{n2(gx)}" cy="{y - 4.5}" r="3.5" fill="{dot_fill}" stroke="{pal["ink"]}" stroke-width="1.2"/>'
                f'<text y="{y}" class="mono" font-size="{AFS}">'
                f'<tspan x="{n2(col(COL_SHA))}" class="mut">{esc(c["sha"])}</tspan>'
                f'<tspan x="{n2(col(COL_REPO))}" class="ink">{esc(repo)}</tspan>'
                f'<tspan x="{n2(col(COL_MSG))}" class="txt">{esc(msg)}</tspan>'
                f'<tspan x="{age_right}" class="mut" text-anchor="end">{esc(age)}</tspan>'
                f"</text></g>")
    else:
        b.append(f'<text x="{n2(col(COL_SHA))}" y="{row0}" class="mono mut" font-size="{AFS}">'
                 f'пока пусто</text>')

    # приглашение и мигающий курсор «_» — как на сайте автора
    b.append(f'<g class="r" style="animation-delay:{n2(.25 + n * .09)}s">'
             f'<text x="{AX}" y="{prompt_y}" class="mono mut" font-size="{AFS}">$</text>'
             f'<rect class="cur" x="{n2(col(COL_SHA))}" y="{prompt_y - 1}" width="{n2(ACW + 1)}" height="3" '
             f'fill="{pal["ink"]}"/></g>')

    anim = ("@keyframes fi{from{opacity:0}to{opacity:1}}"
            "@keyframes blink{0%,49%{opacity:1}50%,100%{opacity:0}}"
            ".r{animation:fi .45s ease-out both}"
            ".cur{animation:blink 1.1s steps(1) infinite}"
            "@media (prefers-reduced-motion: reduce){*{animation:none!important}}")
    css = font_css(("JetBrains Mono", 400)) + css_text(pal, anim)
    desc = "; ".join(f"{c['repo']}: {c['msg']} ({ago(c['date'], now)})" for c in feed) or "пока пусто"
    return svg_doc(AW, H, "".join(b), "Последние коммиты", css, desc=desc)


# ---------------------------------------------------------------- сборка

def save(name, content):
    path = GEN / name
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    K.write(path, content)
    return True


def existing(name):
    return (GEN / name).is_file()


def main():
    raw_now = os.environ.get("BUILD_NOW")
    now = parse_dt(raw_now) if raw_now else None
    now = now or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)

    gh = GitHub()
    data = collect(gh, now)
    have_old = GEN.is_dir() and any(GEN.glob("*.svg"))

    if data.get("repos") is None:
        if have_old:
            warn("нет данных GitHub (сеть/лимит/кеш) — оставляю прежние assets/gen")
            return 0
        warn("нет данных GitHub и прежних файлов — рисую заглушки с прочерками")
        data.update(repos=[], own=[], langs={}, commits_total=None, feed=[])
        data["errors"] = set()

    by_name = {r["name"].lower(): r for r in data["repos"]}
    changed = []

    for i, cfg in enumerate(FEATURED, 1):
        info = by_name.get(cfg["repo"].lower())
        if info is None and data["repos"]:
            warn(f"репо {cfg['repo']} не найден в списке")
        for theme in THEMES:
            name = f"card-{cfg['repo']}-{theme}.svg"
            if info is None and existing(name):
                continue
            if save(name, render_card(i, cfg, info, theme, now)):
                changed.append(name)

    for kind, render, arg in (("stats", render_stats, data), ("activity", render_activity, data["feed"])):
        for theme in THEMES:
            name = f"{kind}-{theme}.svg"
            if kind in data["errors"] and existing(name):
                warn(f"{kind}: данные неполные — оставляю прежний {name}")
                continue
            if save(name, render(arg, theme, now)):
                changed.append(name)

    for name in sorted(changed):
        size = (GEN / name).stat().st_size
        print(f"  {name}  {size // 1024} КБ")
    print(f"build.py: изменено файлов: {len(changed)}, живых запросов к API: {gh.live}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # сборка профиля не должна падать из-за мелочи
        import traceback
        traceback.print_exc()
        warn(f"непредвиденная ошибка: {e} — прежние файлы не тронуты")
        sys.exit(0)
