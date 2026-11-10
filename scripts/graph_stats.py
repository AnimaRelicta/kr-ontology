#!/usr/bin/env python3
"""
КР1, задание 4: статистика собранного графа + проверка, что он описан терминами своей онтологии.

Сам граф (graph.ttl) строит scripts/rs100_anthology.py (выгрузка из Wikidata -> turtle).
Этот скрипт независимо ЧИТАЕТ сохранённый файл через rdflib и считает цифры для отчёта:
  - число троек (граф отдельно, онтология отдельно);
  - число сущностей: сущности Wikidata (есть owl:sameAs) и служебные узлы (награды/браки/подписчики);
  - разбивка по классам (прямые типы и с учётом иерархии) и по свойствам (троек, субъектов);
  - проверки: у каждой сущности есть owl:sameAs на Wikidata; все классы/свойства графа определены в онтологии;
    в графе нет «чужих» классов и свойств.
Запуск:  uv run scripts/graph_stats.py [--graph anthology/graph.ttl] [--onto anthology/ontology.ttl] [--out reports]
Нужен: rdflib pandas. Результат: reports/graph_stats.md, graph_stats_classes.csv, graph_stats_properties.csv
"""
import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd
from rdflib import Graph, URIRef, Literal, RDF, RDFS, OWL

import runlog

NS = "http://example.org/rs100/onto#"
RES = "http://example.org/rs100/res/"
WD = "http://www.wikidata.org/entity/"
SERVICE = {"AwardReceipt", "Marriage", "FollowersSnapshot"}   # узлы-факты: в Wikidata своего QID не имеют
MAIN = "Musician"


def local(u):
    s = str(u)
    return s.split("#")[-1] if "#" in s else s.rsplit("/", 1)[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="anthology/graph.ttl")
    ap.add_argument("--onto", default="anthology/ontology.ttl")
    ap.add_argument("--out", default="reports")
    a = ap.parse_args()
    g, o = Graph(), Graph()
    g.parse(a.graph, format="turtle")
    o.parse(a.onto, format="turtle")
    n_g, n_o = len(g), len(o)
    print(f"Граф данных: {n_g} троек; онтология: {n_o} троек")

    # --- из онтологии
    o_classes = {c for c in o.subjects(RDF.type, OWL.Class) if isinstance(c, URIRef)}
    o_obj = set(o.subjects(RDF.type, OWL.ObjectProperty))
    o_data = set(o.subjects(RDF.type, OWL.DatatypeProperty))
    parent = defaultdict(set)
    for c, p in o.subject_objects(RDFS.subClassOf):
        parent[c].add(p)

    def ancestors(c, seen=None):
        seen = seen or set()
        for p in parent.get(c, ()):
            if p not in seen:
                seen.add(p)
                ancestors(p, seen)
        return seen

    # --- сущности
    typed = defaultdict(set)
    for s, c in g.subject_objects(RDF.type):
        typed[s].add(c)
    same = {s: [x for x in g.objects(s, OWL.sameAs)] for s in typed}
    service_cls = {URIRef(NS + c) for c in SERVICE}
    service = {s for s, cs in typed.items() if cs & service_cls}
    wd_entities = {s for s in typed if s not in service}
    no_same = sorted(local(s) for s in wd_entities if not same.get(s))
    bad_same = sorted(local(s) for s in wd_entities for x in same.get(s, [])
                      if not (str(x).startswith(WD) and local(x) == local(s)))
    main_ents = {s for s, cs in typed.items() if URIRef(NS + MAIN) in cs}

    # --- классы
    direct = Counter(c for cs in typed.values() for c in cs)
    inferred = Counter()
    for cs in typed.values():
        allc = set(cs)
        for c in cs:
            allc |= ancestors(c)
        inferred.update(allc)
    cls_rows = [{"class": local(c), "in_ontology": c in o_classes, "direct_instances": direct.get(c, 0),
                 "with_subclasses": inferred.get(c, 0)}
                for c in sorted(set(direct) | set(inferred), key=lambda c: -inferred.get(c, 0))]
    cdf = pd.DataFrame(cls_rows)

    # --- свойства
    trip, subj = Counter(), defaultdict(set)
    for s, p, _ in g:
        trip[p] += 1
        subj[p].add(s)
    def kind(p):
        if p in o_obj: return "object"
        if p in o_data: return "datatype"
        if p == RDF.type: return "rdf:type"
        if p in (OWL.sameAs, RDFS.label): return "стандартное (owl/rdfs)"
        return "НЕ В ОНТОЛОГИИ"
    prow = []
    for p, n in trip.most_common():
        r = {"property": (local(p) if str(p).startswith(NS) else g.namespace_manager.normalizeUri(p)),
             "kind": kind(p), "triples": n, "subjects": len(subj[p])}
        r["musicians_with"] = len(subj[p] & main_ents)
        r["share_of_musicians"] = round(r["musicians_with"] / len(main_ents), 2) if main_ents else 0
        prow.append(r)
    pdf = pd.DataFrame(prow)

    # --- проверки словаря
    foreign_cls = sorted(str(c) for c in direct if c not in o_classes)
    foreign_pr = sorted(str(p) for p in trip if kind(p) == "НЕ В ОНТОЛОГИИ")
    wd_types = sorted(str(c) for c in direct if str(c).startswith(WD))
    checks = [
        ("у каждой сущности Wikidata есть owl:sameAs", not no_same, f"без sameAs: {', '.join(no_same[:10])}" if no_same else ""),
        ("sameAs ведёт на wd:<тот же QID>", not bad_same, ", ".join(bad_same[:10])),
        ("все классы графа определены в онтологии", not foreign_cls, "; ".join(foreign_cls)),
        ("все свойства графа определены в онтологии (или owl/rdf/rdfs)", not foreign_pr, "; ".join(foreign_pr)),
        ("в графе нет классов Wikidata в rdf:type", not wd_types, "; ".join(wd_types)),
    ]
    ok_all = all(c[1] for c in checks)

    # --- отчёт
    n_obj = int(pdf[pdf.kind == "object"].triples.sum()); n_dat = int(pdf[pdf.kind == "datatype"].triples.sum())
    md = ["# Статистика собранного графа (задание 4)", "",
          f"Файлы: `{a.graph}` (данные), `{a.onto}` (онтология).", "",
          "## Общие цифры", "",
          f"- Троек в графе данных: **{n_g}** (онтология отдельно: {n_o}; вместе: {n_g + n_o})",
          f"- Узлов с типом (сущностей всего): **{len(typed)}**",
          f"  - сущностей Wikidata (имеют owl:sameAs): **{len(wd_entities)}**, из них основной класс {MAIN}: **{len(main_ents)}**",
          f"  - служебных узлов-фактов (AwardReceipt, Marriage, FollowersSnapshot): **{len(service)}** — у них нет собственного QID, "
          "поэтому owl:sameAs не задаётся; они однозначно привязаны к музыканту (hasAwardReceipt, hasMarriage, hasFollowers)",
          f"- Различных классов: **{len(direct)}**; различных предикатов: **{len(trip)}**",
          f"- Троек с объектными свойствами: {n_obj}; с datatype-свойствами: {n_dat}", "",
          "## Проверки", "", "| проверка | результат | детали |", "|---|---|---|",
          *[f"| {n} | {'выполнено' if ok else 'НАРУШЕНО'} | {d} |" for n, ok, d in checks], "",
          "## Разбивка по классам", "",
          "«Прямые» — по rdf:type в графе; «с подклассами» — с учётом иерархии онтологии (Musician считается и Person, и Agent).", "",
          "| класс | в онтологии | прямых экземпляров | с учётом подклассов |", "|---|---|---|---|",
          *[f"| {r['class']} | {'да' if r['in_ontology'] else 'НЕТ'} | {r['direct_instances']} | {r['with_subclasses']} |" for r in cls_rows], "",
          "## Разбивка по свойствам", "",
          f"Заполненность — среди {len(main_ents)} музыкантов.", "",
          "| свойство | вид | троек | субъектов | музыкантов с ним | доля |", "|---|---|---|---|---|---|",
          *[f"| {r['property']} | {r['kind']} | {r['triples']} | {r['subjects']} | {r['musicians_with']} | {r['share_of_musicians']:.0%} |" for r in prow], ""]
    out = Path(a.out); out.mkdir(exist_ok=True)
    (out / "graph_stats.md").write_text("\n".join(md), encoding="utf-8")
    cdf.to_csv(out / "graph_stats_classes.csv", index=False, encoding="utf-8")
    pdf.to_csv(out / "graph_stats_properties.csv", index=False, encoding="utf-8")

    print(f"Сущностей с типом: {len(typed)} (Wikidata: {len(wd_entities)}, музыкантов: {len(main_ents)}, служебных: {len(service)})")
    print(f"Классов: {len(direct)}, предикатов: {len(trip)}")
    print("\nКлассы:\n" + cdf.to_string(index=False))
    print("\nСвойства:\n" + pdf.to_string(index=False))
    print()
    for n, ok, d in checks:
        print(f"[{'OK' if ok else 'НАРУШЕНО'}] {n}" + (f": {d}" if d else ""))
    print("ИТОГ:", "граф корректно описан терминами онтологии" if ok_all else "есть нарушения")
    return 0 if ok_all else 1


if __name__ == "__main__":
    with runlog.session(__file__, inputs=["anthology/graph.ttl", "anthology/ontology.ttl"],
                        outputs=[f"reports/{n}" for n in ("graph_stats.md", "graph_stats_classes.csv", "graph_stats_properties.csv")]):
        sys.exit(main())
