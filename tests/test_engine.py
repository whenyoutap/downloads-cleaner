"""python3 -m unittest discover -s tests   (from the downloads-cleaner folder)"""
import hashlib, os, sys, tempfile, time, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cleaner import engine  # noqa: E402


def snapshot(root: Path):
    """Every file under root: relative path -> (size, mtime, sha256)."""
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = (p.stat().st_size, p.stat().st_mtime, hashlib.sha256(p.read_bytes()).hexdigest())
    return out


class Engine(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "Downloads"
        (self.src / "sub").mkdir(parents=True)
        files = {"invoice.pdf": b"pdf-1", "invoice (1).pdf": b"pdf-1", "sub/invoice_copy.pdf": b"pdf-1", "Screenshot 2025-01-01.png": b"png",
                 "photo.jpg": b"jpg-photo", "setup.exe": b"x" * 3000, "notes.txt": b"notes", ".hidden.txt": b"h", "song.mp3": b"mp3",
                 "big.zip": b"z" * 200000}
        t = time.time() - 400 * 86400
        for i, (name, data) in enumerate(files.items()):
            p = self.src / name
            p.write_bytes(data)
            os.utime(p, (t + i * 86400, t + i * 86400))  # invoice.pdf is the oldest of the three copies
        self.before = snapshot(self.src)

    def cfg(self, **kw):
        return engine.config({"source": str(self.src), "recurse": True, **kw})

    def run_all(self, c):
        rows, skipped = engine.scan(c)
        steps = engine.plan(rows, c)
        return steps, engine.apply(steps, c)

    def test_copy_leaves_originals_and_undo_removes_only_copies(self):
        c = self.cfg()
        steps, m = self.run_all(c)
        self.assertEqual(snapshot(self.src), self.before)
        out = self.tmp / "Downloads (sorted)"
        self.assertTrue((out / "_Duplicates").is_dir())
        self.assertEqual(len(list((out / "_Duplicates").iterdir())), 2)
        self.assertTrue(any(p.name == "Screenshot 2025-01-01.png" for p in (out / "Screenshots").rglob("*")))
        self.assertFalse(any(".hidden" in s["rel"] for s in steps))
        r = engine.undo(c, m["run"])
        self.assertEqual(r["reversed"], len(m["done"]))
        self.assertEqual(snapshot(self.src), self.before)
        self.assertEqual([p for p in out.rglob("*") if p.is_file() and ".cleaner" not in p.parts and p.name != "report.csv"], [])

    def test_move_and_undo_restore_every_byte(self):
        c = self.cfg(action="move")
        steps, m = self.run_all(c)
        left = [p for p in self.src.rglob("*") if p.is_file()]
        self.assertEqual([p.name for p in left], [".hidden.txt"])  # hidden file was skipped, everything else moved
        engine.undo(c, m["run"])
        self.assertEqual(snapshot(self.src), self.before)
        with self.assertRaises(ValueError):
            engine.undo(c, m["run"])

    def test_duplicate_options(self):
        rows, _ = engine.scan(self.cfg())
        dups = [r for r in rows if r["dup_of"]]
        self.assertEqual(len(dups), 2)
        self.assertTrue(all(r["dup_of"] == "invoice.pdf" for r in dups))  # oldest kept
        rows, _ = engine.scan(self.cfg(keep="newest"))
        self.assertEqual({r["dup_of"] for r in rows if r["dup_of"]}, {str(Path("sub/invoice_copy.pdf"))})
        skip = engine.plan(engine.scan(self.cfg(duplicates="skip"))[0], self.cfg(duplicates="skip"))
        self.assertEqual(sum(1 for s in skip if s["skip"] == "duplicate"), 2)
        keep = engine.plan(engine.scan(self.cfg(duplicates="keep"))[0], self.cfg(duplicates="keep"))
        self.assertFalse(any((s["dest"] or "").startswith("_Duplicates") for s in keep))

    def test_filters_layout_rename_conflict(self):
        rows, skipped = engine.scan(self.cfg(min_mb=0.1))
        self.assertEqual([r["name"] for r in rows], ["big.zip"])
        self.assertEqual(skipped["size filter"], 8)
        rows, _ = engine.scan(self.cfg(include_kinds=["Documents"]))
        self.assertTrue(all(r["kind"] == "Documents" for r in rows))
        rows, _ = engine.scan(self.cfg(recurse=False))
        self.assertFalse(any(r["rel"].startswith("sub") for r in rows))
        rows, _ = engine.scan(self.cfg(skip_hidden=False))
        self.assertTrue(any(r["name"] == ".hidden.txt" for r in rows))
        c = self.cfg(layout="year", rename="date-prefix", duplicates="keep")
        steps = engine.plan(engine.scan(c)[0], c)
        self.assertTrue(all(s["dest"].split("/")[0].isdigit() for s in steps))
        self.assertTrue(all(s["dest"].split("/")[-1][:4].isdigit() for s in steps))
        c = self.cfg(layout="kind", duplicates="keep")
        names = [s["dest"] for s in engine.plan(engine.scan(c)[0], c) if s["kind"] == "Documents"]
        self.assertIn("Documents/invoice_copy.pdf", names)
        self.assertEqual(len(names), len(set(n.lower() for n in names)))

    def test_second_run_skips_files_already_sorted(self):
        c = self.cfg()
        self.run_all(c)
        steps = engine.plan(engine.scan(c)[0], c)
        self.assertTrue(all(s["skip"] == "already sorted" for s in steps))

    def test_per_file_errors_are_skipped_not_fatal(self):
        c = self.cfg()
        steps = engine.plan(engine.scan(c)[0], c)
        (self.src / "song.mp3").unlink()
        real = engine.shutil.copy2

        def copy2(a, b):  # notes.txt fails the way a file in use or without permission does, on any system
            if Path(a).name == "notes.txt":
                raise PermissionError(13, "Permission denied")
            return real(a, b)
        engine.shutil.copy2 = copy2
        try:
            m = engine.apply(steps, c)
        finally:
            engine.shutil.copy2 = real
        why = {s["name"]: s["skip"] for s in steps}
        self.assertEqual(why["song.mp3"], "gone since scan")
        self.assertTrue(why["notes.txt"].startswith("error"))
        report = (self.tmp / "Downloads (sorted)" / "report.csv").read_text()
        self.assertIn("notes.txt,,", report)  # a file that failed has no destination in the report
        self.assertEqual(len(m["done"]), sum(1 for s in steps if s["dest"] and not s["skip"]))

    def test_crash_mid_move_is_fully_undoable(self):
        c = self.cfg(action="move")
        steps = engine.plan(engine.scan(c)[0], c)

        def crash(i, s):
            if i == 4:
                raise KeyboardInterrupt  # the process is stopped partway through
        with self.assertRaises(KeyboardInterrupt):
            engine.apply(steps, c, crash)
        runs = engine.runs(c)
        self.assertEqual(len(runs), 1)
        self.assertGreater(runs[0]["files"], 0)
        engine.undo(c, runs[0]["run"])
        self.assertEqual(snapshot(self.src), self.before)

    def test_bad_configs(self):
        for bad in ({"action": "delete"}, {"layout": "x"}, {"min_mb": -1}, {"nope": 1}, {"include_kinds": ["Nope"]}, {"min_mb": 5, "max_mb": 1},
                    {"kinds": {"../x": "pdf"}}, {"output": str(self.src / "inside")}):
            with self.assertRaises(ValueError):
                c = engine.config({"source": str(self.src), **bad})
                engine.folders(c)

    def test_changed_file_is_skipped(self):
        c = self.cfg(action="move")
        rows, _ = engine.scan(c)
        steps = engine.plan(rows, c)
        (self.src / "notes.txt").write_bytes(b"edited after the scan")
        engine.apply(steps, c)
        self.assertTrue((self.src / "notes.txt").exists())
        self.assertEqual([s["skip"] for s in steps if s["name"] == "notes.txt"], ["changed since scan"])


if __name__ == "__main__":
    unittest.main()
