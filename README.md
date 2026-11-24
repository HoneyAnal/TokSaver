# TokSaver

A desktop TikTok downloader with a per-account database — an alternative to 4K Tokkit.
Add accounts once, press **Sync all**, and only new posts are downloaded from then on.

English · [Русский](README.ru.md)

> **Disclaimer.** TokSaver is an independent project, not affiliated with or endorsed by TikTok.
> Use it only for content you have the right to save, and respect TikTok's terms and local law.

## Features

- **Accounts with a database** — every account has its own folder and SQLite DB; videos *and* photo carousels, resumable queue, "outside range" tracking.
- **Sync all / Stop all** — look for new posts and finish the pending queue for every account; a summary (new, downloaded, failed, size, time) is shown at the end.
- **Auto-sync** — off / every 1, 3, 6, 12 hours / daily at a chosen time (while the app is open).
- **Posts window** — list or tile view, sorting, filters, cached previews; photo posts show every picture of the post.
- **Cookies** from a browser or a `cookies.txt` (remembered between runs) for private/age-restricted content.
- **Parallel downloads** (1–4) with automatic back-off if TikTok starts limiting requests.
- **Smooth UI** — dark and light themes, colour-coded log, right-click menus.
- **Languages** — English, Русский, Українська, Deutsch, Français, Español.
- **Safe by design** — yt-dlp and ByeDPI are verified by SHA-256, cookies are only sent to tiktok.com, logs are redacted, archives are extracted safely.

Under the hood: [yt-dlp](https://github.com/yt-dlp/yt-dlp) (videos), [gallery-dl](https://github.com/mikf/gallery-dl) (photo posts),
[ByeDPI](https://github.com/hufrea/byedpi) (optional, Windows only: bypass of DPI blocking).

## Download

Grab the archive for your system from the **Releases** page, unpack it and run `TokSaver`
(`TokSaver.exe` on Windows). Nothing else needs to be installed.

| System | File | Notes |
|---|---|---|
| Windows 10/11 | `TokSaver-windows-x86_64.zip` | SmartScreen may warn about an unsigned app: *More info → Run anyway*. |
| macOS (Apple Silicon) | `TokSaver-macos-arm64.zip` | Unsigned: right-click → *Open*, or `xattr -dr com.apple.quarantine TokSaver`. |
| Linux x86-64 | `TokSaver-linux-x86_64.zip` | `chmod +x TokSaver` if needed. Requires a desktop session (X11/Wayland). |

r data (`data/`, `bin/`, `logs/`) is created next to the program, so the folder is portable.
On first start the app downloads and verifies `yt-dlp` itself.

## Run from source

Requires Python 3.10+ with tkinter.

```bash
git clone https://github.com/HoneyAnal/TokSaver.git
cd TokSaver
python main.py          # Windows: double-click "Запустить.bat"
```

The first start creates a private `.venv` and installs the pinned `requirements.txt`
(disable with `TOKSAVER_NO_VENV=1`).

Linux: install tkinter first (`sudo apt install python3-tk`). ByeDPI is not used outside Windows.

## Build the executable yourself

```bash
pip install -r requirements.txt pyinstaller
python build_exe.py      # → dist/TokSaver-<platform>-<arch>.zip
```

Pushing a tag like `v1.0.0` makes GitHub Actions (`.github/workflows/build.yml`) build
Windows, macOS and Linux packages and attach them to a GitHub Release.

## Tests

```bash
pip install -r requirements.txt pytest
python -m pytest tests        # UI tests need tkinter (on Linux: xvfb-run -a python -m pytest tests)
```

## Adding a language

Drop `lang/<code>.json` next to the others:

```json
{"_name": "Polski", "strings": {"Add and download": "Dodaj i pobierz"}}
```

The key is the English text (see `lang/ru.json`); missing strings fall back to English.

## Settings

Stored in `data/settings.json`: theme, language, cookies source, auto-sync schedule,
parallel downloads, sorting and view modes. More detail (Russian): [docs/FEATURES.ru.txt](docs/FEATURES.ru.txt).

## Contributing

Issues and pull requests are welcome. Please run the tests before submitting.
