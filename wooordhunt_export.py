#!/usr/bin/env python3
"""
Export your personal vocabulary ("Мой словарь") from wooordhunt.ru.

Usage:
    python3 wooordhunt_export.py            # uses Chrome
    python3 wooordhunt_export.py firefox    # uses Firefox

Output:
    wooordhunt_vocabulary.csv   (UTF-8 with BOM, opens fine in Numbers/Excel)
    vocabulary.txt              (word - translation)
    debug_page_N.html           (raw HTML of each scraped page, for troubleshooting)
"""

import csv
import re
import sys
import time

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By

START_URL = "https://wooordhunt.ru/user/preentry"
OUT_CSV = "wooordhunt_vocabulary.csv"
OUT_TXT = "vocabulary.txt"

# Text/labels that usually mark a "next page" control
NEXT_LABELS = {
    "»",
    "›",
    ">",
    ">>",
    "след",
    "следующая",
    "далее",
    "вперед",
    "вперёд",
    "next",
}

RE_LATIN_WORD = re.compile(r"^[A-Za-z][A-Za-z\s\-'’.()/]*$")
RE_NON_LATIN = re.compile(r"[\u0400-\u04FF\u0530-\u058F]")  # Cyrillic + Armenian
RE_TRANSCRIPTION = re.compile(r"(\|[^|]{1,60}\||\[[^\]]{1,60}\])")


# ----------------------------------------------------------------- helpers
def clean(text: str) -> str:
    """Collapse whitespace / non-breaking spaces and strip."""
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def row_from_parts(parts):
    """Turn a list of text fragments (cells or lines) into a vocabulary entry."""
    parts = [clean(p) for p in parts if clean(p)]
    if len(parts) < 2:
        return None

    word, transcription, translation, extra = "", "", [], []
    for p in parts:
        m = RE_TRANSCRIPTION.search(p)
        if m and not word and RE_LATIN_WORD.match(p[: m.start()].strip() or "x"):
            # "word [wɜːd]" in one fragment
            before = p[: m.start()].strip()
            if before:
                word = before
            transcription = m.group(1).strip("|[] ")
            continue
        if m and not transcription and len(p) <= 62 and not RE_NON_LATIN.search(p):
            transcription = m.group(1).strip("|[] ")
            continue
        if not word and RE_LATIN_WORD.match(p):
            word = p
        elif RE_NON_LATIN.search(p):
            translation.append(p)
        else:
            extra.append(p)

    if not word or not translation:
        return None
    return {
        "word": word,
        "transcription": transcription,
        "translation": "; ".join(translation),
        "extra": " | ".join(extra),
    }


def parse_page(html: str):
    """Extract vocabulary entries from one page of HTML."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()

    entries = []

    # Strategy 1: table rows
    for tr in soup.select("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        e = row_from_parts(cells)
        if e:
            entries.append(e)
    if entries:
        return entries

    # Strategy 2: list items / div rows (innermost elements only)
    selector = (
        "li, div[class*=row], div[class*=item], div[class*=word], div[class*=line], p"
    )
    for el in soup.select(selector):
        if el.select(selector):  # skip containers, keep leaf rows
            continue
        # prefer child elements as separate fragments, fall back to text lines
        parts = [
            c.get_text(" ", strip=True)
            for c in el.find_all(["span", "a", "b", "i", "div"])
        ]
        e = row_from_parts(parts) or row_from_parts(el.get_text("\n").split("\n"))
        if e:
            entries.append(e)
    return entries


def find_next_button(driver):
    """Return a clickable 'next page' element, or None."""
    candidates = driver.find_elements(
        By.XPATH, "//a | //button | //span[@onclick] | //div[@onclick] | //li"
    )
    for el in candidates:
        try:
            label = clean(el.text).lower()
            title = (el.get_attribute("title") or "").lower()
            cls = (el.get_attribute("class") or "").lower()
            if "disabled" in cls or not el.is_displayed():
                continue
            if label in NEXT_LABELS or title in NEXT_LABELS or "next" in cls:
                return el
        except Exception:
            continue
    return None


def make_driver(browser: str):
    if browser == "firefox":
        return webdriver.Firefox()
    opts = webdriver.ChromeOptions()
    opts.add_argument("--window-size=1200,900")
    return webdriver.Chrome(options=opts)  # Selenium 4.6+ fetches chromedriver itself


# -------------------------------------------------------------------- main
def main():
    browser = sys.argv[1].lower() if len(sys.argv) > 1 else "chrome"
    driver = make_driver(browser)

    try:
        driver.get(START_URL)
        print("\n" + "=" * 60)
        print("1) Log in to WooordHunt in the opened browser window.")
        print("2) Open the «Мой словарь» tab (https://wooordhunt.ru/history/top).")
        print("3) Make sure the FIRST page of your vocabulary is visible.")
        print("=" * 60)
        input("Press ENTER here when you're ready... ")

        all_entries, seen = [], set()
        page_no = 1

        while True:
            time.sleep(1.5)  # let JS finish rendering
            html = driver.page_source
            with open(f"debug_page_{page_no}.html", "w", encoding="utf-8") as f:
                f.write(html)

            new = 0
            for e in parse_page(html):
                key = (e["word"].lower(), e["translation"].lower())
                if key not in seen:
                    seen.add(key)
                    all_entries.append(e)
                    new += 1
            print(f"Page {page_no}: found {new} new entries (total {len(all_entries)})")

            # Try to go to the next page automatically
            btn = find_next_button(driver)
            if btn is not None:
                try:
                    driver.execute_script("arguments[0].click();", btn)
                    page_no += 1
                    if new == 0 and page_no > 2:
                        print("No new entries after clicking next - stopping.")
                        break
                    continue
                except Exception:
                    pass

            # Fallback: manual pagination
            ans = (
                input(
                    "No automatic 'next' button found. Go to the next page manually and press "
                    "ENTER to scrape it, or type 'q' + ENTER to finish: "
                )
                .strip()
                .lower()
            )
            if ans == "q":
                break
            page_no += 1

        if not all_entries:
            print(
                "\nNothing was extracted. Open debug_page_1.html and check how the words are "
                "structured - send me a snippet and I'll adjust the parser."
            )
            return

        with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(
                f, fieldnames=["word", "transcription", "translation", "extra"]
            )
            w.writeheader()
            w.writerows(all_entries)

        with open(OUT_TXT, "w", encoding="utf-8") as f:
            for e in all_entries:
                f.write(f"{e['word']} - {e['translation']}\n")

        print(f"\nDone! Saved {len(all_entries)} entries to {OUT_CSV} and {OUT_TXT}")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
