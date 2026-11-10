#!/usr/bin/env python3
"""
Антология: «Исполнители (люди) из списка 100 величайших по версии Rolling Stone».

Один файл делает всё для пунктов 1, 4 и 5 КР1:
  1. Берёт список 100 исполнителей из ru.wikipedia (две колонки: 1-50 и 51-100).
     Если рядом лежит rs100_manual.csv (колонка title) - использует его.
  2. Находит QID, скачивает сущности Wikidata (кэш rs100_cache/), оставляет людей (Q5).
  3. Строит СВОЮ онтологию (anthology/ontology.ttl) и граф данных (anthology/graph.ttl)
     в собственном пространстве имён; у каждой сущности owl:sameAs на Wikidata.
  4. Скачивает тексты статей Википедии (ru и en) -> anthology/corpus.jsonl.
  5. Считает статистику (тройки, сущности, классы, свойства, покрытие текстами)
     -> anthology/stats.md, stats_classes.csv, stats_properties.csv, domain.csv.

Запуск:
    uv run scripts/rs100_anthology.py            (нужны: requests pandas beautifulsoup4; rdflib - по желанию, для самопроверки)
    uv run scripts/rs100_anthology.py --no-text  (без скачивания текстов)
    uv run scripts/rs100_anthology.py --out my_dir

Кэш (rs100_cache/) переиспользуется: повторный запуск не ходит в сеть за тем, что уже скачано.
"""
import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote, unquote

import pandas as pd
import requests
from bs4 import BeautifulSoup

import runlog

# ============================ НАСТРОЙКИ ============================
PAGE_TITLE = "100 величайших исполнителей всех времён по версии журнала Rolling Stone"
EXPECTED = 100
DOMAIN_MIN, DOMAIN_MAX = 30, 60
RU_API = "https://ru.wikipedia.org/w/api.php"
EN_API = "https://en.wikipedia.org/w/api.php"
WD_API = "https://www.wikidata.org/w/api.php"
UA = "kr1-rs100-anthology/1.0 (student project; vova2026.12345@gmail.com)"
HEAD = {"User-Agent": UA}

CACHE_DIR = Path("rs100_cache")
TEXT_CACHE = CACHE_DIR / "texts"
LABELS_FILE = CACHE_DIR / "labels2.json"      # {qid: {"ru": "...", "en": "..."}}
MANUAL_FILE = Path("rs100_manual.csv")

ONT = "http://example.org/rs100/onto#"        # ПРОСТРАНСТВО ИМЁН ОНТОЛОГИИ (замените на своё при желании)
RES = "http://example.org/rs100/res/"         # пространство имён данных
WD = "http://www.wikidata.org/entity/"
WDT = "http://www.wikidata.org/prop/direct/"

# ============================ ОНТОЛОГИЯ (единый источник правды) ============================
# (имя, родитель, label_en, label_ru, comment_en, comment_ru, wikidata-эквивалент)
CLASSES = [
    ("Agent", None, "Agent", "Агент", "A person or an organisation.", "Человек или организация.", None),
    ("Person", "Agent", "Person", "Человек", "A human being.", "Человек.", "Q5"),
    ("Musician", "Person", "Musician", "Музыкант", "A person from the Rolling Stone list of greatest artists (domain class).",
     "Человек из списка величайших исполнителей Rolling Stone (основной класс домена).", None),
    ("Organization", "Agent", "Organization", "Организация", "An organised group of people.", "Организованная группа людей.", None),
    ("MusicalGroup", "Organization", "Musical group", "Музыкальная группа", "A band or other musical ensemble.",
     "Музыкальная группа или ансамбль.", "Q215380"),
    ("RecordLabel", "Organization", "Record label", "Звукозаписывающий лейбл", "A company that releases recordings.",
     "Компания, выпускающая музыкальные записи.", "Q18127"),
    ("Place", None, "Place", "Место", "A geographic location, e.g. a city.", "Географический объект, например город.", "Q17334923"),
    ("Country", "Place", "Country", "Страна", "A sovereign state.", "Государство.", "Q6256"),
    ("Genre", None, "Music genre", "Музыкальный жанр", "A musical genre.", "Музыкальный жанр.", "Q188451"),
    ("Occupation", None, "Occupation", "Род занятий", "A profession or occupation.", "Профессия или род занятий.", "Q12737077"),
    ("Instrument", None, "Musical instrument", "Музыкальный инструмент", "An instrument a musician plays.",
     "Инструмент, на котором играет музыкант.", "Q34379"),
    ("Award", None, "Award", "Награда", "An award or prize.", "Награда или премия.", "Q618779"),
    ("AwardReceipt", None, "Award receipt", "Получение награды", "A fact that a musician received an award (with a year).",
     "Факт получения музыкантом награды (с годом).", None),
    ("Marriage", None, "Marriage", "Брак", "A marriage of a person (with optional start and end dates).",
     "Брак человека (с необязательными датами начала и конца).", None),
    ("FollowersSnapshot", None, "Followers snapshot", "Число подписчиков на дату",
     "Number of social-media followers at a point in time.", "Число подписчиков в соцсети на определённую дату.", None),
]

XSD = "http://www.w3.org/2001/XMLSchema#"
# (имя, тип obj/data, domain, range, functional, label_en, label_ru, comment_en, comment_ru, wikidata-свойство)
PROPS = [
    ("birthPlace", "obj", "Musician", "Place", True, "birth place", "место рождения", "Place where the person was born.", "Место, где родился человек.", "P19"),
    ("citizenship", "obj", "Person", "Country", False, "citizenship", "гражданство", "Country of citizenship.", "Страна гражданства.", "P27"),
    ("occupation", "obj", "Person", "Occupation", False, "occupation", "род занятий", "Occupation of the person.", "Род занятий человека.", "P106"),
    ("genre", "obj", "Musician", "Genre", False, "genre", "жанр", "Genre the musician works in.", "Жанр, в котором работает музыкант.", "P136"),
    ("recordLabel", "obj", "Musician", "RecordLabel", False, "record label", "лейбл", "Label the musician recorded for.", "Лейбл, на котором записывался музыкант.", "P264"),
    ("memberOf", "obj", "Musician", "Organization", False, "member of", "член организации/группы", "Organization or band the musician is or was a member of.", "Организация или группа, в которой состоит или состоял музыкант.", "P463"),
    ("playsInstrument", "obj", "Musician", "Instrument", False, "plays instrument", "играет на инструменте", "Instrument the musician plays.", "Инструмент, на котором играет музыкант.", "P1303"),
    ("hasAwardReceipt", "obj", "Musician", "AwardReceipt", False, "has award receipt", "имеет получение награды", "Links a musician to an award receipt.", "Связывает музыканта с фактом получения награды.", None),
    ("award", "obj", "AwardReceipt", "Award", True, "award", "награда", "The award that was received.", "Полученная награда.", None),
    ("hasMarriage", "obj", "Person", "Marriage", False, "has marriage", "имеет брак", "Links a person to a marriage.", "Связывает человека с браком.", None),
    ("spouse", "obj", "Marriage", "Person", True, "spouse", "супруг(а)", "The partner in the marriage.", "Партнёр в браке.", None),
    ("hasFollowers", "obj", "Musician", "FollowersSnapshot", False, "has followers snapshot", "имеет данные о подписчиках", "Links a musician to a followers count at a date.", "Связывает музыканта с числом подписчиков на дату.", None),
    ("birthDate", "data", "Person", "date", True, "birth date", "дата рождения", "Date of birth.", "Дата рождения.", "P569"),
    ("deathDate", "data", "Person", "date", True, "death date", "дата смерти", "Date of death.", "Дата смерти.", "P570"),
    ("activeFromYear", "data", "Musician", "gYear", False, "active from (year)", "активен с (год)", "Year the career started.", "Год начала карьеры.", "P2031"),
    ("activeUntilYear", "data", "Musician", "gYear", False, "active until (year)", "активен до (год)", "Year the career ended.", "Год окончания карьеры.", "P2032"),
    ("heightCm", "data", "Person", "decimal", True, "height (cm)", "рост (см)", "Height in centimetres.", "Рост в сантиметрах.", "P2048"),
    ("childrenCount", "data", "Person", "integer", True, "number of children", "число детей", "Number of children.", "Число детей.", "P1971"),
    ("awardYear", "data", "AwardReceipt", "gYear", False, "award year", "год награды", "Year the award was received.", "Год получения награды.", None),
    ("marriageStart", "data", "Marriage", "date", False, "marriage start", "начало брака", "Start date of the marriage.", "Дата начала брака.", None),
    ("marriageEnd", "data", "Marriage", "date", False, "marriage end", "конец брака", "End date of the marriage.", "Дата окончания брака.", None),
    ("followersCount", "data", "FollowersSnapshot", "integer", True, "followers count", "число подписчиков", "Number of followers.", "Число подписчиков.", "P8687"),
    ("followersDate", "data", "FollowersSnapshot", "date", True, "followers date", "дата измерения подписчиков", "Date the followers count was measured.", "Дата, на которую измерено число подписчиков.", None),
    ("officialWebsite", "data", "Agent", "anyURI", False, "official website", "официальный сайт", "Official website URL.", "Адрес официального сайта.", "P856"),
    ("wikipediaEn", "data", "Agent", "anyURI", True, "English Wikipedia", "статья в англ. Википедии", "URL of the English Wikipedia article.", "Ссылка на статью в английской Википедии.", None),
    ("wikipediaRu", "data", "Agent", "anyURI", True, "Russian Wikipedia", "статья в рус. Википедии", "URL of the Russian Wikipedia article.", "Ссылка на статью в русской Википедии.", None),
    ("rsRank", "data", "Musician", "integer", True, "Rolling Stone rank", "место в списке Rolling Stone", "Rank in the Rolling Stone list (1-100).", "Место в списке Rolling Stone (1-100).", None),
]
PROP = {p[0]: p for p in PROPS}


# ============================ СЕТЬ ============================
def get(url, params):
    last = None
    for attempt in range(5):
        try:
            r = requests.get(url, params={**params, "format": "json"}, headers=HEAD, timeout=60)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", 5 * (attempt + 1))))
                last = "HTTP 429"
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last = repr(e)
            print(f"  [попытка {attempt + 1}] {url}: {last}")
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Не отвечает {url}: {last}")


def chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ============================ 1. СПИСОК ИЗ СТАТЬИ ============================
def _article_title(href):
    if "/wiki/" not in href:
        return None
    t = href.split("/wiki/", 1)[1]
    if ":" in t or "#" in t or "redlink" in href:
        return None
    return unquote(t).replace("_", " ")


def extract_list():
    data = get(RU_API, {"action": "parse", "page": PAGE_TITLE, "prop": "text", "redirects": 1, "formatversion": 2})
    soup = BeautifulSoup(data["parse"]["text"], "html.parser")
    found = {}
    # две колонки: слева <ol> (места 1-50), справа «51. <a>..</a><br>52. ...»
    for td in soup.select("table.ts-col-begin td"):
        m = re.search(r"Места\s+(\d+)", td.get_text(" ", strip=True))
        start = int(m.group(1)) if m else 1
        ol = td.find("ol")
        if ol:
            for i, li in enumerate(ol.find_all("li", recursive=False)):
                for a in li.find_all("a", href=True):
                    t = _article_title(a["href"])
                    if t:
                        found.setdefault(start + i, t)
                        break
        else:
            cur = None
            for node in td.descendants:
                if isinstance(node, str):
                    mm = re.fullmatch(r"\s*(\d{1,3})\.\s*", node)
                    if mm:
                        cur = int(mm.group(1))
                elif node.name == "a" and cur is not None and node.get("href"):
                    t = _article_title(node["href"])
                    if t:
                        found.setdefault(cur, t)
                        cur = None
    df = pd.DataFrame([{"rank": r, "title": t} for r, t in sorted(found.items())])
    print(f"Статья: извлечено строк: {len(df)}")
    return df


def load_list():
    if MANUAL_FILE.exists():
        df = pd.read_csv(MANUAL_FILE)
        df["rank"] = range(1, len(df) + 1)
        print(f"Использую ручной список {MANUAL_FILE}: {len(df)} строк")
        return df[["rank", "title"]]
    df = extract_list()
    if len(df) != EXPECTED:
        print(f"ВНИМАНИЕ: ожидалось {EXPECTED}, получено {len(df)}. Положите рядом rs100_manual.csv (колонка title).")
    return df


# ============================ 2. WIKIDATA ============================
def titles_to_qids(titles):
    mapping = {}
    for ch in chunks(titles, 50):
        data = get(RU_API, {"action": "query", "prop": "pageprops", "ppprop": "wikibase_item",
                            "titles": "|".join(ch), "redirects": 1, "formatversion": 2})
        q = data.get("query", {})
        norm = {n["from"]: n["to"] for n in q.get("normalized", [])}
        redir = {r["from"]: r["to"] for r in q.get("redirects", [])}
        final = {p["title"]: p.get("pageprops", {}).get("wikibase_item") for p in q.get("pages", [])}
        for t in ch:
            t1 = norm.get(t, t)
            mapping[t] = final.get(redir.get(t1, t1))
    return mapping


def fetch_entities(ids):
    CACHE_DIR.mkdir(exist_ok=True)
    out, todo = {}, []
    for i in ids:
        f = CACHE_DIR / f"{i}.json"
        if f.exists():
            out[i] = json.loads(f.read_text(encoding="utf-8"))
        else:
            todo.append(i)
    for ch in chunks(todo, 50):
        data = get(WD_API, {"action": "wbgetentities", "ids": "|".join(ch),
                            "props": "labels|sitelinks|claims", "languages": "en|ru",
                            "sitefilter": "enwiki|ruwiki"})
        for i, ent in data.get("entities", {}).items():
            if "missing" in ent:
                continue
            (CACHE_DIR / f"{i}.json").write_text(json.dumps(ent, ensure_ascii=False), encoding="utf-8")
            out[i] = ent
        time.sleep(0.5)
    return out


def fetch_labels(ids):
    """Метки ru/en для любых QID (кэш в labels2.json)."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache = json.loads(LABELS_FILE.read_text(encoding="utf-8")) if LABELS_FILE.exists() else {}
    todo = [i for i in sorted(set(ids)) if i not in cache]
    for ch in chunks(todo, 50):
        data = get(WD_API, {"action": "wbgetentities", "ids": "|".join(ch), "props": "labels", "languages": "en|ru"})
        for i, ent in data.get("entities", {}).items():
            lab = ent.get("labels", {})
            cache[i] = {"ru": lab.get("ru", {}).get("value", ""), "en": lab.get("en", {}).get("value", "")}
        LABELS_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        time.sleep(0.5)
    return cache


# --- разбор утверждений
def stmts(e, pid):
    return [s for s in e.get("claims", {}).get(pid, [])
            if s.get("rank") != "deprecated" and s.get("mainsnak", {}).get("snaktype") == "value"]


def val(st):
    return st["mainsnak"]["datavalue"]["value"]


def item_id(st):
    v = val(st)
    return v["id"] if isinstance(v, dict) and "id" in v else None


def qual_vals(st, pid):
    return [q["datavalue"]["value"] for q in st.get("qualifiers", {}).get(pid, [])
            if q.get("snaktype") == "value" and "datavalue" in q]


def parse_time(v):
    """-> (год, месяц, день, точность) или None."""
    m = re.match(r"^([+-])(\d+)-(\d\d)-(\d\d)T", v.get("time", ""))
    if not m:
        return None
    y = int(m.group(2)) * (1 if m.group(1) == "+" else -1)
    return y, int(m.group(3)), int(m.group(4)), int(v.get("precision", 0))


def to_date(v):
    """xsd:date только при точности «день»; иначе None (не выдумываем день и месяц)."""
    t = parse_time(v)
    if not t or t[3] < 11 or not (1 <= t[0] <= 9999) or t[1] == 0 or t[2] == 0:
        return None
    return f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d}"


def to_year(v):
    t = parse_time(v)
    if not t or t[3] < 9 or not (1 <= t[0] <= 9999):
        return None
    return f"{t[0]:04d}"


def to_height_cm(v):
    try:
        amount = float(v["amount"])
    except (KeyError, ValueError):
        return None
    unit = v.get("unit", "").rsplit("/", 1)[-1]
    factor = {"Q174728": 1.0, "Q11573": 100.0, "Q218593": 2.54, "Q3710": 30.48}.get(unit)   # см, м, дюйм, фут
    return None if factor is None else round(amount * factor, 1)


def to_int(v):
    try:
        return int(float(v["amount"]))
    except (KeyError, ValueError):
        return None


# ============================ TURTLE-WRITER (без rdflib) ============================
def esc(s):
    return (s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))


def lit(value, dt=None, lang=None):
    if lang:
        return f'"{esc(str(value))}"@{lang}'
    if dt:
        return f'"{esc(str(value))}"^^xsd:{dt}'
    return f'"{esc(str(value))}"'


class TTL:
    PREFIXES = {"": ONT, "res": RES, "wd": WD, "wdt": WDT, "owl": "http://www.w3.org/2002/07/owl#",
                "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
                "xsd": XSD}

    def __init__(self):
        self.triples = set()

    def add(self, s, p, o):
        self.triples.add((s, p, o))

    def dump(self, path, header=""):
        by_s = {}
        for s, p, o in self.triples:
            by_s.setdefault(s, {}).setdefault(p, []).append(o)
        lines = [f"@prefix {k}: <{v}> ." for k, v in self.PREFIXES.items()]
        if header:
            lines.insert(0, header)
        lines.append("")
        for s in sorted(by_s):
            preds = by_s[s]
            order = sorted(preds, key=lambda p: (p != "a", p))
            parts = [f"    {p} " + ", ".join(sorted(preds[p])) for p in order]
            lines.append(f"{s}\n" + " ;\n".join(parts) + " .\n")
        Path(path).write_text("\n".join(lines), encoding="utf-8")


def build_ontology():
    g = TTL()
    g.add("<" + ONT.rstrip("#") + ">", "a", "owl:Ontology")
    g.add("<" + ONT.rstrip("#") + ">", "rdfs:label", lit("RS100 musicians ontology", lang="en"))
    g.add("<" + ONT.rstrip("#") + ">", "rdfs:label", lit("Онтология музыкантов RS100", lang="ru"))
    for name, parent, len_, lru, cen, cru, wdq in CLASSES:
        c = f":{name}"
        g.add(c, "a", "owl:Class")
        if parent:
            g.add(c, "rdfs:subClassOf", f":{parent}")
        g.add(c, "rdfs:label", lit(len_, lang="en")); g.add(c, "rdfs:label", lit(lru, lang="ru"))
        g.add(c, "rdfs:comment", lit(cen, lang="en")); g.add(c, "rdfs:comment", lit(cru, lang="ru"))
        if wdq:
            g.add(c, "owl:equivalentClass", f"wd:{wdq}")
    for name, kind, dom, rng, func, len_, lru, cen, cru, wdp in PROPS:
        p = f":{name}"
        g.add(p, "a", "owl:ObjectProperty" if kind == "obj" else "owl:DatatypeProperty")
        if func:
            g.add(p, "a", "owl:FunctionalProperty")
        g.add(p, "rdfs:domain", f":{dom}")
        g.add(p, "rdfs:range", f":{rng}" if kind == "obj" else f"xsd:{rng}")
        g.add(p, "rdfs:label", lit(len_, lang="en")); g.add(p, "rdfs:label", lit(lru, lang="ru"))
        g.add(p, "rdfs:comment", lit(cen, lang="en")); g.add(p, "rdfs:comment", lit(cru, lang="ru"))
        if wdp:
            g.add(p, "owl:equivalentProperty", f"wdt:{wdp}")
    return g


# ============================ 3. ГРАФ ДАННЫХ ============================
def build_graph(domain, ents, labels):
    """domain: DataFrame(rank,title,qid). Возвращает (TTL, types{qid: set(классов)}, предупреждения)."""
    g = TTL()
    types = {}
    warns = Counter()
    domain_ids = set(domain["qid"])

    def uri(q):
        return f"res:{q}"

    def mark(q, cls):
        types.setdefault(q, set()).add(cls)

    def emit_entity(q):
        """sameAs + метки + классы для любой сущности."""
        g.add(uri(q), "owl:sameAs", f"wd:{q}")
        lab = labels.get(q, {})
        for lg in ("ru", "en"):
            if lab.get(lg):
                g.add(uri(q), "rdfs:label", lit(lab[lg], lang=lg))
        if not lab.get("ru") and not lab.get("en"):
            warns["entity_without_label"] += 1

    def link(subj, prop, target_q, cls):
        g.add(subj, f":{prop}", uri(target_q))
        mark(target_q, cls)

    for r in domain.itertuples():
        q = r.qid
        e = ents[q]
        s = uri(q)
        mark(q, "Musician")
        g.add(s, ":rsRank", lit(int(r.rank), "integer"))
        sl = e.get("sitelinks", {})
        for site, prop, base in (("enwiki", "wikipediaEn", "https://en.wikipedia.org/wiki/"),
                                 ("ruwiki", "wikipediaRu", "https://ru.wikipedia.org/wiki/")):
            if site in sl:
                g.add(s, f":{prop}", lit(base + quote(sl[site]["title"].replace(" ", "_"), safe="()_,.-'!~*"), "anyURI"))

        # простые объектные свойства: (pid, свойство, класс значения)
        for pid, prop, cls in (("P19", "birthPlace", "Place"), ("P27", "citizenship", "Country"),
                               ("P106", "occupation", "Occupation"), ("P136", "genre", "Genre"),
                               ("P264", "recordLabel", "RecordLabel"), ("P463", "memberOf", "Organization"),
                               ("P1303", "playsInstrument", "Instrument")):
            for st in stmts(e, pid):
                t = item_id(st)
                if t:
                    link(s, prop, t, cls)
        # birthPlace функциональное: оставляем первое значение
        bp = sorted(o for (ss, p, o) in g.triples if ss == s and p == ":birthPlace")
        if len(bp) > 1:
            warns["multiple_birthPlace_trimmed"] += 1
            for o in bp[1:]:
                g.triples.discard((s, ":birthPlace", o))

        # даты и числа
        for pid, prop, conv, dt in (("P569", "birthDate", to_date, "date"), ("P570", "deathDate", to_date, "date"),
                                    ("P2031", "activeFromYear", to_year, "gYear"), ("P2032", "activeUntilYear", to_year, "gYear")):
            vals_ = []
            for st in stmts(e, pid):
                v = conv(val(st))
                if v is None:
                    warns[f"{prop}_imprecise_skipped"] += 1
                else:
                    vals_.append(v)
            if prop in ("birthDate", "deathDate"):
                vals_ = sorted(set(vals_))[:1]
            for v in sorted(set(vals_)):
                g.add(s, f":{prop}", lit(v, dt))
        for st in stmts(e, "P2048")[:1]:
            h = to_height_cm(val(st))
            if h is None:
                warns["height_unknown_unit"] += 1
            else:
                g.add(s, ":heightCm", lit(h, "decimal"))
        for st in stmts(e, "P1971")[:1]:
            n = to_int(val(st))
            if n is not None:
                g.add(s, ":childrenCount", lit(n, "integer"))
        for st in stmts(e, "P856")[:1]:
            g.add(s, ":officialWebsite", lit(val(st), "anyURI"))

        # награды (с годом)
        for i, st in enumerate(stmts(e, "P166"), 1):
            a = item_id(st)
            if not a:
                continue
            node = f"res:{q}_award_{i}"
            mark_node = types.setdefault(node, set()); mark_node.add("AwardReceipt")
            g.add(s, ":hasAwardReceipt", node)
            link(node, "award", a, "Award")
            for v in qual_vals(st, "P585")[:1]:
                y = to_year(v)
                if y:
                    g.add(node, ":awardYear", lit(y, "gYear"))
        # браки (с датами)
        for i, st in enumerate(stmts(e, "P26"), 1):
            sp = item_id(st)
            if not sp:
                continue
            node = f"res:{q}_marriage_{i}"
            types.setdefault(node, set()).add("Marriage")
            g.add(s, ":hasMarriage", node)
            link(node, "spouse", sp, "Person")
            for qp, prop in (("P580", "marriageStart"), ("P582", "marriageEnd")):
                for v in qual_vals(st, qp)[:1]:
                    d = to_date(v)
                    if d:
                        g.add(node, f":{prop}", lit(d, "date"))
                    else:
                        warns[f"{prop}_imprecise_skipped"] += 1
        # подписчики (с датой измерения)
        for i, st in enumerate(stmts(e, "P8687"), 1):
            n = to_int(val(st))
            if n is None:
                continue
            node = f"res:{q}_followers_{i}"
            types.setdefault(node, set()).add("FollowersSnapshot")
            g.add(s, ":hasFollowers", node)
            g.add(node, ":followersCount", lit(n, "integer"))
            for v in qual_vals(st, "P585")[:1]:
                d = to_date(v)
                if d:
                    g.add(node, ":followersDate", lit(d, "date"))

    # типы и sameAs/метки для всех «настоящих» сущностей (кроме служебных узлов)
    for q, cs in types.items():
        if q.startswith("res:"):
            for c in cs:
                g.add(q, "a", f":{c}")
    real = {q for q in types if not q.startswith("res:")}
    for q in sorted(real | domain_ids):
        emit_entity(q)
        for c in types.get(q, set()):
            g.add(uri(q), "a", f":{c}")
        # родительский класс не выводим вручную - это работа owlrl на этапе 6
    return g, types, warns


# ============================ 4. ТЕКСТЫ ============================
def fetch_wiki_text(lang, title):
    TEXT_CACHE.mkdir(parents=True, exist_ok=True)
    f = TEXT_CACHE / f"{lang}_{re.sub(r'[^0-9A-Za-zА-Яа-я_-]', '_', title)}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    api = RU_API if lang == "ru" else EN_API
    data = get(api, {"action": "query", "prop": "extracts|info", "explaintext": 1, "inprop": "url",
                     "redirects": 1, "titles": title, "formatversion": 2})
    pages = data.get("query", {}).get("pages", [])
    page = pages[0] if pages else {}
    rec = {"title": page.get("title", title), "url": page.get("fullurl", ""), "text": (page.get("extract") or "").strip()}
    f.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    time.sleep(0.3)
    return rec


def build_corpus(domain, ents, labels, path):
    n_docs, with_text, missing = 0, set(), []
    with open(path, "w", encoding="utf-8") as out:
        for r in domain.itertuples():
            sl = ents[r.qid].get("sitelinks", {})
            name = labels.get(r.qid, {}).get("ru") or labels.get(r.qid, {}).get("en") or r.title
            for lang, site in (("ru", "ruwiki"), ("en", "enwiki")):
                if site not in sl:
                    continue
                try:
                    rec = fetch_wiki_text(lang, sl[site]["title"])
                except RuntimeError as e:
                    print(f"  текст {lang} {r.qid}: {e}")
                    continue
                if not rec["text"]:
                    continue
                doc = {"id": f"{r.qid}:{lang}wiki", "qid": r.qid, "entity": name, "source": "wikipedia", "lang": lang,
                       "title": rec["title"], "url": rec["url"], "license": "CC BY-SA 4.0",
                       "n_chars": len(rec["text"]), "text": rec["text"]}
                out.write(json.dumps(doc, ensure_ascii=False) + "\n")
                n_docs += 1
                with_text.add(r.qid)
            if r.qid not in with_text:
                missing.append(r.qid)
    return n_docs, with_text, missing


# ============================ 5. СТАТИСТИКА ============================
def write_stats(out_dir, g, types, domain, ents, warns, corpus_info, onto):
    triples = len(g.triples)
    ent_nodes = {q for q in types}                       # все узлы с типом
    real = {q for q in ent_nodes if not q.startswith("res:")}
    cls_counter = Counter(c for q, cs in types.items() for c in cs)
    pred_counter = Counter("rdf:type" if p == "a" else p for (_, p, _) in g.triples)
    pd.DataFrame(sorted(cls_counter.items(), key=lambda x: -x[1]), columns=["class", "n_instances"]).to_csv(
        out_dir / "stats_classes.csv", index=False)
    pd.DataFrame(sorted(pred_counter.items(), key=lambda x: -x[1]), columns=["predicate", "n_triples"]).to_csv(
        out_dir / "stats_properties.csv", index=False)

    # покрытие по свойствам среди домена
    n = len(domain)
    cov_rows = []
    for name, p in PROP.items():
        subj = {s for (s, pp, _) in g.triples if pp == f":{name}" and s.startswith("res:") and s[4:] in set(domain["qid"])}
        cov_rows.append({"property": name, "wikidata": p[9] or "", "n_filled": len(subj), "share": round(len(subj) / n, 2)})
    pd.DataFrame(cov_rows).to_csv(out_dir / "stats_domain_coverage.csv", index=False)

    n_docs, with_text, missing = corpus_info if corpus_info else (0, set(), list(domain["qid"]))
    md = [
        "# Статистика антологии", "",
        f"- Исполнителей в домене (люди, Q5): **{n}**",
        f"- Троек в графе данных: **{triples}** (онтология отдельно: {len(onto.triples)})",
        f"- Узлов с типом: **{len(ent_nodes)}**, из них сущностей Wikidata (с owl:sameAs): **{len(real)}**, "
        f"служебных узлов (награды/браки/подписчики): {len(ent_nodes) - len(real)}",
        f"- Текстовых документов: **{n_docs}**, сущностей с текстом: **{len(with_text)}** из {n}", "",
        "## Разбивка по классам", "", "| класс | экземпляров |", "|---|---|",
        *[f"| {c} | {k} |" for c, k in cls_counter.most_common()], "",
        "## Разбивка по свойствам (число троек)", "", "| свойство | троек |", "|---|---|",
        *[f"| {p} | {k} |" for p, k in pred_counter.most_common()], "",
        "## Предупреждения при сборке", "",
        *( [f"- {k}: {v}" for k, v in warns.items()] or ["- нет"] ),
    ]
    if missing:
        md += ["", f"Без текста: {', '.join(missing)}"]
    (out_dir / "stats.md").write_text("\n".join(md), encoding="utf-8")
    return triples, len(real), cls_counter, pred_counter


def selfcheck_rdflib(path, expected):
    try:
        import rdflib
    except ImportError:
        print("rdflib не установлен - самопроверка Turtle пропущена (uv pip install rdflib).")
        return
    gg = rdflib.Graph()
    gg.parse(path, format="turtle")
    print(f"rdflib: {path.name} читается, троек {len(gg)} (мой подсчёт: {expected})",
          "OK" if len(gg) == expected else "<- РАСХОЖДЕНИЕ")


# ============================ MAIN ============================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="anthology")
    ap.add_argument("--no-text", action="store_true", help="не скачивать тексты")
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)

    lst = load_list()
    qmap = titles_to_qids(lst["title"].tolist())
    lst["qid"] = lst["title"].map(qmap)
    if lst["qid"].isna().any():
        print("Нет QID у страниц:", lst[lst["qid"].isna()]["title"].tolist())
    lst = lst.dropna(subset=["qid"])

    ents = fetch_entities(list(lst["qid"].unique()))
    is_h = lambda q: q in ents and any(
        s.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id") == "Q5" for s in ents[q].get("claims", {}).get("P31", []))
    domain = lst[lst["qid"].map(is_h)].reset_index(drop=True)
    print(f"Всего в списке {len(lst)}, людей (Q5): {len(domain)}")
    if not (DOMAIN_MIN <= len(domain) <= DOMAIN_MAX):
        print(f"ВНИМАНИЕ: в домене {len(domain)} сущностей, требование КР - {DOMAIN_MIN}-{DOMAIN_MAX}.")
    domain.to_csv(out_dir / "domain.csv", index=False)

    # метки: домен + всё, на что ссылаются утверждения
    need = set(domain["qid"])
    for q in domain["qid"]:
        e = ents[q]
        for pid in ("P19", "P27", "P106", "P136", "P264", "P463", "P1303", "P166", "P26"):
            need.update(t for t in (item_id(s) for s in stmts(e, pid)) if t)
    labels = fetch_labels(need)

    onto = build_ontology()
    onto.dump(out_dir / "ontology.ttl", "# Стартовая онтология (сгенерирована скриптом). Откройте в Protégé и доработайте.")
    g, types, warns = build_graph(domain, ents, labels)
    g.dump(out_dir / "graph.ttl", "# Граф данных антологии RS100 (люди). Онтология: ontology.ttl")

    corpus_info = None
    if not args.no_text:
        corpus_info = build_corpus(domain, ents, labels, out_dir / "corpus.jsonl")
    triples, n_ent, cls_c, pred_c = write_stats(out_dir, g, types, domain, ents, warns, corpus_info, onto)

    selfcheck_rdflib(out_dir / "graph.ttl", triples)
    selfcheck_rdflib(out_dir / "ontology.ttl", len(onto.triples))
    print(f"\nТроек: {triples}; сущностей Wikidata: {n_ent}; классов: {len(cls_c)}; свойств: {len(pred_c)}")
    if corpus_info:
        print(f"Тексты: документов {corpus_info[0]}, сущностей с текстом {len(corpus_info[1])}/{len(domain)}")
    print(f"Файлы в {out_dir.resolve()}: ontology.ttl, graph.ttl, corpus.jsonl, domain.csv, stats.md, stats_*.csv")


if __name__ == "__main__":
    _out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv[:-1] else Path("anthology")
    with runlog.session(__file__, inputs=[MANUAL_FILE],
                        outputs=[_out / n for n in ("ontology.ttl", "graph.ttl", "corpus.jsonl", "domain.csv",
                                                    "stats.md", "stats_classes.csv", "stats_properties.csv",
                                                    "stats_domain_coverage.csv")]):
        sys.exit(main())
