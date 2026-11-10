#!/usr/bin/env python3
"""
КР1, задание 6: проверка (pyshacl) и логический вывод (owlrl).

  1. pyshacl: граф anthology/graph.ttl проверяется по shapes/shapes.ttl; онтология подключается как ont_graph
     (чтобы sh:class учитывал иерархию классов). Виды ограничений: кардинальность, тип данных, класс значения,
     диапазон, шаблон, порядок значений.
     Результат: reports/shacl_report.ttl (граф отчёта), reports/shacl_report.txt (текст pyshacl),
     reports/validation_summary.md (нарушения, сгруппированные по виду и свойству, + решение по каждому).
  2. owlrl: замыкание (OWL 2 RL) над graph.ttl + ontology.ttl; новые тройки = замыкание минус исходный граф.
     Результат: reports/inferred_triples.ttl и раздел «логический вывод» в validation_summary.md.

Запуск:  uv run scripts/validate_and_infer.py [--graph ...] [--onto ...] [--shapes shapes/shapes.ttl] [--out reports] [--rdfs]
--rdfs  использовать RDFS-семантику вместо OWL RL (быстрее, меньше новых троек).
Нужны: rdflib pyshacl owlrl pandas.
"""
import argparse
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import owlrl
from pyshacl import validate
from rdflib import Graph, URIRef, Literal, Namespace, RDF, RDFS, OWL

import runlog

SH = Namespace("http://www.w3.org/ns/shacl#")
NS = "http://example.org/rs100/onto#"
WD, WDT = "http://www.wikidata.org/entity/", "http://www.wikidata.org/prop/direct/"

KIND = {
    "MinCountConstraintComponent": "кардинальность (min)", "MaxCountConstraintComponent": "кардинальность (max)",
    "DatatypeConstraintComponent": "тип данных", "ClassConstraintComponent": "класс значения",
    "MinInclusiveConstraintComponent": "диапазон", "MaxInclusiveConstraintComponent": "диапазон",
    "PatternConstraintComponent": "шаблон", "LessThanConstraintComponent": "порядок значений",
    "NodeKindConstraintComponent": "вид узла",
}

# Предлагаемое решение по типу нарушения: (компонент, свойство) -> (действие, обоснование).
# Это ЧЕРНОВИК: после просмотра реальных нарушений подтвердите или исправьте текст.
DECISIONS = {
    ("MinCountConstraintComponent", "awardYear"): ("нарушение оставлено (Warning)",
        "в Wikidata у части наград нет квалификатора «дата»; год нельзя выдумывать, поэтому ограничение ослаблено до предупреждения"),
    ("MinCountConstraintComponent", "followersDate"): ("нарушение оставлено (Warning)",
        "дата измерения не всегда указана в Wikidata; значение подписчиков остаётся полезным и без даты"),
    ("LessThanConstraintComponent", "marriageStart"): ("данные исправлены или нарушение оставлено — по результату проверки Wikidata",
        "если даты перепутаны в Wikidata, исправлять нужно там; в графе фиксируем как есть и отмечаем"),
}


def local(u):
    s = str(u)
    return s.split("#")[-1] if "#" in s else s.rsplit("/", 1)[-1]


def trivial(t):
    s, p, o = t
    if isinstance(s, Literal):
        return True
    if p == RDF.type and o in (RDFS.Resource, OWL.Thing, RDFS.Class, OWL.Class, RDF.Property):
        return True
    if p in (OWL.sameAs, OWL.equivalentClass, OWL.equivalentProperty, RDFS.subClassOf, RDFS.subPropertyOf) and s == o:
        return True
    return False


def category(t):
    s, p, o = t
    if p == RDF.type and str(s).startswith("http://example.org/rs100/res/") and str(o).startswith(NS):
        return "rdf:type по иерархии классов (Musician → Person → Agent и т. д.)"
    if p == OWL.sameAs:
        return "owl:sameAs: симметрия и транзитивность (wd:Q… sameAs res:Q…)"
    if str(s).startswith(WD):
        return "свойства, скопированные на wd:Q… через owl:sameAs"
    if str(p).startswith(WDT):
        return "эквивалентные свойства Wikidata (wdt:P…) через owl:equivalentProperty"
    if p == RDF.type and str(o).startswith(WD):
        return "эквивалентные классы Wikidata через owl:equivalentClass"
    if str(s).startswith(NS) or str(s).startswith(WDT):
        return "выводы над онтологией (подклассы, подсвойства, домены)"
    return "прочее"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="anthology/graph.ttl")
    ap.add_argument("--onto", default="anthology/ontology.ttl")
    ap.add_argument("--shapes", default="shapes/shapes.ttl")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--rdfs", action="store_true")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(exist_ok=True)
    g, o, sg = Graph(), Graph(), Graph()
    g.parse(a.graph, format="turtle"); o.parse(a.onto, format="turtle"); sg.parse(a.shapes, format="turtle")
    print(f"Граф: {len(g)} троек; онтология: {len(o)}; shapes: {len(sg)}")

    # ================= SHACL =================
    t0 = time.time()
    kw = dict(data_graph=g, shacl_graph=sg, ont_graph=o, inference="none", abort_on_first=False, meta_shacl=False, debug=False)
    try:
        conforms, rg, rtext = validate(allow_warnings=True, **kw)
    except TypeError:                      # старая версия pyshacl без allow_warnings
        conforms, rg, rtext = validate(**kw)
    print(f"pyshacl: {time.time() - t0:.1f} с; conforms (с учётом предупреждений) = {conforms}")
    rg.serialize(out / "shacl_report.ttl", format="turtle")
    (out / "shacl_report.txt").write_text(rtext, encoding="utf-8")

    groups = defaultdict(list)
    for r in rg.subjects(RDF.type, SH.ValidationResult):
        sev = local(rg.value(r, SH.resultSeverity) or "Violation")
        comp = local(rg.value(r, SH.sourceConstraintComponent) or "")
        path = rg.value(r, SH.resultPath)
        groups[(sev, comp, local(path) if path is not None else "—")].append(
            (local(rg.value(r, SH.focusNode) or ""), str(rg.value(r, SH.value) or ""), str(rg.value(r, SH.resultMessage) or "")))
    sev_count = Counter()
    for (sev, *_), v in groups.items():
        sev_count[sev] += len(v)
    print("Нарушений по уровням:", dict(sev_count) or "нет")

    # ================= OWLRL =================
    union = Graph()
    for t in g: union.add(t)
    for t in o: union.add(t)
    before = set(union)
    t0 = time.time()
    sem = owlrl.RDFS_Semantics if a.rdfs else owlrl.OWLRL_Semantics
    owlrl.DeductiveClosure(sem, axiomatic_triples=False, datatype_axioms=False).expand(union)
    added_all = set(union) - before
    added = {t for t in added_all if not trivial(t)}
    print(f"owlrl ({'RDFS' if a.rdfs else 'OWL RL'}): {time.time() - t0:.1f} с; добавлено троек: {len(added_all)}, значимых: {len(added)}")
    inf = Graph()
    for pfx, ns in g.namespaces():
        inf.bind(pfx, ns)
    inf.bind("", NS); inf.bind("wdt", WDT); inf.bind("wd", WD)
    for t in added: inf.add(t)
    inf.serialize(out / "inferred_triples.ttl", format="turtle")
    cats = defaultdict(list)
    for t in added: cats[category(t)].append(t)

    def inst(graph, cls):
        return len(set(graph.subjects(RDF.type, URIRef(NS + cls))))
    ba = [(c, inst(g, c), inst(union, c)) for c in ("Musician", "Person", "Agent", "Organization", "RecordLabel", "Place", "Country")]

    # ================= ОТЧЁТ =================
    md = ["# Проверка и логический вывод (задание 6)", "",
          "## 1. SHACL (pyshacl)", "",
          f"Данные: `{a.graph}` ({len(g)} троек); ограничения: `{a.shapes}`; онтология как ont_graph: `{a.onto}`.", "",
          f"Результат: `sh:conforms` (предупреждения не считаются) = **{conforms}**; всего результатов: "
          f"{', '.join(f'{k}: {v}' for k, v in sev_count.items()) or 'нет'}. Полный отчёт: `shacl_report.ttl`, `shacl_report.txt`.", ""]
    if groups:
        md += ["### Нарушения по видам", "",
               "| уровень | вид ограничения | компонент | свойство | кол-во | примеры (узел → значение) | решение |", "|---|---|---|---|---|---|---|"]
        for (sev, comp, path), v in sorted(groups.items(), key=lambda x: (x[0][0], -len(x[1]))):
            ex = "; ".join(f"{f} → {val}" if val else f for f, val, _ in v[:3])
            act, why = DECISIONS.get((comp, path), ("ТРЕБУЕТСЯ РЕШЕНИЕ", "исправить данные / ослабить ограничение / оставить — выбрать и обосновать"))
            md.append(f"| {sev} | {KIND.get(comp, comp)} | {comp} | {path} | {len(v)} | {ex} | **{act}**: {why} |")
        md += ["", "Столбец «решение» — черновик. Для каждой строки оставьте один вариант: данные исправлены (что именно), "
               "ограничение ослаблено (как) или нарушение оставлено (почему)."]
    else:
        md += ["Нарушений нет."]
    md += ["", "## 2. Логический вывод (owlrl)", "",
           f"Семантика: **{'RDFS' if a.rdfs else 'OWL 2 RL'}**, вход: граф + онтология ({len(before)} троек). "
           f"Добавлено троек: **{len(added_all)}**, из них после отбрасывания тривиальных (rdfs:Resource, owl:Thing, рефлексивные): **{len(added)}**. "
           "Файл: `inferred_triples.ttl`.", "",
           "### Что добавилось (по категориям)", "", "| категория | троек | пример |", "|---|---|---|"]
    for c, ts in sorted(cats.items(), key=lambda x: -len(x[1])):
        s, p, ob = sorted(ts, key=str)[0]
        ex = " ".join(x.n3(union.namespace_manager) for x in (s, p, ob))
        md.append(f"| {c} | {len(ts)} | `{ex}` |")
    md += ["", "### Эффект на числе экземпляров классов", "", "| класс | в исходном графе | после вывода |", "|---|---|---|",
           *[f"| {c} | {x} | {y} |" for c, x, y in ba], ""]
    (out / "validation_summary.md").write_text("\n".join(md), encoding="utf-8")

    for k, v in sorted(groups.items(), key=lambda x: (x[0][0], -len(x[1]))):
        print(f"  [{k[0]}] {KIND.get(k[1], k[1])} / {k[2]}: {len(v)}  пример: {v[0][0]} → {v[0][1]}")
    for c, ts in sorted(cats.items(), key=lambda x: -len(x[1])):
        print(f"  + {len(ts):6d}  {c}")
    print("Классы до/после:", [(c, x, y) for c, x, y in ba])
    print(f"Файлы в {out.resolve()}: shacl_report.ttl, shacl_report.txt, inferred_triples.ttl, validation_summary.md")
    return 0


if __name__ == "__main__":
    with runlog.session(__file__, inputs=["anthology/graph.ttl", "anthology/ontology.ttl", "shapes/shapes.ttl"],
                        outputs=[f"reports/{n}" for n in ("shacl_report.ttl", "shacl_report.txt", "inferred_triples.ttl", "validation_summary.md")]):
        sys.exit(main())
