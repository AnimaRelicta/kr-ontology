#!/usr/bin/env python3
"""
КР1, задание 3: автоматическая проверка онтологии на требования + таблица «вопрос -> классы и свойства».
Запуск:  uv run scripts/ontology_check.py [--onto anthology/ontology.ttl] [--out reports]
Нужен: rdflib. Результат: reports/ontology_check.md (+ код возврата 1, если есть нарушения).
"""
import argparse
import sys
from pathlib import Path

from rdflib import Graph, URIRef, Literal, RDF, RDFS, OWL, XSD

import runlog

NS = "http://example.org/rs100/onto#"   # если сменили пространство имён - поменяйте здесь

# вопрос -> (классы, свойства), как в отчёте (раздел 3.3)
USAGE = {
    1: (["Musician"], ["birthDate"]),
    2: (["Musician"], ["deathDate"]),
    3: (["Musician", "AwardReceipt"], ["hasAwardReceipt"]),
    4: (["Musician", "Organization"], ["memberOf"]),
    5: (["Person", "Marriage"], ["hasMarriage", "spouse", "marriageStart", "marriageEnd"]),
    6: (["Musician"], ["heightCm"]),
    7: (["Musician"], ["wikipediaEn"]),
    8: (["Musician"], ["wikipediaEn"]),
    9: (["Musician"], ["wikipediaEn"]),
    10: (["Musician"], ["wikipediaEn"]),
    11: (["Musician"], ["birthDate", "deathDate", "wikipediaEn"]),
    12: (["Musician", "AwardReceipt"], ["hasAwardReceipt", "wikipediaEn"]),
}


def local(u):
    return str(u).split("#")[-1] if "#" in str(u) else str(u).rsplit("/", 1)[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onto", default="anthology/ontology.ttl")
    ap.add_argument("--out", default="reports")
    a = ap.parse_args()
    g = Graph()
    g.parse(a.onto, format="turtle")
    print(f"Онтология загружена: {len(g)} троек")

    classes = sorted(c for c in g.subjects(RDF.type, OWL.Class) if isinstance(c, URIRef))
    oprops = sorted(g.subjects(RDF.type, OWL.ObjectProperty))
    dprops = sorted(g.subjects(RDF.type, OWL.DatatypeProperty))
    funcs = set(g.subjects(RDF.type, OWL.FunctionalProperty))
    res = []   # (требование, выполнено, детали)

    def add(name, ok, detail=""):
        res.append((name, bool(ok), detail))

    # 1. классы и иерархия
    sub = [(c, p) for c in classes for p in g.objects(c, RDFS.subClassOf) if p in classes]
    add("не меньше 5 классов", len(classes) >= 5, f"классов: {len(classes)}")
    add("есть иерархия классов", len(sub) > 0, f"отношений subClassOf: {len(sub)}")
    # 2. свойства
    add("есть object- и datatype-свойства", oprops and dprops, f"object: {len(oprops)}, datatype: {len(dprops)}")
    props = oprops + dprops
    no_dom = [local(p) for p in props if not any(True for _ in g.objects(p, RDFS.domain))]
    no_rng = [local(p) for p in props if not any(True for _ in g.objects(p, RDFS.range))]
    add("у всех свойств указан domain", not no_dom, ", ".join(no_dom))
    add("у всех свойств указан range", not no_rng, ", ".join(no_rng))
    add("есть функциональное свойство", len(funcs) > 0, f"функциональных: {len(funcs)}")
    # 3. подписи
    def has_lang(s, pred, lang):
        return any(isinstance(o, Literal) and o.language == lang for o in g.objects(s, pred))
    bad = []
    for e in classes + props:
        miss = []
        if not has_lang(e, RDFS.label, "ru"): miss.append("label@ru")
        if not has_lang(e, RDFS.label, "en"): miss.append("label@en")
        if not any(True for _ in g.objects(e, RDFS.comment)): miss.append("comment")
        if miss: bad.append(f"{local(e)} ({', '.join(miss)})")
    add("у всех классов и свойств есть label (ru, en) и comment", not bad, "; ".join(bad))
    # 4. пространство имён
    foreign = [str(e) for e in classes + props if not str(e).startswith(NS)]
    add("все классы и свойства в собственном пространстве имён", not foreign, "; ".join(foreign))
    # 5. домены и диапазоны ссылаются на свои классы
    wrong = []
    for p in props:
        for o in list(g.objects(p, RDFS.domain)) + [r for r in g.objects(p, RDFS.range) if p in oprops]:
            if isinstance(o, URIRef) and o not in classes:
                wrong.append(f"{local(p)} -> {o}")
    add("domain/range объектных свойств - классы онтологии", not wrong, "; ".join(wrong))
    # 6. equivalent*
    eqc = [(local(c), str(o)) for c in classes for o in g.objects(c, OWL.equivalentClass)]
    eqp = [(local(p), str(o)) for p in props for o in g.objects(p, OWL.equivalentProperty)]
    add("указаны owl:equivalentClass / owl:equivalentProperty к Wikidata", eqc and eqp,
        f"классов: {len(eqc)}, свойств: {len(eqp)}")
    # 7. использование в вопросах
    used_p = {p for _, ps in USAGE.values() for p in ps}
    used_c = {c for cs, _ in USAGE.values() for c in cs}
    unknown = sorted((used_p | used_c) - {local(e) for e in classes + props})
    add("всё упомянутое в таблице вопросов есть в онтологии", not unknown, ", ".join(unknown))
    unused_p = sorted(local(p) for p in props if local(p) not in used_p)
    unused_c = sorted(local(c) for c in classes if local(c) not in used_c)

    ok_all = all(r[1] for r in res)
    md = ["# Проверка онтологии (задание 3)", "", f"Файл: `{a.onto}`", "",
          "| требование | результат | детали |", "|---|---|---|",
          *[f"| {n} | {'выполнено' if ok else 'НАРУШЕНО'} | {d} |" for n, ok, d in res], "",
          "## Классы и свойства по вопросам", "", "| вопрос | классы | свойства |", "|---|---|---|",
          *[f"| {q} | {', '.join(cs)} | {', '.join(ps)} |" for q, (cs, ps) in USAGE.items()], "",
          "## Не используются в вопросах (нужно пояснение в отчёте)", "",
          f"Свойства ({len(unused_p)}): {', '.join(unused_p) or '—'}", "",
          f"Классы ({len(unused_c)}): {', '.join(unused_c) or '—'}", ""]
    Path(a.out).mkdir(exist_ok=True)
    (Path(a.out) / "ontology_check.md").write_text("\n".join(md), encoding="utf-8")
    for n, ok, d in res:
        print(f"[{'OK' if ok else 'НАРУШЕНО'}] {n}" + (f": {d}" if d else ""))
    print(f"\nСвойства без вопросов: {unused_p}\nКлассы без вопросов: {unused_c}")
    print("ИТОГ:", "все требования выполнены" if ok_all else "есть нарушения")
    return 0 if ok_all else 1


if __name__ == "__main__":
    with runlog.session(__file__, inputs=["anthology/ontology.ttl"], outputs=["reports/ontology_check.md"]):
        sys.exit(main())
