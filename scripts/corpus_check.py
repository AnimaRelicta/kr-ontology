#!/usr/bin/env python3
"""
КР1, задание 5: проверка и описание корпуса текстов (anthology/corpus.jsonl).

Сам корпус собирает scripts/rs100_anthology.py (тексты статей Википедии ru/en по сайтлинкам Wikidata).
Этот скрипт проверяет файл и готовит данные для отчёта:
  - каждая строка - корректный JSON с обязательными полями; id уникальны; n_chars == len(text);
  - у КАЖДОЙ сущности домена (anthology/domain.csv) есть хотя бы один источник с непустым текстом;
  - в корпусе нет сущностей вне домена;
  - статистика: документов, сущностей, языков, источников, объёма текста.
Запуск:  uv run scripts/corpus_check.py [--corpus anthology/corpus.jsonl] [--domain anthology/domain.csv] [--out reports]
Нужен: pandas. Результат: reports/corpus_stats.md, reports/corpus_entities.csv; код возврата 1, если есть нарушения.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

import runlog

REQUIRED = ["id", "qid", "entity", "source", "lang", "title", "url", "license", "n_chars", "text"]
MIN_CHARS = 500          # короче - считаем, что текста по сути нет


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="anthology/corpus.jsonl")
    ap.add_argument("--domain", default="anthology/domain.csv")
    ap.add_argument("--out", default="reports")
    a = ap.parse_args()

    dom = pd.read_csv(a.domain)
    dom_q = list(dict.fromkeys(dom["qid"]))
    names = dict(zip(dom["qid"], dom["title"]))

    docs, errors = [], []
    with open(a.corpus, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"строка {i}: невалидный JSON ({e})")
                continue
            miss = [k for k in REQUIRED if k not in d]
            if miss:
                errors.append(f"строка {i}: нет полей {miss}")
                continue
            if d["n_chars"] != len(d["text"]):
                errors.append(f"{d['id']}: n_chars={d['n_chars']}, а длина текста {len(d['text'])}")
            if not str(d["url"]).startswith("http"):
                errors.append(f"{d['id']}: некорректный url «{d['url']}»")
            docs.append(d)
    print(f"Прочитано документов: {len(docs)}")

    ids = Counter(d["id"] for d in docs)
    dup = [k for k, v in ids.items() if v > 1]
    good = [d for d in docs if len(d["text"].strip()) >= MIN_CHARS]
    short = [d["id"] for d in docs if len(d["text"].strip()) < MIN_CHARS]
    by_q = {}
    for d in good:
        by_q.setdefault(d["qid"], []).append(d)
    uncovered = [q for q in dom_q if q not in by_q]
    outside = sorted({d["qid"] for d in docs} - set(dom_q))

    rows = []
    for q in dom_q:
        ds = by_q.get(q, [])
        rows.append({"qid": q, "entity": (ds[0]["entity"] if ds else names.get(q, "")), "n_docs": len(ds),
                     "langs": ",".join(sorted(d["lang"] for d in ds)), "chars": sum(len(d["text"]) for d in ds),
                     "sources": " | ".join(f"{d['lang']}: {d['url']}" for d in ds)})
    edf = pd.DataFrame(rows)

    checks = [
        ("все строки — корректный JSON с обязательными полями, n_chars совпадает с длиной", not errors, "; ".join(errors[:5])),
        ("id документов уникальны", not dup, ", ".join(dup[:5])),
        (f"у каждой сущности домена есть хотя бы один источник с текстом (≥{MIN_CHARS} символов)", not uncovered,
         "без текста: " + ", ".join(f"{q} ({names.get(q, '')})" for q in uncovered[:10])),
        ("в корпусе нет сущностей вне домена", not outside, ", ".join(outside[:10])),
    ]
    ok_all = all(c[1] for c in checks)
    lens = [len(d["text"]) for d in good]
    s = pd.Series(lens) if lens else pd.Series([0])
    langs, srcs = Counter(d["lang"] for d in good), Counter(d["source"] for d in good)
    cover1, cover2 = int((edf["n_docs"] >= 1).sum()), int((edf["n_docs"] >= 2).sum())
    lic = sorted({d["license"] for d in docs})

    md = ["# Корпус текстов (задание 5)", "", f"Файл: `{a.corpus}` (jsonl, одна статья — одна строка).", "",
          "## Структура записи", "",
          "| поле | содержимое |", "|---|---|",
          "| id | `<QID>:<язык>wiki`, уникальный идентификатор документа |",
          "| qid | идентификатор сущности в Wikidata (связь с owl:sameAs в графе) |",
          "| entity | название сущности (метка ru, иначе en) |",
          "| source, lang | источник (wikipedia) и язык статьи (ru/en) |",
          "| title, url | заголовок статьи и ссылка на неё |",
          "| license | лицензия источника (CC BY-SA 4.0) |",
          "| n_chars | число символов в text |",
          "| text | полный текст статьи без разметки (plain text через API Википедии) |", "",
          "## Общие цифры", "",
          f"- Документов: **{len(docs)}** (с текстом ≥{MIN_CHARS} символов: {len(good)})",
          f"- Сущностей в домене: **{len(dom_q)}**; сущностей хотя бы с одним текстом: **{cover1}**; с двумя и более (ru и en): {cover2}",
          f"- По языкам: {', '.join(f'{k}: {v}' for k, v in sorted(langs.items()))}; по источникам: {', '.join(f'{k}: {v}' for k, v in srcs.items())}",
          "- Объём: " + f"{sum(lens):,}".replace(",", " ") + " символов; на документ: min " + f"{int(s.min()):,}".replace(",", " ")
          + ", медиана " + f"{int(s.median()):,}".replace(",", " ") + ", max " + f"{int(s.max()):,}".replace(",", " "),
          f"- Лицензия: {', '.join(lic)}; источник текста каждой сущности: ссылка `url` и таблица `corpus_entities.csv`", "",
          "## Проверки", "", "| проверка | результат | детали |", "|---|---|---|",
          *[f"| {n} | {'выполнено' if ok else 'НАРУШЕНО'} | {d} |" for n, ok, d in checks], ""]
    if short:
        md += ["Слишком короткие документы (не учитываются): " + ", ".join(short), ""]
    out = Path(a.out); out.mkdir(exist_ok=True)
    (out / "corpus_stats.md").write_text("\n".join(md), encoding="utf-8")
    edf.to_csv(out / "corpus_entities.csv", index=False, encoding="utf-8")

    print(f"Сущностей с текстом: {cover1}/{len(dom_q)}; с ru и en: {cover2}; символов всего: {sum(lens)}")
    for n, ok, d in checks:
        print(f"[{'OK' if ok else 'НАРУШЕНО'}] {n}" + (f": {d}" if d else ""))
    print("ИТОГ:", "корпус удовлетворяет требованиям" if ok_all else "есть нарушения")
    return 0 if ok_all else 1


if __name__ == "__main__":
    with runlog.session(__file__, inputs=["anthology/corpus.jsonl", "anthology/domain.csv"],
                        outputs=["reports/corpus_stats.md", "reports/corpus_entities.csv"]):
        sys.exit(main())
