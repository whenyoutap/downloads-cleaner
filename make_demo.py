#!/usr/bin/env python3
"""Make a messy test Downloads folder, so you can try clean.py without touching your own files.

    python3 make_demo.py demo-downloads              # 2,175 files, about 29 GB as written (sparse on Linux/macOS: little real disk)
    python3 make_demo.py demo-downloads --scale 0.02 # the same files and duplicates at 2% of the size (about 0.6 GB)

Fixed seed, so every run makes the same folder: the same names, sizes, dates and duplicates.
Files are placeholders (a unique header, then zeros), not real photos or documents.
Duplicates are byte-identical copies under the names browsers and apps give them: "file (1).pdf", re-downloads, forwards.
"""
import os, random, sys
from datetime import datetime, timedelta
from pathlib import Path

R = random.Random(20261116)
MB = 1 << 20

# (name pattern, count, size range in MB, chance this file gets re-downloaded or copied)
FAMILIES = [
    ("IMG_{n4}.jpg", 520, (1.5, 6), .12),
    ("IMG-{date}-WA{n4}.jpg", 380, (.08, .4), .30),
    ("Screenshot {date} at {time}.png", 300, (.3, 2.5), .04),
    ("PXL_{date}_{n6}.mp4", 60, (20, 180), .10),
    ("VID-{date}-WA{n4}.mp4", 90, (2, 25), .30),
    ("invoice_{n4}.pdf", 70, (.05, .4), .25),
    ("{doc}.pdf", 110, (.1, 6), .30),
    ("{doc}.docx", 40, (.02, 1.5), .20),
    ("{sheet}.xlsx", 35, (.02, 2), .20),
    ("{installer}", 26, (60, 420), .55),
    ("{archive}.zip", 45, (1, 120), .25),
    ("{song}.mp3", 40, (3, 9), .10),
    ("{deck}.pptx", 18, (2, 40), .20),
    ("{code}", 30, (.001, .2), .05),
    ("{misc}", 25, (.01, 3), .05),
]
DOCS = ["resume", "offer_letter", "rent_agreement", "boarding_pass", "e-ticket", "bank_statement", "salary_slip", "aadhaar_update_form", "insurance_policy",
        "lecture_notes", "research_paper", "user_manual", "warranty_card", "tax_receipt", "visa_checklist", "hotel_booking", "medical_report", "course_certificate"]
SHEETS = ["budget", "expenses", "attendance", "inventory", "trip_plan", "marks", "sales_q3"]
INSTALLERS = ["ChromeSetup.exe", "VSCodeUserSetup-x64.exe", "zoom_installer.exe", "WhatsAppSetup.exe", "Spotify_Setup.exe", "python-3.12.4-amd64.exe",
              "node-v20.15.0-x64.msi", "Docker Desktop Installer.exe", "OBS-Studio-30.2-Windows.exe", "Steam_Setup.exe", "vlc-3.0.21-win64.exe",
              "Git-2.45.2-64-bit.exe", "AnyDesk.exe", "Telegram_Setup.exe", "Notion_Setup.exe", "Discord_Setup.exe", "7z2407-x64.exe", "Postman-win64-Setup.exe",
              "Figma_Setup.exe", "AdobeReader_Installer.exe", "android-studio.exe", "GoogleDriveSetup.exe", "TeamsSetup_x64.exe", "Slack_Setup.exe", "Brave_Setup.exe", "Canva_Setup.exe"]
ARCHIVES = ["project_files", "photos_backup", "assignment", "wallpapers", "fonts_pack", "dataset", "invoices_2024", "templates", "source_code", "drivers"]
SONGS = ["track", "song", "audio", "voice_note", "podcast_ep"]
DECKS = ["presentation", "pitch_deck", "seminar", "project_review", "final_ppt"]
CODE = ["main.py", "index.html", "config.json", "script.js", "data.csv", "notes.md", "styles.css", "query.sql", "analysis.ipynb", "setup.sh"]
MISC = ["unknown_file", "download", "file", "attachment", "temp"]
START, END = datetime(2022, 1, 1), datetime(2026, 11, 14)


def when():
    return START + timedelta(seconds=R.randrange(int((END - START).total_seconds())))


def name_for(pat, t, i):
    return pat.format(n4=f"{R.randrange(10000):04d}", n6=f"{R.randrange(10**6):06d}", date=t.strftime("%Y%m%d"), time=t.strftime("%H.%M.%S"),
                      doc=f"{R.choice(DOCS)}{R.choice(['', '_' + str(R.randrange(2019, 2027)), '_final', '_v2', ''])}", sheet=R.choice(SHEETS) + f"_{i}",
                      installer=INSTALLERS[i % len(INSTALLERS)], archive=f"{R.choice(ARCHIVES)}_{i}", song=f"{R.choice(SONGS)}_{i:03d}",
                      deck=f"{R.choice(DECKS)}_{i}", code=f"{i}_{R.choice(CODE)}", misc=f"{R.choice(MISC)}_{i}")


def _sparse(f):
    """On Windows, mark the file sparse so the zeros take no disk space (Linux and macOS do this on their own)."""
    if os.name != "nt":
        return
    import ctypes, msvcrt
    from ctypes import wintypes
    out = wintypes.DWORD()
    ctypes.windll.kernel32.DeviceIoControl(wintypes.HANDLE(msvcrt.get_osfhandle(f.fileno())), 0x900C4, None, 0, None, 0, ctypes.byref(out), None)  # FSCTL_SET_SPARSE


def write(p: Path, size: int, key: str, t: datetime):
    with p.open("wb") as f:
        _sparse(f)
        head = key.encode().ljust(64, b"\0")[:min(64, size)]
        f.write(head)
        if size > len(head):  # the rest reads as zeros: a hole, not written bytes (truncate writes zeros on Windows)
            f.seek(size - 1)
            f.write(b"\0")
    os.utime(p, (t.timestamp(), t.timestamp()))


def taken(d: Path, name: str) -> Path:
    p, stem, ext, i = d / name, Path(name).stem, Path(name).suffix, 1
    while p.exists():
        p, i = d / f"{stem} ({i}){ext}", i + 1
    return p


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Make a messy test Downloads folder.")
    ap.add_argument("folder", nargs="?", default="demo-downloads")
    ap.add_argument("--scale", type=float, default=1.0, help="multiply every file size (default 1)")
    a = ap.parse_args()
    d = Path(a.folder)
    d.mkdir(parents=True, exist_ok=True)
    if any(d.iterdir()):
        sys.exit(f"{d} is not empty")
    n = 0
    for pat, count, (lo, hi), redo in FAMILIES:
        for i in range(count):
            t = when()
            size = max(1, int(R.uniform(lo, hi) * MB * a.scale))
            p = taken(d, name_for(pat, t, i))
            key = f"{pat}|{i}|{size}"
            write(p, size, key, t)
            n += 1
            chance = redo
            while R.random() < chance:  # the same file again: "name (1).ext", or saved again under another name
                t2 = t + timedelta(days=R.randrange(0, 400))
                q = taken(d, p.name) if R.random() < .7 else taken(d, f"{p.stem}_copy{p.suffix}")
                write(q, size, key, min(t2, END))
                n += 1
                chance *= .5
    print(f"made {n} files in {d}")


if __name__ == "__main__":
    main()
