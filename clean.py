#!/usr/bin/env python3
"""Downloads cleaner: sort a messy folder into tidy folders and find duplicates, with a live 3D dashboard. Fully offline.

    python clean.py                          open the dashboard (your browser, on this computer only)
    python clean.py "C:\\Users\\you\\Downloads"  open the dashboard on that folder
    python clean.py FOLDER --report          print what it would do; nothing is written
    python clean.py FOLDER --apply           do it from the command line (same settings as the dashboard)
    python clean.py FOLDER --undo RUN        reverse a run (run ids are listed with --runs)

Every dashboard setting is a flag too (python clean.py --help). By default your files are only read: sorted copies go to
"<folder> (sorted)". With --action move the originals move there instead, and --undo puts them back. Nothing is ever deleted.
No internet, no AI model, no telemetry. Python 3.8+, standard library only.
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cleaner import engine  # noqa: E402


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f} {u}" if u != "B" else f"{int(n)} B"
        n /= 1024


def main():
    ap = argparse.ArgumentParser(description="Sort a folder and find duplicates. Offline. Copies by default; never deletes.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", help="the folder to clean (default: your Downloads)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--report", action="store_true", help="print the plan, write nothing")
    mode.add_argument("--apply", action="store_true", help="carry out the plan without the dashboard")
    mode.add_argument("--undo", metavar="RUN", help="reverse a run")
    mode.add_argument("--runs", action="store_true", help="list past runs")
    ap.add_argument("--port", type=int, default=8766, help="dashboard port (default 8766)")
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    for key, default, rule, help_ in engine.SCHEMA:
        if key == "source":
            continue
        flag = "--" + key.replace("_", "-")
        if rule == "bool":
            ap.add_argument(flag, choices=["yes", "no"], help=f"{help_} (default {'yes' if default else 'no'})")
        elif isinstance(rule, list):
            ap.add_argument(flag, choices=rule, help=f"{help_} (default {default})")
        elif rule == "kinds":
            ap.add_argument(flag, help="comma-separated, e.g. Images,Videos (default: all)")
        elif rule == "kindmap":
            ap.add_argument(flag, metavar="JSON", help='e.g. \'{"Images": "jpg png", "Books": "epub pdf"}\' (replaces the built-in kinds)')
        else:
            ap.add_argument(flag, help=f"{help_} (default {default or 'none'})")
    a = ap.parse_args()

    over = {}
    if a.folder:
        over["source"] = a.folder
    for key, default, rule, _ in engine.SCHEMA:
        v = getattr(a, key, None)
        if key == "source" or v is None:
            continue
        over[key] = (v == "yes") if rule == "bool" else v.split(",") if rule == "kinds" else json.loads(v) if rule == "kindmap" else v
    try:
        cfg = engine.config(over)
        if a.report or a.apply or a.undo or a.runs:
            src, out = engine.folders(cfg)
    except ValueError as e:
        sys.exit(f"error: {e}")

    if not (a.report or a.apply or a.undo or a.runs):
        from cleaner import server
        return server.serve(cfg, a.port, not a.no_browser)
    if a.runs:
        for r in engine.runs(cfg):
            print(f'{r["run"]}  {r["action"]:<4}  {r["files"]:>6} files  {"undone" if r["undone"] else ""}')
        return
    if a.undo:
        r = engine.undo(cfg, a.undo)
        print(f'Undone: {r["reversed"]} files reversed, {r["left"]} left as they were (changed or missing).')
        return
    rows, skipped = engine.scan(cfg, lambda ph, d, t: print(f"\r{ph} {d:,}" + (f"/{t:,}" if t else ""), end="", file=sys.stderr))
    print("\r" + " " * 40 + "\r", end="", file=sys.stderr)
    steps = engine.plan(rows, cfg)
    s = engine.summary(steps, skipped)
    print(f'{s["files"]:,} files, {human(s["bytes"])} in {src}')
    for k, v in sorted(s["kinds"].items(), key=lambda kv: -kv[1]["bytes"]):
        print(f'  {k:<13} {v["files"]:>6,} {"file " if v["files"] == 1 else "files"}  {human(v["bytes"]):>9}')
    print(f'  {"Duplicates":<13} {s["duplicates"]:>6,} {"file " if s["duplicates"] == 1 else "files"}  {human(s["duplicate_bytes"]):>9}  (extra copies of files counted above)')
    if skipped:
        print("  skipped: " + ", ".join(f"{n} {why}" for why, n in skipped.items()))
    if a.report:
        print(f'Report only. {"Move" if cfg["action"] == "move" else "Copy"} {s["to_write"]:,} files into {out} with --apply.')
        return
    m = engine.apply(steps, cfg, lambda i, st: print(f"\r{cfg['action']} {i + 1:,}/{len(steps):,}", end="", file=sys.stderr))
    print("\r" + " " * 40 + "\r", end="", file=sys.stderr)
    print(f'Done: {len(m["done"]):,} files {"moved" if cfg["action"] == "move" else "copied"} into {out}. Nothing was deleted. Undo with: --undo {m["run"]}')


if __name__ == "__main__":
    main()
