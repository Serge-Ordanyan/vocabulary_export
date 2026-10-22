#!/usr/bin/env python3
"""
Convert saved WooordHunt pages (debug_page_*.html) into clean vocabulary files
for Kindle / KOReader.

Usage (in the folder with the debug_page_*.html files):
    python build_kindle_vocab.py                 # alphabetical EPUB (A-Z chapters)
    python build_kindle_vocab.py --order recent  # keep WooordHunt order (newest first)
    python build_kindle_vocab.py --input-dir /path/to/folder

Output:
    wooordhunt_vocabulary.epub   <- copy to Kindle / KOReader device
    wooordhunt_vocabulary.csv    <- word, transcription, translation
    wooordhunt_vocabulary.txt    <- plain text
"""

import argparse
import csv
import datetime
import html
import re
import uuid
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup

RE_TRANS = re.compile(r"[|\[]([^|\[\]]{1,80})[|\]]")
RE_LATIN = re.compile(r"^[A-Za-z][A-Za-z '’\-.]*$")
JUNK_LINES = {"≡", "☰", "×", "✕", "✖"}
DASHES = "—–-"


def clean(text):
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


# ---------------------------------------------------------------- parsing
def list_anchors(soup):
    """Word links that belong to the vocabulary list only (not header/history/menu)."""
    marker = soup.find(
        string=re.compile("Папки")
    )  # the folders bar sits right above the list
    anchors = marker.find_all_next("a") if marker else soup.find_all("a")
    hits = [a for a in anchors if "/word/" in (a.get("href") or "")]
    if not hits:
        hits = [a for a in anchors if RE_LATIN.match(clean(a.get_text()))]
    return hits


def row_container(a, ids):
    """Climb from the word link to the element that wraps exactly this one row."""
    node = a
    while node.parent is not None and node.parent.name not in (
        "body",
        "html",
        "[document]",
    ):
        parent = node.parent
        if "Папки" in parent.get_text(" ", strip=True):
            break
        if sum(1 for x in parent.find_all("a") if id(x) in ids) > 1:
            break
        node = parent
    return node


def parse_row(a, container):
    word = clean(a.get_text())
    lines = [clean(t) for t in container.get_text("\n").split("\n")]
    text = " ".join(l for l in lines if l and l not in JUNK_LINES)
    if text.lower().startswith(word.lower()):
        text = text[len(word) :].strip()
    transcription = ""
    m = RE_TRANS.search(text)
    if m:
        transcription = m.group(1).strip()
        text = (text[: m.start()] + " " + text[m.end() :]).strip()
    translation = clean(text.lstrip(DASHES + ": "))
    return {"word": word, "transcription": transcription, "translation": translation}


def parse_file(path):
    soup = BeautifulSoup(
        path.read_text(encoding="utf-8", errors="replace"), "html.parser"
    )
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    anchors = list_anchors(soup)
    ids = {id(a) for a in anchors}
    rows = []
    for a in anchors:
        row = parse_row(a, row_container(a, ids))
        if row["word"]:
            rows.append(row)
    return rows


def natural_key(p):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.name)]


# ------------------------------------------------------------------ EPUB
CSS = """body { font-family: serif; line-height: 1.35; }
h2 { margin: 0.5em 0; }
p { margin: 0 0 0.7em 0; text-indent: 0; }
.t { font-style: italic; }
"""


def esc(s):
    return html.escape(s, quote=True)


def entry_html(e):
    out = f"<b>{esc(e['word'])}</b>"
    if e["transcription"]:
        out += f' <span class="t">[{esc(e["transcription"])}]</span>'
    if e["translation"]:
        out += f" — {esc(e['translation'])}"
    return f"<p>{out}</p>"


def xhtml(title, body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        'lang="en" xml:lang="en">\n<head><meta charset="utf-8"/>'
        f"<title>{esc(title)}</title>"
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        f"<body><h2>{esc(title)}</h2>\n{body}\n</body></html>"
    )


def make_chapters(entries, order):
    """Return list of (title, [entries])."""
    if order == "alpha":
        groups = {}
        for e in sorted(entries, key=lambda x: x["word"].lower()):
            first = e["word"][0].upper()
            key = first if first.isalpha() else "#"
            groups.setdefault(key, []).append(e)
        return sorted(groups.items(), key=lambda kv: (kv[0] == "#", kv[0]))
    size = 100
    return [
        (f"Words {i + 1}–{min(i + size, len(entries))}", entries[i : i + size])
        for i in range(0, len(entries), size)
    ]


def write_epub(path, entries, order, title="WooordHunt Vocabulary"):
    chapters = make_chapters(entries, order)
    book_id = f"urn:uuid:{uuid.uuid4()}"
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest, spine, nav_li, ncx_points, files = [], [], [], [], {}
    for i, (ch_title, items) in enumerate(chapters, 1):
        fn = f"ch{i:03d}.xhtml"
        files[f"OEBPS/{fn}"] = xhtml(ch_title, "\n".join(entry_html(e) for e in items))
        manifest.append(
            f'<item id="ch{i}" href="{fn}" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="ch{i}"/>')
        nav_li.append(f'<li><a href="{fn}">{esc(ch_title)}</a></li>')
        ncx_points.append(
            f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{esc(ch_title)}</text></navLabel>'
            f'<content src="{fn}"/></navPoint>'
        )

    files["OEBPS/style.css"] = CSS
    files["OEBPS/nav.xhtml"] = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
        f'<head><meta charset="utf-8"/><title>{esc(title)}</title></head><body>'
        f'<nav epub:type="toc" id="toc"><h1>{esc(title)}</h1><ol>{"".join(nav_li)}</ol></nav></body></html>'
    )
    files["OEBPS/toc.ncx"] = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        f'<head><meta name="dtb:uid" content="{book_id}"/></head>'
        f"<docTitle><text>{esc(title)}</text></docTitle><navMap>{''.join(ncx_points)}</navMap></ncx>"
    )
    files["OEBPS/content.opf"] = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="bookid">{book_id}</dc:identifier>'
        f"<dc:title>{esc(title)}</dc:title><dc:language>en</dc:language>"
        "<dc:creator>WooordHunt</dc:creator>"
        f'<meta property="dcterms:modified">{now}</meta></metadata><manifest>'
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        '<item id="css" href="style.css" media-type="text/css"/>'
        f"{''.join(manifest)}</manifest><spine toc=\"ncx\">{''.join(spine)}</spine></package>"
    )
    container = (
        '<?xml version="1.0"?>\n<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
        '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
        "</rootfiles></container>"
    )

    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        z.writestr(
            "META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED
        )
        for name, data in files.items():
            z.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", default=".")
    ap.add_argument("--order", choices=["alpha", "recent"], default="alpha")
    ap.add_argument("--name", default="wooordhunt_vocabulary")
    args = ap.parse_args()

    in_dir = Path(args.input_dir)
    pages = sorted(in_dir.glob("debug_page_*.html"), key=natural_key)
    if not pages:
        raise SystemExit(f"No debug_page_*.html files found in {in_dir.resolve()}")

    entries, seen = [], set()
    for p in pages:
        rows = parse_file(p)
        added = 0
        for r in rows:
            key = r["word"].lower()
            if key in seen:
                continue
            seen.add(key)
            entries.append(r)
            added += 1
        print(f"{p.name}: {len(rows)} rows, {added} new")

    if not entries:
        raise SystemExit(
            "No words extracted - check that the pages are logged-in vocabulary pages."
        )

    out = in_dir
    with open(out / f"{args.name}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["word", "transcription", "translation"])
        w.writeheader()
        w.writerows(entries)

    with open(out / f"{args.name}.txt", "w", encoding="utf-8") as f:
        for e in entries:
            line = e["word"]
            if e["transcription"]:
                line += f" [{e['transcription']}]"
            if e["translation"]:
                line += f" — {e['translation']}"
            f.write(line + "\n")

    write_epub(out / f"{args.name}.epub", entries, args.order)
    no_tr = sum(1 for e in entries if not e["translation"])
    print(f"\nDone: {len(entries)} unique words ({no_tr} without translation)")
    print(f"Saved {args.name}.epub / .csv / .txt in {out.resolve()}")


if __name__ == "__main__":
    main()
