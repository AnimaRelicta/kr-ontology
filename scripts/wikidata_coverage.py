#!/usr/bin/env python3
"""
КР1, задание 1: проверка домена на требования, SPARQL-запросами к Wikidata.

Что делает (всё по списку сущностей из anthology/domain.csv):
  1. Считает сущности основного класса (Q5) в домене           -> требование 30-60.
  2. Для каждого свойства считает долю сущностей, у которых оно заполнено,
     и для каждой сущности - сколько свойств заполнено          -> требование «>=5 свойств, среди них даты и числа».
  3. Находит свойства, у утверждений которых есть квалификатор времени (дата) -> требование «хотя бы одно».
  4. Собирает таблицу «сущность -> ссылка на источник текста» из corpus.jsonl -> требование «текст по большинству».
Результаты: reports/wikidata_*.csv, reports/text_sources.csv, reports/domain_check.md
(в domain_check.md есть тексты SPARQL-запросов - их можно вставить в отчёт).

Запуск:  uv run scripts/wikidata_coverage.py [--domain anthology/domain.csv] [--corpus anthology/corpus.jsonl]
                                              [--out reports] [--discover]
--discover  искать квалификаторы времени у ВСЕХ свойств сущностей (запрос тяжелее, может упереться в тайм-аут).
Нужны: requests pandas
"""
import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

import runlog

SPARQL_URL = "https://query.wikidata.org/sparql"
WD_API = "https://www.wikidata.org/w/api.php"
HEAD = {"User-Agent": "kr1-rs100-anthology/1.0 (student project; vova2026.12345@gmail.com)",
        "Accept": "application/sparql-results+json"}

# свойства онтологии, которые берутся из Wikidata: pid -> (название, тип значения)
PROPS = {
    "P19": ("место рождения", "объект"), "P27": ("гражданство", "объект"), "P106": ("род занятий", "объект"),
    "P136": ("жанр", "объект"), "P264": ("лейбл", "объект"), "P463": ("член организации", "объект"),
    "P1303": ("инструмент", "объект"), "P166": ("награда", "объект"), "P26": ("супруг(а)", "объект"),
    "P569": ("дата рождения", "дата"), "P570": ("дата смерти", "дата"),
    "P2031": ("начало активности", "дата"), "P2032": ("конец активности", "дата"),
    "P2048": ("рост", "число"), "P1971": ("число детей", "число"), "P8687": ("число подписчиков", "число"),
    "P856": ("официальный сайт", "ссылка"),
}
DATE_PROPS = [p for p, (_, t) in PROPS.items() if t == "дата"]
NUM_PROPS = [p for p, (_, t) in PROPS.items() if t == "число"]
MIN_PROPS = 5
DOMAIN_MIN, DOMAIN_MAX = 30, 60


def sparql(query):
    """POST-запрос к WDQS с повторами. Возвращает список строк-словарей {переменная: значение}."""
    last = None
    for attempt in range(4):
        try:
            r = requests.post(SPARQL_URL, data={"query": query}, headers=HEAD, timeout=120)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", 10)))
                last = "HTTP 429"
                continue
            r.raise_for_status()
            return [{k: v["value"] for k, v in b.items()} for b in r.json()["results"]["bindings"]]
        except requests.RequestException as e:
            last = repr(e)
            print(f"  [попытка {attempt + 1}] WDQS: {last}")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"WDQS не ответил: {last}")


def wd_labels(pids):
    """Русские/английские названия свойств Wikidata."""
    out = {}
    pids = sorted(set(pids))
    for i in range(0, len(pids), 50):
        try:
            r = requests.get(WD_API, params={"action": "wbgetentities", "ids": "|".join(pids[i:i + 50]),
                                             "props": "labels", "languages": "ru|en", "format": "json"},
                             headers=HEAD, timeout=60)
            for pid, e in r.json().get("entities", {}).items():
                lab = e.get("labels", {})
                out[pid] = (lab.get("ru") or lab.get("en") or {}).get("value", pid)
        except Exception as ex:
            print("  не удалось получить названия свойств:", ex)
    return out


def values(qids):
    return " ".join(f"wd:{q}" for q in qids)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="anthology/domain.csv")
    ap.add_argument("--corpus", default="anthology/corpus.jsonl")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--discover", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(exist_ok=True)

    dom = pd.read_csv(a.domain)
    qids = list(dict.fromkeys(dom["qid"]))
    n_dom = len(qids)
    print(f"В домене (domain.csv): {n_dom} сущностей")
    V = values(qids)
    PR = " ".join(f"wdt:{p}" for p in PROPS)
    queries = {}

    # ---- 1. класс
    queries["1. Сущности основного класса (Q5)"] = (
        f"SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE {{ VALUES ?s {{ {V} }} ?s wdt:P31 wd:Q5 . }}")
    n_h = int(sparql(queries["1. Сущности основного класса (Q5)"])[0]["n"])
    print(f"Из них людей (Q5) в Wikidata: {n_h}")

    # ---- 2. заполненность свойств
    queries["2. Какие свойства заполнены у каких сущностей"] = (
        f"SELECT DISTINCT ?s ?p WHERE {{ VALUES ?s {{ {V} }} VALUES ?p {{ {PR} }} ?s ?p ?o . }}")
    rows = sparql(queries["2. Какие свойства заполнены у каких сущностей"])
    filled = {q: set() for q in qids}
    for r in rows:
        q = r["s"].rsplit("/", 1)[-1]
        p = r["p"].rsplit("/", 1)[-1]
        if q in filled:
            filled[q].add(p)
    cov = [{"property": p, "name": PROPS[p][0], "type": PROPS[p][1],
            "n_filled": sum(p in s for s in filled.values()),
            "share": round(sum(p in s for s in filled.values()) / n_dom, 3)} for p in PROPS]
    cov_df = pd.DataFrame(cov).sort_values("share", ascending=False)
    cov_df.to_csv(out / "wikidata_coverage.csv", index=False, encoding="utf-8")

    names = dict(zip(dom["qid"], dom["title"]))
    ent = pd.DataFrame([{"qid": q, "title": names.get(q, ""), "n_props": len(s),
                         "has_date": bool(s & set(DATE_PROPS)), "has_number": bool(s & set(NUM_PROPS)),
                         "props": " ".join(sorted(s))} for q, s in filled.items()])
    ent.to_csv(out / "wikidata_entity_props.csv", index=False, encoding="utf-8")
    ge5 = int((ent["n_props"] >= MIN_PROPS).sum())
    both = int(((ent["n_props"] >= MIN_PROPS) & ent["has_date"] & ent["has_number"]).sum())

    # ---- 3. квалификаторы времени
    if a.discover:
        pfilter = ""
    else:
        pv = " ".join(f"p:{p}" for p in PROPS)
        pfilter = f"VALUES ?p {{ {pv} }} "
    qkey = "3. Свойства с квалификатором времени"
    queries[qkey] = (
        "SELECT ?p ?pq (COUNT(DISTINCT ?s) AS ?entities) (COUNT(?st) AS ?statements) WHERE {\n"
        f"  VALUES ?s {{ {V} }} {pfilter}\n"
        "  ?s ?p ?st . ?st ?pq ?v .\n"
        '  FILTER(STRSTARTS(STR(?p), "http://www.wikidata.org/prop/P")\n'
        '         && STRSTARTS(STR(?pq), "http://www.wikidata.org/prop/qualifier/P")\n'
        "         && DATATYPE(?v) = <http://www.w3.org/2001/XMLSchema#dateTime>)\n"
        "} GROUP BY ?p ?pq ORDER BY DESC(?entities)")
    qrows = sparql(queries[qkey])
    lab = wd_labels([r["p"].rsplit("/", 1)[-1] for r in qrows] + [r["pq"].rsplit("/", 1)[-1] for r in qrows])
    qdf = pd.DataFrame([{"property": r["p"].rsplit("/", 1)[-1],
                         "property_name": lab.get(r["p"].rsplit("/", 1)[-1], ""),
                         "time_qualifier": r["pq"].rsplit("/", 1)[-1],
                         "qualifier_name": lab.get(r["pq"].rsplit("/", 1)[-1], ""),
                         "entities": int(r["entities"]), "statements": int(r["statements"]),
                         "share_of_domain": round(int(r["entities"]) / n_dom, 3)} for r in qrows])
    qdf.to_csv(out / "wikidata_time_qualifiers.csv", index=False, encoding="utf-8")

    # ---- 4. источники текста
    docs = {}
    with open(a.corpus, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            docs.setdefault(d["qid"], []).append(d)
    src = []
    for q in qids:
        ds = docs.get(q, [])
        src.append({"qid": q, "entity": (ds[0]["entity"] if ds else names.get(q, "")),
                    "n_docs": len(ds), "sources": " | ".join(f"{d['lang']}: {d['url']}" for d in ds),
                    "chars": sum(d["n_chars"] for d in ds)})
    src_df = pd.DataFrame(src)
    src_df.to_csv(out / "text_sources.csv", index=False, encoding="utf-8")
    with_text = int((src_df["n_docs"] > 0).sum())

    # ---- итоговый отчёт
    def ok(c): return "выполнено" if c else "НЕ выполнено"
    md = ["# Проверка домена на требования КР1", "",
          f"Домен: исполнители-люди из списка Rolling Stone 100. Сущностей в domain.csv: **{n_dom}**.", "",
          "## Итог по требованиям", "",
          "| требование | факт | вывод |", "|---|---|---|",
          f"| 30-60 сущностей одного основного класса (Q5) | {n_h} из {n_dom} - люди | {ok(DOMAIN_MIN <= n_h <= DOMAIN_MAX)} |",
          f"| у большинства >= {MIN_PROPS} заполненных свойств | {ge5} из {n_dom} ({ge5 / n_dom:.0%}) | {ok(ge5 > n_dom / 2)} |",
          f"| среди них есть даты и числа | дата и число и >= {MIN_PROPS} свойств: {both} из {n_dom} ({both / n_dom:.0%}) | {ok(both > n_dom / 2)} |",
          f"| хотя бы одно свойство с квалификатором времени | найдено свойств: {qdf['property'].nunique() if len(qdf) else 0} | {ok(len(qdf) > 0)} |",
          f"| текстовое описание по большинству сущностей | {with_text} из {n_dom} ({with_text / n_dom:.0%}) | {ok(with_text > n_dom / 2)} |",
          "", "## Заполненность свойств (доля сущностей домена)", "",
          "| свойство | название | тип | заполнено | доля |", "|---|---|---|---|---|",
          *[f"| {r.property} | {r.name} | {r.type} | {r.n_filled} | {r.share:.0%} |" for r in cov_df.itertuples()],
          "", f"Сущностей с >= {MIN_PROPS} свойствами: {ge5} из {n_dom}. Распределение числа заполненных свойств: "
          f"min {ent['n_props'].min()}, медиана {ent['n_props'].median():.0f}, max {ent['n_props'].max()}.",
          "", "## Свойства с квалификатором времени", ""]
    if len(qdf):
        md += ["| свойство | квалификатор времени | сущностей | утверждений |", "|---|---|---|---|",
               *[f"| {r.property} {r.property_name} | {r.time_qualifier} {r.qualifier_name} | {r.entities} ({r.share_of_domain:.0%}) | {r.statements} |"
                 for r in qdf.itertuples()]]
    else:
        md += ["Не найдено."]
    md += ["", "## Источники текстов", "",
           f"Документов в corpus.jsonl: {sum(len(v) for v in docs.values())}; сущностей с текстом: {with_text} из {n_dom}. "
           "Полная таблица «сущность - ссылка» - в text_sources.csv.", "",
           "## SPARQL-запросы (endpoint https://query.wikidata.org/sparql)", ""]
    for k, q in queries.items():
        md += [f"### {k}", "```sparql", q if len(q) < 1500 else q.replace(V, "<список QID из domain.csv>"), "```", ""]
    md += ["В запросах `VALUES ?s` содержит QID всех сущностей домена (из anthology/domain.csv)."]
    (out / "domain_check.md").write_text("\n".join(md), encoding="utf-8")

    print(f"\nЛюдей (Q5): {n_h}; >= {MIN_PROPS} свойств: {ge5}/{n_dom}; с датой и числом: {both}/{n_dom}; "
          f"свойств с квалификатором времени: {qdf['property'].nunique() if len(qdf) else 0}; с текстом: {with_text}/{n_dom}")
    print(cov_df.to_string(index=False))
    if len(qdf):
        print("\nКвалификаторы времени:\n" + qdf.to_string(index=False))
    print(f"\nФайлы в {out.resolve()}: domain_check.md, wikidata_coverage.csv, wikidata_entity_props.csv, "
          "wikidata_time_qualifiers.csv, text_sources.csv")
    return 0


if __name__ == "__main__":
    with runlog.session(__file__, inputs=["anthology/domain.csv", "anthology/corpus.jsonl"],
                        outputs=[f"reports/{n}" for n in ("domain_check.md", "wikidata_coverage.csv", "wikidata_entity_props.csv",
                                                           "wikidata_time_qualifiers.csv", "text_sources.csv")]):
        sys.exit(main())
