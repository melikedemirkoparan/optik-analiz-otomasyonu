#!/usr/bin/env python3
"""
KULLANIM_KILAVUZU.md -> HTML + PDF üretir.

Görseller HTML'e base64 olarak GÖMÜLÜR; böylece tek dosya kendi kendine
yeter (mail ile gönderilebilir, gorseller/ klasörü olmadan da açılır).

Kullanım:
    python3 docs/build_docs.py
"""
import base64
import mimetypes
import re
import subprocess
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent
MD = DOCS / "KULLANIM_KILAVUZU.md"
HTML = DOCS / "KULLANIM_KILAVUZU.html"
PDF = DOCS / "KULLANIM_KILAVUZU.pdf"

CSS = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body {
  font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
  font-size: 11pt; line-height: 1.55; color: #1c1e21;
  max-width: 900px; margin: 0 auto; padding: 24px;
  background: #fff;
}
h1 { font-size: 22pt; border-bottom: 3px solid #2b6cb0; padding-bottom: 8px;
     color: #1a365d; margin-top: 0; }
h2 { font-size: 15pt; color: #2b6cb0; margin-top: 28px;
     border-bottom: 1px solid #cbd5e0; padding-bottom: 4px; }
h3 { font-size: 12.5pt; color: #2c5282; margin-top: 20px; }
h4 { font-size: 11.5pt; color: #4a5568; margin-top: 16px; }
code { background: #edf2f7; padding: 1px 5px; border-radius: 3px;
       font-family: "SF Mono", Consolas, monospace; font-size: 0.9em;
       color: #b83280; }
pre { background: #f7fafc; border: 1px solid #cbd5e0; border-left: 4px solid #2b6cb0;
      padding: 10px 14px; border-radius: 4px; overflow-x: auto; }
pre code { background: none; color: #2d3748; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 10pt; }
th { background: #2b6cb0; color: #fff; text-align: left; padding: 7px 10px; }
td { border: 1px solid #cbd5e0; padding: 6px 10px; vertical-align: top; }
tr:nth-child(even) td { background: #f7fafc; }
blockquote { border-left: 4px solid #f6ad55; background: #fffaf0;
             margin: 12px 0; padding: 8px 16px; color: #744210; }
blockquote p { margin: 4px 0; }
img { max-width: 100%; border: 1px solid #cbd5e0; border-radius: 4px;
      margin: 10px 0; }
/* Sayfa kırılmaları: başlık içerikten kopmasın, tablo/görsel ikiye bölünmesin */
h1, h2, h3, h4 { break-after: avoid-page; page-break-after: avoid; }
table, pre, blockquote { break-inside: avoid-page; page-break-inside: avoid; }
img { break-inside: avoid-page; page-break-inside: avoid; }
tr { break-inside: avoid; page-break-inside: avoid; }
hr { border: none; border-top: 1px solid #e2e8f0; margin: 24px 0; }
ul, ol { padding-left: 24px; }
li { margin: 3px 0; }
strong { color: #1a202c; }
"""


def embed_images(html: str) -> str:
    """<img src="gorseller/x.png"> -> base64 data URI."""
    def repl(m):
        src = m.group(1)
        if src.startswith(("data:", "http://", "https://")):
            return m.group(0)
        path = (DOCS / src).resolve()
        if not path.exists():
            print(f"  ! görsel bulunamadı: {src}")
            return m.group(0)
        mime = mimetypes.guess_type(str(path))[0] or "image/png"
        b64 = base64.b64encode(path.read_bytes()).decode()
        return m.group(0).replace(src, f"data:{mime};base64,{b64}")

    return re.sub(r'<img[^>]+src="([^"]+)"', repl, html)


def main():
    if not MD.exists():
        sys.exit(f"bulunamadı: {MD}")

    print(f"kaynak : {MD.name}  ({MD.stat().st_size/1024:.0f} KB)")

    # --- HTML (pandoc: tablo/başlık desteği en iyisi) ---
    css_file = DOCS / "_style.css"
    css_file.write_text(CSS)
    try:
        subprocess.run(
            ["pandoc", str(MD), "-f", "gfm", "-t", "html5",
             "--standalone", "--metadata", "title=Optik Analiz — Kullanım Kılavuzu",
             "--css", str(css_file), "-o", str(HTML)],
            check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        sys.exit(f"pandoc hatası: {e.stderr[:400]}")

    # CSS'i ve görselleri dosyanın içine göm -> tek dosya kendine yeter
    html = HTML.read_text()
    html = html.replace(
        f'<link rel="stylesheet" href="{css_file}" />',
        f"<style>{CSS}</style>")
    if "<style>" not in html:                    # pandoc sürüm farkı güvencesi
        html = html.replace("</head>", f"<style>{CSS}</style></head>")
    print("görseller gömülüyor…")
    html = embed_images(html)
    HTML.write_text(html)
    css_file.unlink(missing_ok=True)
    print(f"HTML   : {HTML.name}  ({HTML.stat().st_size/1024/1024:.1f} MB)")

    # --- PDF (weasyprint: gömülü HTML'den) ---
    print("PDF üretiliyor…")
    try:
        subprocess.run(["weasyprint", str(HTML), str(PDF)],
                       check=True, capture_output=True, text=True, timeout=600)
        print(f"PDF    : {PDF.name}  ({PDF.stat().st_size/1024/1024:.1f} MB)")
    except FileNotFoundError:
        print("  ! weasyprint kurulu değil — PDF atlandı")
    except subprocess.CalledProcessError as e:
        print(f"  ! PDF hatası: {e.stderr[:300]}")

    print("tamam.")


if __name__ == "__main__":
    main()
