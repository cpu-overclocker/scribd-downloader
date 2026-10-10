import base64
import io
import os
import re
import sys
import time
import tempfile
from datetime import datetime
from urllib.parse import unquote, urlparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from PIL import Image

# ============================================================
#  CONFIGURATION
# ============================================================
HEADLESS_ENABLED = True     # False to see Chrome
CAPTURE_SCALE = 2           # 1 = fast, 2 = quality, 3 = very heavy
RESTART_BROWSER_EVERY = 5   # Restart Chrome every N documents (avoids memory leaks)
DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS = 120
DEFAULT_PAGE_LOAD_CONCURRENCY = 8
OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "ScribdDownloads")
LOG_FILE = os.path.join(OUTPUT_DIR, "scribd_bulk.log")
# ============================================================


def log(msg, also_print=True):
    """Write to the log file + console."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    if also_print:
        print(msg)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def build_chrome_options(runtime_profile_dir):
    options = Options()
    if HEADLESS_ENABLED:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1600,2200")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument(f"--user-data-dir={runtime_profile_dir}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--hide-scrollbars")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    return options


def convert_scribd_link(url):
    match = re.search(r"scribd\.com/(?:document|doc|embeds)/(\d+)", url)
    if not match:
        return None
    return f"https://www.scribd.com/embeds/{match.group(1)}/content"


def get_filename_from_url(url):
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    last_segment = path.split("/")[-1] if path else "scribd_document"
    last_segment = unquote(last_segment)
    # Sanitize forbidden Windows characters
    last_segment = re.sub(r'[\\/:*?"<>|]', '_', last_segment)[:150]
    return f"{last_segment}.pdf"


def hide_cookie_dialogs(driver):
    try:
        driver.execute_script("""
            const sels = ['[class*="cookie"]','[class*="consent"]','[class*="gdpr"]',
                          '[id*="cookie"]','[id*="consent"]','#onetrust-consent-sdk',
                          '.cc-window','[class*="osano"]'];
            sels.forEach(s => { try { document.querySelectorAll(s).forEach(e => e.remove()); } catch(e){} });
        """)
    except Exception:
        pass


def load_all_pages(driver):
    """Load all pages via window.docManager. Returns the number of pages."""
    page_count = driver.execute_script("""
        return (typeof window.docManager !== 'undefined'
                && window.docManager !== null
                && typeof window.docManager.pages !== 'undefined')
               ? Object.values(window.docManager.pages).filter(Boolean).length
               : 0;
    """)
    if page_count == 0:
        return 0

    batch_count = max(1, (page_count + DEFAULT_PAGE_LOAD_CONCURRENCY - 1) // DEFAULT_PAGE_LOAD_CONCURRENCY)
    document_timeout_seconds = DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS * batch_count
    driver.set_script_timeout(document_timeout_seconds + 10)

    result = driver.execute_async_script("""
        const concurrency = arguments[0];
        const timeoutMs = arguments[1];
        const documentTimeoutMs = arguments[2];
        const done = arguments[arguments.length - 1];
        const manager = window.docManager;
        if (!manager || !manager.pages) { done({supported: false}); return; }
        const pages = Object.values(manager.pages).filter(Boolean);
        const pending = pages.filter((p) => !p.innerPageElem);
        const active = new Map();
        const failed = [];
        const startedAt = Date.now();
        let nextIndex = 0;
        let completed = pages.length - pending.length;
        let finished = false;

        function finish() {
            if (finished) return;
            finished = true;
            clearInterval(timer);
            pages.forEach((p) => {
                if (!p.innerPageElem) return;
                try { p.display(); } catch(e){}
                try { p.turnOnImages(); } catch(e){}
            });
            done({ supported: true, total: pages.length,
                   loaded: pages.filter(p => p.innerPageElem).length, failed });
        }
        function launchMore() {
            while (active.size < concurrency && nextIndex < pending.length) {
                const p = pending[nextIndex++];
                try {
                    if (!p.loadHasStarted) { p.load(); }
                    active.set(p.pageNum, { page: p, startedAt: Date.now() });
                } catch(e) { failed.push({ pageNum: p.pageNum, reason: String(e) }); }
            }
            if (completed + failed.length >= pages.length) { finish(); }
        }
        const timer = setInterval(() => {
            for (const [num, st] of active) {
                if (st.page.innerPageElem) { active.delete(num); completed += 1; continue; }
                if (Date.now() - st.startedAt >= timeoutMs) {
                    active.delete(num);
                    failed.push({ pageNum: num, reason: 'timeout' });
                }
            }
            if (Date.now() - startedAt >= documentTimeoutMs) {
                active.clear(); finish(); return;
            }
            launchMore();
        }, 50);
        launchMore();
    """, DEFAULT_PAGE_LOAD_CONCURRENCY,
         DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS * 1000,
         document_timeout_seconds * 1000)

    if not result.get("supported"):
        return 0
    return result["loaded"]


def capture_page_via_cdp(driver, element, scale=2):
    rect = driver.execute_script("""
        const el = arguments[0];
        const r = el.getBoundingClientRect();
        return { x: r.left + window.scrollX, y: r.top + window.scrollY,
                 width: r.width, height: r.height };
    """, element)
    if not rect or rect["width"] < 1 or rect["height"] < 1:
        return None
    clip = {
        "x": max(0, rect["x"]),
        "y": max(0, rect["y"]),
        "width": max(1, rect["width"]),
        "height": max(1, rect["height"]),
        "scale": scale,
    }
    result = driver.execute_cdp_cmd("Page.captureScreenshot", {
        "format": "png",
        "captureBeyondViewport": True,
        "fromSurface": True,
        "clip": clip,
    })
    png = base64.b64decode(result["data"])
    return Image.open(io.BytesIO(png)).convert("RGB")


def unique_path(path):
    if not os.path.exists(path):
        return path
    name, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(f"{name}_{i}{ext}"):
        i += 1
    return f"{name}_{i}{ext}"


def process_one_document(driver, scribd_url):
    """
    Process one document. Returns (ok, message).
    """
    embed_url = convert_scribd_link(scribd_url)
    if not embed_url:
        return False, "Invalid Scribd URL"

    filename = get_filename_from_url(scribd_url)
    filename = unique_path(os.path.join(OUTPUT_DIR, filename))

    # Loading
    driver.get(embed_url)
    time.sleep(3)
    hide_cookie_dialogs(driver)

    # Wait for docManager
    for _ in range(30):
        ready = driver.execute_script("""
            return (typeof window.docManager !== 'undefined'
                    && window.docManager !== null
                    && typeof window.docManager.pages !== 'undefined') ? 1 : 0;
        """)
        if ready:
            break
        time.sleep(1)
    else:
        return False, "docManager not detected"

    # Load all pages
    loaded = load_all_pages(driver)
    if loaded == 0:
        return False, "no pages loaded"
    time.sleep(2)

    # Get page elements
    page_elements = driver.find_elements(By.CSS_SELECTOR, ".outer_page")
    if not page_elements:
        for sel in ["div[id^='page']", ".newpage", "div.page"]:
            page_elements = driver.find_elements(By.CSS_SELECTOR, sel)
            page_elements = [e for e in page_elements
                             if e.rect.get("width", 0) > 300 and e.rect.get("height", 0) > 300]
            if page_elements:
                break

    if not page_elements:
        return False, "no page elements found"

    # Capture
    images = []
    for i, el in enumerate(page_elements):
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
            time.sleep(0.3)
            img = capture_page_via_cdp(driver, el, scale=CAPTURE_SCALE)
            if img:
                images.append(img)
        except Exception as e:
            log(f"      ⚠️ page {i+1} failed: {e}", also_print=False)

    if not images:
        return False, "no images captured"

    # Merge
    try:
        first, rest = images[0], images[1:]
        first.save(filename, "PDF", save_all=True, append_images=rest)
    except Exception as e:
        return False, f"write error: {e}"

    size_kb = os.path.getsize(filename) / 1024
    return True, f"{len(images)} pages, {size_kb:.0f} KB → {os.path.basename(filename)}"


def run_bulk(urls):
    """Process a list of URLs."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    log("=" * 60)
    log(f"🚀 BULK EXPORT — {len(urls)} document(s)")
    log(f"📁 Output folder: {OUTPUT_DIR}")
    log("=" * 60)

    ok_count = 0
    fail_count = 0
    failures = []
    start_time = time.time()

    driver = None
    processed_since_restart = 0

    try:
        for idx, url in enumerate(urls, 1):
            url = url.strip()
            if not url or url.startswith("#"):
                continue

            # Periodically restart the browser
            if driver is None or processed_since_restart >= RESTART_BROWSER_EVERY:
                if driver is not None:
                    log(f"   🔄 Restarting browser (memory)...")
                    try:
                        driver.quit()
                    except Exception:
                        pass
                profile_dir = tempfile.mkdtemp(prefix="scribd-profile-")
                options = build_chrome_options(profile_dir)
                driver = webdriver.Chrome(options=options)
                processed_since_restart = 0

            log(f"\n[{idx}/{len(urls)}] {url}")
            try:
                ok, msg = process_one_document(driver, url)
            except Exception as e:
                ok, msg = False, f"exception: {e}"

            if ok:
                ok_count += 1
                log(f"   ✅ {msg}")
            else:
                fail_count += 1
                failures.append((url, msg))
                log(f"   ❌ {msg}")
                # On serious error, restart the browser
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = None
                processed_since_restart = 0
                continue

            processed_since_restart += 1

    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

    elapsed = time.time() - start_time
    log("\n" + "=" * 60)
    log(f"📊 SUMMARY: {ok_count} ✅ / {fail_count} ❌  ({elapsed:.0f}s)")
    log("=" * 60)

    if failures:
        log("\n❌ Failures:")
        for url, msg in failures:
            log(f"   • {url}\n     → {msg}")

    # Failure file for retry
    if failures:
        fail_file = os.path.join(OUTPUT_DIR, "failed_urls.txt")
        with open(fail_file, "w", encoding="utf-8") as f:
            for url, _ in failures:
                f.write(url + "\n")
        log(f"\n💡 Failed links saved to: {fail_file}")
        log("   → Run the script again with this file to retry.")


def read_urls_interactive():
    """Ask the user for URLs, one per line. Empty line = end."""
    print("Paste Scribd links (one per line). Empty line to finish.")
    print("-" * 60)
    urls = []
    while True:
        try:
            line = input().strip()
        except EOFError:
            break
        if not line:
            break
        urls.append(line)
    return urls


def read_urls_from_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]


def main():
    print("=" * 60)
    print("       📚 SCRIBD BULK DOWNLOADER 📚")
    print("=" * 60)
    print()
    print("1. Enter link(s) manually")
    print("2. Load from a .txt file")
    print()

    choice = input("Choice [1/2]: ").strip()

    if choice == "2":
        path = input("Path to .txt file: ").strip().strip('"')
        if not os.path.isfile(path):
            print(f"❌ File not found: {path}")
            return
        urls = read_urls_from_file(path)
        if not urls:
            print("❌ No links found in file.")
            return
    else:
        urls = read_urls_interactive()

    if not urls:
        print("❌ No links provided.")
        return

    print(f"\n✅ {len(urls)} link(s) to process.")
    confirm = input("Start? [Y/n]: ").strip().lower()
    if confirm and confirm != "y":
        print("Cancelled.")
        return

    run_bulk(urls)
    input("\n⏸️  Press Enter to quit...")


if __name__ == "__main__":
    main()