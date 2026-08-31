# 同梱フォントの出所とライセンス

ダッシュボードはフォントを自己ホストする（実行時に外部 CDN・Google Fonts へ
アクセスしない）。このディレクトリの woff2 は以下の再配布物。

## Zen Maru Gothic（本文・表用。400 / 500 / 700）

- ファイル: `zen-maru-gothic-v19-japanese_latin-{regular,500,700}.woff2`
- 著作権: Copyright 2021 The Zen Maru Gothic Project Authors
  (https://github.com/googlefonts/zen-marugothic)
- ライセンス: SIL Open Font License 1.1（同梱の `OFL.txt`）
- 取得元: google-webfonts-helper (https://gwfh.mranftl.com/) 経由の
  Google Fonts 配布物（japanese + latin サブセット）

## 木漏れ日ゴシック / Komorebi Gothic（見出し用）

- ファイル: `komorebi-gothic.woff2`（`../komorebi-gothic.ttf` から
  pyftsubset で U+0000-00FF, U+3000-30FF, U+4E00-9FFF, U+FF00-FFEF を
  サブセット化・woff2 変換したもの。TTF はフォールバック用に同梱）
- 配布元: MODI 工場 (http://modi.jpn.org/font_komorebi-gothic.php)
- ベース: M+ OUTLINE FONTS（M+ FONT LICENSE。改変・再配布自由）
