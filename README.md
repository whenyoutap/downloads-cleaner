# Downloads cleaner

Sorts a messy folder (your Downloads, a phone backup, anything) into tidy folders and finds duplicate files, with a live 3D dashboard that shows every file as it goes. It runs entirely on your computer and never deletes anything.

## Run it

You need Python 3.8 or newer ([python.org](https://www.python.org/downloads/)). Nothing else to install.

```
python clean.py
```

Your browser opens the dashboard. Pick a folder, press **Scan**, look at the plan, press **Clean**.

- **Scan** reads every file, sorts it by kind, and finds duplicates by their content (same bytes, any name). Nothing is written.
- **Clean** carries out the plan. Each file flies to its folder as it is done.
- **Undo** (under Past runs) reverses a clean.
- **Replay** plays your last clean again, the same files in the same order, for a screen recording. Press `H` to hide the settings panel and `F` for full screen.

## Copy or move

- **Copy** (the default): sorted copies go to `<folder> (sorted)`. Your folder stays exactly as it was.
- **Move**: your files move into `<folder> (sorted)`. Every move is logged as it happens, and Undo puts every file back where it was.

Copy needs free space for the copies; it checks before it starts and stops if the drive is too full. Moves on the same drive need no extra space.

Neither deletes anything. Undo of a copy removes only the copies this tool made, and only if you haven't changed them. Cleaning the same folder again skips files that are already sorted.

## Every setting

The dashboard and the command line share one list of settings:

| Setting | Choices |
|---|---|
| Copy or move | copy, move |
| Where sorted files go | any folder outside the one you clean |
| Folder structure | kind, kind/month, kind/year, year/month, year |
| Duplicates | separate (into `_Duplicates`), skip (leave them), keep (sort like any file) |
| Which copy is the original | oldest, newest, shortest name |
| Rename | none, date prefix (`2025-03-14 invoice.pdf`) |
| Only these kinds | any of the kinds below |
| Screenshots in their own folder | yes, no |
| Size limits | smaller than / larger than, in MB |
| Age limits | not modified in N days / modified in the last N days |
| Hidden files | skip, include |
| Subfolders | skip (default: an unzipped project stays intact), include (their files are sorted too) |
| If a name is taken | number it (`file (2).pdf`), skip |
| Kinds | edit which extensions belong to Images, Videos, Documents... or add your own |

## From the command line

```
python clean.py "C:\Users\you\Downloads" --report                 # the plan, nothing written
python clean.py "C:\Users\you\Downloads" --apply --action move    # do it
python clean.py "C:\Users\you\Downloads" --runs                   # past runs
python clean.py "C:\Users\you\Downloads" --undo RUN               # reverse one
python clean.py --help                                            # every setting as a flag
```

## Privacy and safety

- No internet, no AI model, no telemetry, no accounts. The 3D library (three.js) ships inside this folder, so the page loads nothing from the web.
- The dashboard listens on 127.0.0.1 only. Every request needs a secret made fresh each time you start it, so other websites open in your browser can't use it.
- Every file it reads stays inside the folder you chose, and every file it writes stays inside the sorted folder.
- `report.csv` in the sorted folder lists every file: where it came from, where it went, and which file it duplicates. `.cleaner/` holds the log Undo uses.

## Try it without your own files

```
python make_demo.py demo-downloads              # 2,175 placeholder files with realistic names, 386 duplicates
python clean.py demo-downloads
```

The demo files are mostly empty space ("sparse"), so the 29 GB folder takes almost no real disk. `--scale 0.02` makes them small.

## Tests

```
python -m unittest discover -s tests
```

## License

MIT. three.js is MIT too (`web/vendor/three/LICENSE`).
