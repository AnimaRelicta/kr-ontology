"""Запись лога запуска скрипта (доказательство, что скрипт был запущен).

Использование в скрипте:
    import runlog
    if __name__ == "__main__":
        with runlog.session(__file__, inputs=[...], outputs=[...]):
            sys.exit(main())

Что сохраняется в logs/<скрипт>_<дата-время>.log:
  - заголовок: время старта, команда запуска, рабочая папка, Python, ОС, пользователь/компьютер,
    SHA-256 самого скрипта и входных файлов;
  - весь вывод программы (stdout и stderr) - он при этом остаётся и на экране;
  - итог: код возврата, время работы, SHA-256/размер/время изменения выходных файлов.
Строка о запуске дописывается в logs/runs.jsonl.
"""
import getpass, hashlib, json, os, platform, sys, time, traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

LOG_DIR = Path("logs")


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _describe(paths):
    out = []
    for p in map(Path, paths):
        if p.is_file():
            st = p.stat()
            out.append({"path": str(p), "sha256": _sha(p), "bytes": st.st_size,
                        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
        else:
            out.append({"path": str(p), "missing": True})
    return out


class _Tee:
    def __init__(self, stream, logfile):
        self.stream, self.logfile = stream, logfile

    def write(self, s):
        try:
            self.stream.write(s)
        except UnicodeEncodeError:  # консоль Windows без UTF-8
            self.stream.write(s.encode("utf-8", "replace").decode(self.stream.encoding or "ascii", "replace"))
        self.logfile.write(s)
        self.logfile.flush()
        return len(s)

    def flush(self):
        self.stream.flush()
        self.logfile.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


@contextmanager
def session(script, inputs=(), outputs=()):
    script = Path(script).resolve()
    LOG_DIR.mkdir(exist_ok=True)
    t0 = datetime.now()
    log_path = LOG_DIR / f"{script.stem}_{t0:%Y%m%d_%H%M%S}.log"
    argv_files = [a for a in sys.argv[1:] if Path(a).is_file()]
    f = open(log_path, "w", encoding="utf-8")
    f.write("=" * 70 + "\n")
    f.write(f"Скрипт:        {script}\n")
    f.write(f"Команда:       {' '.join([sys.executable] + sys.argv)}\n")
    f.write(f"Старт:         {t0.isoformat(timespec='seconds')}\n")
    f.write(f"Рабочая папка: {Path.cwd()}\n")
    f.write(f"Python:        {sys.version.split()[0]}\n")
    f.write(f"ОС / машина:   {platform.platform()} / {platform.node()}\n")
    try:
        f.write(f"Пользователь:  {getpass.getuser()}\n")
    except Exception:
        pass
    f.write("SHA-256 скрипта и входных файлов:\n")
    for d in _describe([script, *inputs, *argv_files]):
        f.write("  " + (f"{d['path']}: НЕ НАЙДЕН\n" if d.get("missing") else
                        f"{d['path']}  sha256={d['sha256']}  {d['bytes']} байт\n"))
    f.write("=" * 70 + "\n")
    f.flush()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _Tee(old_out, f), _Tee(old_err, f)
    code, error = 0, None
    try:
        yield log_path
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        raise
    except BaseException:
        code, error = 1, traceback.format_exc()
        traceback.print_exc()
        raise
    finally:
        t1 = datetime.now()
        outs = _describe(outputs)
        sys.stdout, sys.stderr = old_out, old_err
        f.write("\n" + "=" * 70 + "\n")
        f.write(f"Завершение:    {t1.isoformat(timespec='seconds')}  (длительность {(t1 - t0).total_seconds():.1f} с)\n")
        f.write(f"Код возврата:  {code}\n")
        f.write("Выходные файлы:\n")
        for d in outs:
            f.write("  " + (f"{d['path']}: нет\n" if d.get("missing") else
                            f"{d['path']}  sha256={d['sha256']}  {d['bytes']} байт  изменён {d['modified']}\n"))
        f.close()
        with open(LOG_DIR / "runs.jsonl", "a", encoding="utf-8") as j:
            j.write(json.dumps({"script": script.name, "start": t0.isoformat(timespec="seconds"),
                                "end": t1.isoformat(timespec="seconds"), "exit_code": code,
                                "log": str(log_path), "outputs": outs}, ensure_ascii=False) + "\n")
        print(f"[runlog] лог сохранён: {log_path}")
