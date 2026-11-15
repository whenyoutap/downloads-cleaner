"""The cleaner's engine: scan a folder, plan where every file goes, apply the plan, undo it.

One settings schema (DEFAULTS) drives everything; the command line and the dashboard both build a config from it with `config()`.
Safety rules, enforced here and not in the UI:
- Copy is the default. Your files are only read.
- "move" moves originals into the sorted folder. Every move is written to a manifest as it happens, and `undo()` puts files back.
- Nothing is ever deleted. Undoing a copy run removes only the copies this tool made, and only if they are unchanged.
- Every source path stays inside the source folder, and every destination inside the output folder.
Standard library only, Python 3.8+.
"""
import csv, hashlib, json, os, shutil, stat, time, uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

KINDS = {
    "Images": "jpg jpeg png gif webp heic heif bmp tiff tif svg raw cr2 nef arw dng",
    "Videos": "mp4 mov mkv avi webm m4v 3gp wmv",
    "Audio": "mp3 wav m4a aac flac ogg opus wma",
    "Documents": "pdf doc docx odt rtf txt md pages epub",
    "Spreadsheets": "xls xlsx csv ods numbers",
    "Slides": "ppt pptx odp key",
    "Installers": "exe msi dmg pkg deb rpm apk appimage msix",
    "Archives": "zip rar 7z tar gz tgz bz2 xz iso",
    "Code": "py js ts html css json xml yml yaml sh ipynb c cpp java go rs sql",
    "Fonts": "ttf otf woff woff2",
}
SCREENSHOT_PREFIXES = ("screenshot", "screen shot", "screen_shot", "scr_", "capture")

# Every setting, its default, and what it may be. The dashboard draws its controls from this list.
SCHEMA = [
    ("source", "~/Downloads", "path", "The folder to clean"),
    ("output", "", "path", 'Where sorted files go. Empty: "<folder> (sorted)" next to it'),
    ("action", "copy", ["copy", "move"], "copy: originals stay where they are. move: originals move into the sorted folder (undo puts them back)"),
    ("layout", "kind/month", ["kind", "kind/month", "kind/year", "year/month", "year"], "Folder structure"),
    ("duplicates", "separate", ["separate", "skip", "keep"], "separate: extra copies go to _Duplicates. skip: leave them where they are. keep: sort them like any file"),
    ("keep", "oldest", ["oldest", "newest", "shortest-name"], "Which copy of a duplicate counts as the original"),
    ("rename", "none", ["none", "date-prefix"], "date-prefix: 2025-03-14 invoice.pdf"),
    ("include_kinds", [], "kinds", "Only these kinds (empty: all)"),
    ("screenshots", True, "bool", "Put images named Screenshot... in their own folder"),
    ("min_mb", 0.0, "number", "Skip files smaller than this (MB, 0: no limit)"),
    ("max_mb", 0.0, "number", "Skip files larger than this (MB, 0: no limit)"),
    ("older_than_days", 0, "int", "Only files not modified in this many days (0: any)"),
    ("newer_than_days", 0, "int", "Only files modified in the last this many days (0: any)"),
    ("skip_hidden", True, "bool", "Skip hidden files and folders"),
    ("recurse", False, "bool", "Also sort files inside subfolders (they come out of their folders; off keeps unzipped projects intact)"),
    ("on_conflict", "number", ["number", "skip"], 'If a name is taken: number ("file (2).pdf") or skip'),
    ("kinds", KINDS, "kindmap", "Which extensions belong to each kind"),
]
DEFAULTS = {k: v for k, v, _, _ in SCHEMA}


def config(overrides=None):
    """A full, checked config: DEFAULTS with `overrides` applied. Raises ValueError with a readable message."""
    c = json.loads(json.dumps(DEFAULTS))
    for k, v in (overrides or {}).items():
        if k not in c:
            raise ValueError(f"unknown setting: {k}")
        c[k] = v
    for k, _, rule, _ in SCHEMA:
        v = c[k]
        if isinstance(rule, list) and v not in rule:
            raise ValueError(f"{k} must be one of {', '.join(rule)}")
        if rule == "bool" and not isinstance(v, bool):
            raise ValueError(f"{k} must be true or false")
        if rule in ("number", "int"):
            try:
                c[k] = float(v) if rule == "number" else int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be a number")
            if c[k] < 0:
                raise ValueError(f"{k} can't be negative")
        if rule == "kinds" and (not isinstance(v, list) or any(x not in list(c["kinds"]) + ["Screenshots", "Other"] for x in v)):
            raise ValueError("include_kinds lists unknown kinds")
        if rule == "kindmap":
            if not isinstance(v, dict) or not all(isinstance(x, str) for x in v.values()):
                raise ValueError("kinds must map a kind name to a space-separated list of extensions")
            for name in v:
                if not name or any(ch in name for ch in '\\/:*?"<>|') or name.startswith((".", "_")):
                    raise ValueError(f"bad kind name: {name!r}")
    if c["min_mb"] and c["max_mb"] and c["min_mb"] > c["max_mb"]:
        raise ValueError("min_mb is larger than max_mb")
    if c["older_than_days"] and c["newer_than_days"] and c["older_than_days"] >= c["newer_than_days"]:
        raise ValueError("older_than_days must be smaller than newer_than_days when both are set")
    return c


def folders(c):
    """The resolved source and output folders, after checking that neither sits inside the other."""
    src = Path(os.path.expanduser(c["source"])).resolve()
    if not src.is_dir():
        raise ValueError(f"not a folder: {src}")
    out = Path(os.path.expanduser(c["output"])).resolve() if c["output"] else src.with_name(src.name + " (sorted)")
    if out == src or src in out.parents or out in src.parents:
        raise ValueError("the sorted folder must be outside the folder being cleaned (and not contain it)")
    return src, out


def _hidden(p: Path, st) -> bool:
    return p.name.startswith(".") or bool(getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 2))


def kind_of(name: str, c) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    kinds = {e: k for k, v in c["kinds"].items() for e in v.split()}
    k = kinds.get(ext, "Other")
    if c["screenshots"] and k == "Images" and name.lower().startswith(SCREENSHOT_PREFIXES):
        return "Screenshots"
    return k


def digest(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def scan(c, progress=lambda phase, done, total: None):
    """Every file in the source that passes the filters, with its kind, and duplicates marked (same size, then same SHA-256)."""
    src, _ = folders(c)
    now, rows, skipped = time.time(), [], defaultdict(int)
    walk = os.walk(src) if c["recurse"] else [(str(src), [], [e.name for e in os.scandir(src) if e.is_file()])]
    for root, dirs, files in walk:
        if c["skip_hidden"]:
            dirs[:] = [d for d in dirs if not _hidden(Path(root, d), os.stat(Path(root, d), follow_symlinks=False))]
        for name in files:
            p = Path(root, name)
            try:
                st = os.stat(p, follow_symlinks=False)
            except OSError:
                skipped["unreadable"] += 1
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            if c["skip_hidden"] and _hidden(p, st):
                skipped["hidden"] += 1
                continue
            mb, age = st.st_size / 2 ** 20, (now - st.st_mtime) / 86400
            if (c["min_mb"] and mb < c["min_mb"]) or (c["max_mb"] and mb > c["max_mb"]):
                skipped["size filter"] += 1
                continue
            if (c["older_than_days"] and age < c["older_than_days"]) or (c["newer_than_days"] and age > c["newer_than_days"]):
                skipped["date filter"] += 1
                continue
            k = kind_of(name, c)
            if c["include_kinds"] and k not in c["include_kinds"]:
                skipped["kind filter"] += 1
                continue
            rows.append({"rel": str(p.relative_to(src)), "name": name, "size": st.st_size, "mtime": st.st_mtime, "kind": k, "dup_of": None})
            if len(rows) % 200 == 0:
                progress("scan", len(rows), 0)
    # Duplicates: hash only files that share a size with another file.
    by_size = defaultdict(list)
    for r in rows:
        if r["size"]:
            by_size[r["size"]].append(r)
    groups = [g for g in by_size.values() if len(g) > 1]
    todo, done = sum(len(g) for g in groups), 0
    order = {"oldest": lambda r: (r["mtime"], r["rel"]), "newest": lambda r: (-r["mtime"], r["rel"]), "shortest-name": lambda r: (len(r["name"]), r["mtime"], r["rel"])}[c["keep"]]
    for g in groups:
        by_hash = defaultdict(list)
        for r in g:
            try:
                by_hash[digest(src / r["rel"])].append(r)
            except OSError:
                skipped["unreadable"] += 1
            done += 1
            if done % 25 == 0:
                progress("hash", done, todo)
        for same in by_hash.values():
            same.sort(key=order)
            for r in same[1:]:
                r["dup_of"] = same[0]["rel"]
    rows.sort(key=lambda r: (r["mtime"], r["rel"]))
    return rows, dict(skipped)


def _dest_dir(r, c):
    d = datetime.fromtimestamp(r["mtime"])
    return {"kind": r["kind"], "kind/month": f'{r["kind"]}/{d:%Y-%m}', "kind/year": f'{r["kind"]}/{d:%Y}', "year/month": f"{d:%Y}/{d:%Y-%m}", "year": f"{d:%Y}"}[c["layout"]]


def _same(p: Path, r) -> bool:
    """True when p already holds this file: same size and modified time (copies keep the time)."""
    try:
        st = os.stat(p)
    except OSError:
        return False
    return st.st_size == r["size"] and abs(st.st_mtime - r["mtime"]) < 2e-3


def plan(rows, c):
    """Where every file goes. Each step: the row plus `dest` (relative to the output folder) or `skip` with the reason."""
    _, out = folders(c)
    taken, steps = set(), []
    for r in rows:
        s = dict(r)
        if r["dup_of"] and c["duplicates"] == "skip":
            s["dest"], s["skip"] = None, "duplicate"
            steps.append(s)
            continue
        d = "_Duplicates" if r["dup_of"] and c["duplicates"] == "separate" else _dest_dir(r, c)
        name = r["name"]
        if c["rename"] == "date-prefix":
            name = f'{datetime.fromtimestamp(r["mtime"]):%Y-%m-%d} {name}'
        stem, ext = (name.rsplit(".", 1)[0], "." + name.rsplit(".", 1)[1]) if "." in name[1:] else (name, "")
        cand, i = f"{d}/{name}", 2
        if cand.lower() not in taken and _same(out / cand, r):
            s["dest"], s["skip"] = None, "already sorted"
            steps.append(s)
            continue
        while cand.lower() in taken or (out / cand).exists():
            if c["on_conflict"] == "skip":
                cand = None
                break
            cand, i = f"{d}/{stem} ({i}){ext}", i + 1
        if cand is None:
            s["dest"], s["skip"] = None, "name taken"
        else:
            taken.add(cand.lower())
            s["dest"], s["skip"] = cand, None
        steps.append(s)
    return steps


def summary(steps, skipped=None):
    kinds = defaultdict(lambda: [0, 0])
    dups = [s for s in steps if s["dup_of"]]
    for s in steps:
        if not s["dup_of"]:
            kinds[s["kind"]][0] += 1
            kinds[s["kind"]][1] += s["size"]
    return {"files": len(steps), "bytes": sum(s["size"] for s in steps), "duplicates": len(dups), "duplicate_bytes": sum(s["size"] for s in dups),
            "to_write": sum(1 for s in steps if s["dest"]), "kinds": {k: {"files": n, "bytes": b} for k, (n, b) in kinds.items()}, "skipped": skipped or {}}


def _inside(p: Path, root: Path) -> bool:
    p, root = p.resolve(), root.resolve()
    return p == root or root in p.parents


def apply(steps, c, progress=lambda i, step: None, should_stop=lambda: False):
    """Carry out the plan. Writes report.csv and a manifest (.cleaner/<run>.json) that grows as files are done, so a crash can still be undone."""
    src, out = folders(c)
    run = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    mdir = out / ".cleaner"
    mdir.mkdir(parents=True, exist_ok=True)
    if c["action"] == "copy":  # copies need room; moves on the same drive don't
        need = sum(s["size"] for s in steps if s["dest"])
        free = shutil.disk_usage(out.parent if not out.exists() else out).free
        if need > free * .95:
            raise RuntimeError(f"not enough free space for the copies: they need {need / 2**30:.1f} GB, the drive has {free / 2**30:.1f} GB. Use move, or a sorted folder on another drive")
    head = {"run": run, "action": c["action"], "source": str(src), "output": str(out), "started": time.time(), "finished": None}
    hpath, jpath = mdir / f"{run}.json", mdir / f"{run}.jsonl"
    hpath.write_text(json.dumps(head))
    done = []
    # The journal gets one line per file the moment it is done, so a crash or a killed process can't lose a move.
    with jpath.open("a", encoding="utf-8") as journal:
        try:
            for i, s in enumerate(steps):
                if should_stop():
                    break
                if not s["dest"]:
                    progress(i, s)
                    continue
                a, b = src / s["rel"], out / s["dest"]
                if not (_inside(a, src) and _inside(b, out)):
                    raise RuntimeError(f"path outside the chosen folders: {a} -> {b}")
                try:
                    st = os.stat(a)
                    if st.st_size != s["size"] or abs(st.st_mtime - s["mtime"]) > 1e-3:
                        s["skip"] = "changed since scan"
                    elif b.exists():
                        s["skip"] = "name taken"
                    else:
                        b.parent.mkdir(parents=True, exist_ok=True)
                        if c["action"] == "move":
                            os.replace(a, b) if os.stat(a.parent).st_dev == os.stat(b.parent).st_dev else shutil.move(str(a), str(b))
                        else:
                            shutil.copy2(a, b)
                        bst = os.stat(b)
                        d = {"from": s["rel"], "to": s["dest"], "size": bst.st_size, "mtime": bst.st_mtime}
                        journal.write(json.dumps(d) + "\n")
                        journal.flush()
                        done.append(d)
                except FileNotFoundError:
                    s["skip"] = "gone since scan"
                except OSError as e:  # in use by another app, no permission, path too long...
                    s["skip"] = f"error: {e.strerror or e}"
                progress(i, s)
        finally:
            journal.flush()
            os.fsync(journal.fileno())
            head["finished"] = time.time()
            hpath.write_text(json.dumps(head))
    manifest = dict(head, done=done)
    with (out / "report.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["original", "sorted_to", "kind", "bytes", "duplicate_of", "skipped"])
        for s in steps:
            w.writerow([s["rel"], "" if s.get("skip") else s["dest"] or "", s["kind"], s["size"], s["dup_of"] or "", s.get("skip") or ""])
    return manifest


def runs(c):
    """Past runs in the output folder, newest first."""
    _, out = folders(c)
    found = []
    for p in sorted((out / ".cleaner").glob("*.json"), reverse=True):
        try:
            m = json.loads(p.read_text())
            found.append({"run": m["run"], "action": m["action"], "files": len(_journal(p)), "undone": m.get("undone"), "started": m["started"]})
        except (OSError, ValueError, KeyError):
            pass
    return found


def _journal(hpath: Path):
    """The files a run finished, from its journal. A half-written last line (a crash mid-write) is ignored."""
    j = hpath.with_suffix(".jsonl")
    done = []
    if j.exists():
        for line in j.read_text(encoding="utf-8").splitlines():
            try:
                done.append(json.loads(line))
            except ValueError:
                pass
    return done


def undo(c, run, progress=lambda i, n: None):
    """Reverse one run. Moves go back to where they were; copies this tool made are removed if unchanged. Returns counts."""
    src, out = folders(c)
    mpath = out / ".cleaner" / f"{run}.json"
    m = json.loads(mpath.read_text())
    m["done"] = _journal(mpath)
    if m.get("undone"):
        raise ValueError("this run was already undone")
    msrc, mout = Path(m["source"]), Path(m["output"])
    back = kept = 0
    for i, d in enumerate(reversed(m["done"])):
        a, b = msrc / d["from"], mout / d["to"]
        if not (_inside(a, msrc) and _inside(b, mout)):
            raise RuntimeError("manifest points outside its folders")
        try:
            st = os.stat(b)
        except FileNotFoundError:
            kept += 1
            continue
        same = st.st_size == d["size"] and abs(st.st_mtime - d["mtime"]) < 1e-3
        if m["action"] == "move":
            if same and not a.exists():
                a.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(b), str(a))
                back += 1
            else:
                kept += 1
        else:
            if same and a.exists():  # only our own unchanged copy, and only while the original is still there
                os.remove(b)
                back += 1
            else:
                kept += 1
        progress(i, len(m["done"]))
    for dirpath, dirnames, filenames in os.walk(mout, topdown=False):  # tidy up folders this run left empty
        p = Path(dirpath)
        if p != mout and ".cleaner" not in p.parts and not any(p.iterdir()):
            p.rmdir()
    m["undone"] = {"at": time.time(), "reversed": back, "left": kept}
    mpath.write_text(json.dumps({k: v for k, v in m.items() if k != "done"}))
    return m["undone"]
