#!/usr/bin/env python3
"""КР1, задание 2: запускает вопросы к графу и корпусу, печатает ответы и сверяет с эталоном.
Запуск:  python kr1_questions.py [--graph anthology/graph.ttl] [--onto anthology/ontology.ttl] [--corpus anthology/corpus.jsonl]
Нужен rdflib (pip install rdflib)."""
import argparse, json, re, sys
from datetime import date
from rdflib import Graph

import runlog

PFX = """PREFIX : <http://example.org/rs100/onto#>
PREFIX res: <http://example.org/rs100/res/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
"""

def RU(s, v="?name"):
    """Метка на русском, а если русской нет - на английском (напр. у Jay-Z только en)."""
    return (f'(lang({v})="ru" || (lang({v})="en" && NOT EXISTS {{ {s} rdfs:label ?x . FILTER(lang(?x)="ru") }}))')

# (номер, тип, вопрос, SPARQL, функция приведения результата, эталон)
GRAPH_Q = [
 (1, "фильтр по дате", "Кто родился 01.01.1960 или позже?",
  'SELECT ?name WHERE { ?m a :Musician ; rdfs:label ?name ; :birthDate ?d . '
  'FILTER(?d >= "1960-01-01"^^xsd:date && ' + RU('?m') + ') } ORDER BY ?d',
  lambda r: [str(x[0]) for x in r],
  ["Доктор Дре", "JAŸ‐Z", "Тупак Шакур", "Эминем"]),
 (2, "агрегация", "Сколько музыкантов всего / умерли?",
  'SELECT (COUNT(DISTINCT ?m) AS ?total) (COUNT(DISTINCT ?dead) AS ?dead_n) WHERE '
  '{ ?m a :Musician . OPTIONAL { ?m :deathDate ?dd . BIND(?m AS ?dead) } }',
  lambda r: [int(r[0][0]), int(r[0][1])], [53, 34]),
 (3, "сортировка + агрегация", "Топ-3 по числу наград",
  'SELECT ?name (COUNT(?r) AS ?n) WHERE { ?m a :Musician ; rdfs:label ?name ; :hasAwardReceipt ?r . '
  'FILTER(' + RU('?m') + ') } GROUP BY ?name ORDER BY DESC(?n) LIMIT 3',
  lambda r: [(str(x[0]), int(x[1])) for x in r],
  [("Арета Франклин", 44), ("Элтон Джон", 27), ("Боб Дилан", 25)]),
 (4, "многошаговый (2 ребра)", "Кто в одной группе с Роем Орбисоном?",
  'SELECT DISTINCT ?name WHERE { res:Q188426 :memberOf ?g . ?o a :Musician ; :memberOf ?g ; rdfs:label ?name . '
  'FILTER(?o != res:Q188426 && ' + RU('?o') + ') }',
  lambda r: sorted(str(x[0]) for x in r), sorted(["Том Петти", "Боб Дилан"])),
 (5, "квалификатор времени", "Супруг Джона Леннона в 1970 году",
  'SELECT ?name WHERE { res:Q1203 :hasMarriage ?mar . ?mar :spouse ?sp ; :marriageStart ?s . '
  'OPTIONAL { ?mar :marriageEnd ?e } ?sp rdfs:label ?name . '
  'FILTER(?s <= "1970-06-30"^^xsd:date && (!BOUND(?e) || ?e >= "1970-06-30"^^xsd:date) && ' + RU('?sp') + ') }',
  lambda r: [str(x[0]) for x in r], ["Йоко Оно"]),
 (6, "фильтр по числу + сортировка", "Рост > 180 см по убыванию",
  'SELECT ?name ?h WHERE { ?m a :Musician ; rdfs:label ?name ; :heightCm ?h . '
  'FILTER(?h > 180 && ' + RU('?m') + ') } ORDER BY DESC(?h)',
  lambda r: [(str(x[0]), float(x[1])) for x in r],
  [("Джонни Кэш", 188.0), ("Карл Перкинс", 185.0), ("Фрэнк Заппа", 183.0)]),
]

# (номер, qid, [фрагменты цитат, которые должны быть в тексте])
TEXT_Q = [
 (7,  "Q392:enwiki",   ["he was met with cheering and booing and left the stage after three songs",
                        "the boos were from folk fans whom Dylan had alienated by appearing, unexpectedly, with an electric guitar"]),
 (8,  "Q409:enwiki",   ['two days before "Smile Jamaica"',
                        "believed to have been politically motivated, as many felt that Smile Jamaica was actually a support rally for Manley"]),
 (9,  "Q5928:enwiki",  ["aspirated his own vomit and died of asphyxia while intoxicated with barbiturates",
                        "18 times the recommended dosage"]),
 (10, "Q220707:enwiki",['gave him the nickname "Muddy" at an early age because he loved to play in the muddy water of nearby Deer Creek']),
 (11, "Q5977:enwiki",  ["Soon after takeoff, the plane crashed, killing Holly, Ritchie Valens, the Big Bopper, and pilot Roger Peterson",
                        "took off in inclement weather, even though he was not certified to fly by instruments only"]),
 (12, "Q125121:enwiki",["noticed for her gospel singing at New Bethel Baptist Church in Detroit"]),
]

def norm(s):
    return re.sub(r"\s+", " ", s.replace("“", '"').replace("”", '"').replace("’", "'")).strip()

def load_corpus(path):
    docs = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line); docs[d["id"]] = d
    return docs

def check_quotes(docs):
    ok_all = True
    print("\n=== Вопросы 7-12: проверка цитат в корпусе ===")
    for n, did, frags in TEXT_Q:
        text = norm(docs[did]["text"]) if did in docs else ""
        for fr in frags:
            ok = norm(fr) in text
            ok_all &= ok
            print(f"[{'OK' if ok else 'НЕТ'}] Q{n} {did}: «{fr[:70]}…»")
        if did in docs: print("      источник:", docs[did]["url"])
    return ok_all

def age(b, d):
    b, d = date.fromisoformat(b), date.fromisoformat(d)
    return d.year - b.year - ((d.month, d.day) < (b.month, b.day))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="anthology/graph.ttl")
    ap.add_argument("--onto", default="anthology/ontology.ttl")
    ap.add_argument("--corpus", default="anthology/corpus.jsonl")
    a = ap.parse_args()
    g = Graph(); g.parse(a.graph, format="turtle")
    try: g.parse(a.onto, format="turtle")
    except Exception as e: print("онтология не загружена:", e)
    print(f"Загружено троек: {len(g)}")
    ok_all = True
    print("\n=== Вопросы 1-6: граф ===")
    for n, typ, q, sp, conv, ref in GRAPH_Q:
        got = conv(list(g.query(PFX + sp)))
        ok = got == ref; ok_all &= ok
        print(f"[{'OK' if ok else 'РАСХОЖДЕНИЕ'}] Q{n} ({typ}): {q}\n    получено: {got}" + ("" if ok else f"\n    эталон:   {ref}"))

    docs = load_corpus(a.corpus)
    ok_all &= check_quotes(docs)

    print("\n=== Вопросы 11-12: графовая часть ===")
    rows = g.query(PFX + 'SELECT ?m ?name ?b ?d ?en WHERE { ?m a :Musician ; rdfs:label ?name ; :birthDate ?b ; :deathDate ?d . '
                         'OPTIONAL { ?m :wikipediaEn ?en } FILTER(' + RU('?m') + ') }')
    youngest = min(rows, key=lambda r: age(str(r[2]), str(r[3])))
    q11 = str(youngest[0]).rsplit("/", 1)[-1]
    print(f"Q11: самый молодой умерший — {youngest[1]} ({q11}), возраст {age(str(youngest[2]), str(youngest[3]))}, статья: {youngest[4]}")
    ok_all &= (q11 == "Q5977")
    top = list(g.query(PFX + 'SELECT ?m ?name (COUNT(?r) AS ?n) WHERE { ?m a :Musician ; rdfs:label ?name ; :hasAwardReceipt ?r . '
                             'FILTER(' + RU('?m') + ') } GROUP BY ?m ?name ORDER BY DESC(?n) LIMIT 1'))[0]
    q12 = str(top[0]).rsplit("/", 1)[-1]
    print(f"Q12: больше всего наград — {top[1]} ({q12}), {top[2]}")
    ok_all &= (q12 == "Q125121")
    for qid in (q11, q12):
        d = docs.get(f"{qid}:enwiki")
        if d:
            m = re.search(r"== ?Death ?==(.{0,600})", d["text"], re.S) if qid == q11 else None
            print(f"\n--- текст по {qid} ({d['url']}) ---")
            print((m.group(1) if m else d["text"][:600]).strip())
    print("\nИТОГ:", "всё совпало с эталоном" if ok_all else "есть расхождения")
    return 0 if ok_all else 1

if __name__ == "__main__":
    with runlog.session(__file__, inputs=["anthology/graph.ttl", "anthology/ontology.ttl", "anthology/corpus.jsonl"]):
        sys.exit(main())