#!/usr/bin/env python3
"""docs/ の HTML マニュアルを A4 の PDF に変換する。

`docs/role_manual.html`（権限レベル別 取扱説明書）などを、Chromium の
ヘッドレス印刷でそのまま PDF にする。HTML が正本で、PDF は生成物。

    python scripts/build_manual_pdf.py                  # role_manual.html を変換
    python scripts/build_manual_pdf.py --install-font   # フォントを入れてから変換
    python scripts/build_manual_pdf.py docs/keta_maki_guide.html
    python scripts/build_manual_pdf.py docs/leaflet.html --png   # ページごとの PNG も出す

`--png` は PDF をページごとの PNG（`<名前>-1.png`, `<名前>-2.png` …）にする。
SNS やチャットへ貼るとき用で、PDF と同じく生成物。PyMuPDF を使うので、
無ければ `pip install pymupdf` を促して PDF だけで終了する（bot 本体の
requirements.txt には入れない。この変換をするときだけ要る）。

**フォントについて**: マニュアルは Zen Maru Gothic（SIL Open Font License）を
指定している。CSS の @font-face は `local()` を先に見るので、OS にこの
フォントが入っていればネットワーク無しで正しく描画される。入っていない
環境では `--install-font` でユーザーのフォントディレクトリへ取得する
（フォント本体はリポジトリに含めない）。

Chromium は次の順で探す:
  1. 環境変数 CHROME_BIN
  2. PATH 上の chromium / chromium-browser / google-chrome / chrome
  3. Playwright が入れた chromium（PLAYWRIGHT_BROWSERS_PATH 配下）
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

BOT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HTML = BOT_ROOT / "docs" / "role_manual.html"

#: Zen Maru Gothic（Google Fonts / SIL Open Font License 1.1）
FONT_URLS = {
    "ZenMaruGothic-Regular.ttf": (
        "https://fonts.gstatic.com/s/zenmarugothic/v19/o-0SIpIxzW5b-RxT-6A8jWAtCp-k7Q.ttf"
    ),
    "ZenMaruGothic-Medium.ttf": (
        "https://fonts.gstatic.com/s/zenmarugothic/v19/o-0XIpIxzW5b-RxT-6A8jWAtCp-cGWtCPA.ttf"
    ),
    "ZenMaruGothic-Bold.ttf": (
        "https://fonts.gstatic.com/s/zenmarugothic/v19/o-0XIpIxzW5b-RxT-6A8jWAtCp-cUW1CPA.ttf"
    ),
}

CHROME_NAMES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome")


def find_chrome() -> str:
    """Chromium の実行ファイルを探す。見つからなければ RuntimeError。"""
    env = os.environ.get("CHROME_BIN")
    if env and Path(env).is_file():
        return env

    for name in CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found

    # Playwright が入れた Chromium（このリポジトリの CI / 開発コンテナ向け）
    pw_root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
    if pw_root.is_dir():
        for candidate in sorted(pw_root.glob("chromium-*/chrome-linux/chrome")):
            if candidate.is_file():
                return str(candidate)
        for candidate in sorted(pw_root.glob("chromium*/chrome-mac/Chromium.app/*/MacOS/Chromium")):
            if candidate.is_file():
                return str(candidate)

    raise RuntimeError(
        "Chromium が見つかりません。環境変数 CHROME_BIN に実行ファイルのパスを指定するか、"
        "chromium / google-chrome を PATH に通してください。"
    )


def user_font_dir() -> Path:
    """OS ごとのユーザー用フォントディレクトリ。"""
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Fonts"
    if system == "Windows":
        return Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts"
    return Path.home() / ".local" / "share" / "fonts"


def install_font() -> None:
    """Zen Maru Gothic をユーザーのフォントディレクトリへ入れる。"""
    dest = user_font_dir()
    dest.mkdir(parents=True, exist_ok=True)
    for name, url in FONT_URLS.items():
        target = dest / name
        if target.is_file():
            print(f"  既にあります: {target.name}")
            continue
        print(f"  取得中: {name}")
        with urllib.request.urlopen(url, timeout=120) as res:
            target.write_bytes(res.read())

    if shutil.which("fc-cache"):
        subprocess.run(["fc-cache", "-f"], check=False, capture_output=True)
    print(f"フォントを入れました: {dest}")


def render(html: Path, pdf: Path, chrome: str) -> None:
    """Chromium のヘッドレス印刷で HTML を PDF にする。"""
    # Chromium はプロファイルを書ける場所を必要とするので使い捨てを渡す
    with tempfile.TemporaryDirectory(prefix="manual-pdf-") as profile:
        cmd = [
            chrome,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--no-pdf-header-footer",
            # フォントとレイアウトが確定してから印刷させる
            "--virtual-time-budget=20000",
            "--run-all-compositor-stages-before-draw",
            f"--user-data-dir={profile}",
            f"--print-to-pdf={pdf}",
            html.resolve().as_uri(),
        ]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)

    if not pdf.is_file():
        raise RuntimeError(
            f"PDF が生成されませんでした（終了コード {proc.returncode}）\n{proc.stderr.strip()}"
        )


def rasterize(pdf: Path, dpi: int) -> list[Path]:
    """PDF をページごとの PNG にする。戻り値は書き出したファイル。"""
    try:
        import pymupdf  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - 環境依存
        raise RuntimeError(
            "PNG の書き出しには PyMuPDF が要ります: pip install pymupdf"
        ) from exc

    written: list[Path] = []
    with pymupdf.open(pdf) as doc:
        for number, page in enumerate(doc, start=1):
            out = pdf.with_name(f"{pdf.stem}-{number}.png")
            page.get_pixmap(dpi=dpi).save(out)
            written.append(out)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="docs/ の HTML マニュアルを PDF に変換する")
    parser.add_argument(
        "html",
        nargs="?",
        default=str(DEFAULT_HTML),
        help="変換する HTML（既定: docs/role_manual.html）",
    )
    parser.add_argument("-o", "--output", help="出力先 PDF（既定: 入力と同じ名前の .pdf）")
    parser.add_argument(
        "--install-font",
        action="store_true",
        help="Zen Maru Gothic をユーザーのフォントディレクトリへ入れてから変換する",
    )
    parser.add_argument(
        "--png",
        action="store_true",
        help="PDF に加えてページごとの PNG（<名前>-1.png …）も書き出す",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=150,
        help="--png の解像度（既定: 150。A4 なら 1240x1754 px）",
    )
    args = parser.parse_args()

    html = Path(args.html)
    if not html.is_file():
        print(f"HTML が見つかりません: {html}", file=sys.stderr)
        return 1
    pdf = Path(args.output) if args.output else html.with_suffix(".pdf")

    if args.install_font:
        install_font()

    try:
        chrome = find_chrome()
        render(html, pdf, chrome)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"{html} -> {pdf}  ({pdf.stat().st_size / 1024:.0f} KB)")

    if args.png:
        try:
            pages = rasterize(pdf, args.dpi)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        for page in pages:
            print(f"{pdf} -> {page}  ({page.stat().st_size / 1024:.0f} KB)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
