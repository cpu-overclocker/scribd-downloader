# 📚 Scribd Downloader

A Python tool to bulk-download Scribd documents as high-quality PDFs.
Uses Selenium + Chrome DevTools Protocol to bypass the paywall via the official
`/embeds/` endpoint, captures each page individually, and merges them into a
single PDF.

> ⚠️ **Disclaimer** — This tool is for **educational and personal use only**.
> Downloading copyrighted content without permission may violate Scribd's
> Terms of Service and applicable copyright law. Support the authors by
> purchasing or subscribing when possible. The maintainers are not responsible
> for any misuse.

---

## ✨ Features

- 🔗 **Bulk processing** — feed a list of URLs or a `.txt` file
- 🚀 **Paywall bypass** via Scribd's public `/embeds/` endpoint
- 🖼️ **No crop, no duplicate pages** — captures each page via CDP
  (`Page.captureScreenshot` with `captureBeyondViewport`)
- 🧠 **Direct page loader** using `window.docManager` (no fragile scrolling)
- ♻️ **Auto browser restart** every N documents to prevent memory leaks
- 🛡️ **Resilient** — one failed document doesn't stop the batch
- 📝 **Timestamped log** + `failed_urls.txt` for easy retry
- 🍪 **Cookie banner removal**
- 🎯 **Headless mode** by default

---

## 📦 Requirements

- **Python** 3.10+
- **Google Chrome** installed
- **ChromeDriver** — managed automatically by Selenium Manager (Selenium ≥ 4.6)

---

## 🚀 Installation

```bash
git clone https://github.com/cpu-overclocker/scribd-downloader.git
cd scribd-downloader

python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
```

---

## 🧑‍💻 Usage

### Interactive mode

```bash
python scribd_downloader.py
```

Then either:
1. Paste URLs one by one (empty line to finish), or
2. Provide a `.txt` file path (one URL per line, `#` for comments)

### Example `urls.txt`

```txt
# This line is ignored
https://www.scribd.com/document/987654321/Another-Document
```

### Output

PDFs are written to:

```
~/ScribdDownloads/
├── Another-Document.pdf
├── scribd_bulk.log
└── failed_urls.txt     # only if some URLs failed
```

---

## ⚙️ Configuration

Edit the constants at the top of `scribd_downloader.py`:

| Constant | Default | Description |
|---|---|---|
| `HEADLESS_ENABLED` | `True` | Run Chrome in headless mode |
| `CAPTURE_SCALE` | `2` | 1 = fast, 2 = quality, 3 = very heavy |
| `RESTART_BROWSER_EVERY` | `5` | Restart Chrome every N documents |
| `DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS` | `120` | Per-page timeout |
| `DEFAULT_PAGE_LOAD_CONCURRENCY` | `8` | Parallel page loads |

---

## 🧠 How it works

1. The user-provided URL is converted to its **embed** form:
   `https://www.scribd.com/document/<id>/...` → `https://www.scribd.com/embeds/<id>/content`
2. Chrome loads the embed page (no login required).
3. The script accesses `window.docManager.pages` and calls each page's
   `.load()` method concurrently.
4. Each `.outer_page` element is captured with
   `Page.captureScreenshot` + `captureBeyondViewport=True`, so the capture
   isn't limited to the viewport.
5. All images are merged into a single PDF with **Pillow**.

---

## 🐛 Troubleshooting

See [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

Common issues:
- **`SessionNotCreatedException`** → Chrome/ChromeDriver mismatch. Upgrade Selenium: `pip install --upgrade selenium`.
- **`PermissionError` when saving** → PDFs are saved in `~/ScribdDownloads`, not the current directory. Close any open PDF and try again.
- **`docManager non détecté`** → the document is private, deleted, or Scribd changed its DOM. Try `HEADLESS_ENABLED = False` to inspect visually.

---

## ⭐ Star history

If this project helps you, consider giving it a star — it motivates further work.
