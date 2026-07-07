# .env が読み込めない問題の恒久対策（config.py 改修）

「`.env` は正しく設定したのに『必須設定が不足しています（DISCORD_TOKEN / GUILD_ID）』と表示されて起動できない」という問題を、**config.py 側で根本的に解消**しました。この文書は、何を直したのか・既存のVPSにどう適用するのか・確認方法を、順番に説明します。

---

## 1. なぜ起きていたのか（原因のおさらい）

`.env` の中身が正しくても、次のいずれかが原因で「見えていない／壊れて読まれる」ことがありました。

| # | 原因 | 具体例 |
|---|------|--------|
| 1 | **場所のズレ（最頻出）** | 手動起動で `cd ~/club-bot/app` してから `python bot.py` を実行すると、プログラムの現在地は `app/` になる。しかし `.env` はその1つ上の `~/club-bot/.env` に置かれている。従来の `load_dotenv()` は「現在地」から探すため、`.env` を見つけられなかった。 |
| 2 | **先頭の BOM** | Windows のメモ帳等で保存すると、ファイル先頭に見えない印（BOM）が付く。これが付くと**1行目のキー名が壊れ**、`DISCORD_TOKEN` が `\ufeffDISCORD_TOKEN` になって読めなくなる（2行目以降は無事なので「一部だけ欠落」という不可解な症状になる）。 |
| 3 | **改行コード CRLF** | Windows で編集すると行末に `\r` が残り、値の末尾に紛れ込む。 |
| 4 | **囲い引用符・全角/不可視文字** | `DISCORD_TOKEN="xxxx"` の引用符、全角スペース（　）、NBSP などが値に混ざる。 |

---

## 2. 何を直したのか（config.py / bot.py の変更点）

### config.py
1. **`.env` の場所に依存しない読み込み**
   - `config.py` 自身の位置を基準に、以下の順で `.env` を明示的に探索します。
     1. プロジェクト直下（`config.py` の1つ上 = `~/club-bot/.env`）
     2. `app/` 内（`config.py` と同じ階層）
     3. どちらも無ければ従来どおり現在地から探索
   - これで「どのフォルダから起動しても」`.env` を確実に見つけます（原因1を解消）。
   - `override=False` にしているため、systemd の `EnvironmentFile` で環境変数が注入済みの場合はそれを尊重します。

2. **BOM を除去して読み込み**
   - `load_dotenv(..., encoding="utf-8-sig")` で読むことで、先頭 BOM を自動除去します（原因2を解消）。

3. **値の自動クリーニング（`_clean`）**
   - すべての設定値から、BOM・`\r`（CR）・NBSP・全角スペース・前後空白・値全体を囲う引用符（`"` / `'`）を自動的に取り除きます（原因3・4を解消）。
   - 文字列項目は `_get_str()`、数値は `_get_int()`、ID一覧は `_get_int_list()` を通じて、すべてこのクリーニングを経由します。

4. **診断用メソッド `loaded_env_path()`**
   - 実際に読み込んだ `.env` の絶対パスを返します（環境変数のみの場合は空文字）。

### bot.py
- 起動時に「どの `.env` を読み込んだか」を必ずログに記録します。
- 必須設定が不足したときは、読み込んだ `.env` のパス（または「見つからなかった」旨と正しい置き場所）を併せて表示するので、原因の切り分けが一目でできます。

> つまり **今後は、`.env` を `~/club-bot/`（プロジェクト直下）か `~/club-bot/app/`（config.py と同じ場所）のどちらに置いても** 動作します。手動起動でも systemd 起動でも同じです。

---

## 3. 既存のVPSに適用する手順

すでにVPSで動かしている場合の更新手順です。順番に実行してください。SSH でVPSにログインした状態から始めます。

### 手順A: 新しいコードに差し替える

配布 zip を展開して `config.py` と `bot.py` を置き換えるのが確実です。

```bash
# 1) いったんBotを止める（systemdで動かしている場合）
sudo systemctl stop club-bot

# 2) 新しい zip をVPSに転送して展開（例: ホームに club-bot.zip を置いた場合）
cd ~
unzip -o club-bot.zip -d club-bot-new

# 3) 差し替える2ファイルだけをコピー（app/ の中身を置く場所に合わせてください）
cp club-bot-new/club-bot/config.py ~/club-bot/app/config.py
cp club-bot-new/club-bot/bot.py    ~/club-bot/app/bot.py
```

> フォルダ構成が違う場合は、`config.py` と `bot.py` を実際に置いている場所へコピーしてください。

### 手順B: 既存の .env を正規化する（保険）

念のため、既存の `.env` から改行コード CRLF と BOM を取り除いておきます（新コードでも吸収しますが、きれいにしておくと安心です）。

```bash
# CRLF を LF に変換
sed -i 's/\r$//' ~/club-bot/.env

# 先頭 BOM を除去
sed -i '1s/^\xEF\xBB\xBF//' ~/club-bot/.env
```

`.env` を `app/` の中に置いていた場合は、上記の `~/club-bot/.env` を `~/club-bot/app/.env` に読み替えてください。どちらの場所でも新コードは動きます。

### 手順C: Botを再起動する

```bash
sudo systemctl start club-bot

# 状態を確認
sudo systemctl status club-bot
```

---

## 4. 正しく直ったかの確認

### 確認1: 手動でクイックチェック（推奨）

Botを止めた状態で、設定が読めるかだけを確認できます。

```bash
cd ~/club-bot/app
../venv/bin/python -c "from config import config; print('読み込んだ.env:', config.loaded_env_path()); print('不足:', config.validate())"
```

- `不足: []` と表示されれば **OK**（必須設定はすべて揃っています）。
- `読み込んだ.env:` の右に、実際に読んだ `.env` の絶対パスが出ます。

### 確認2: ログを見る

```bash
# systemd のログ
journalctl -u club-bot -n 30 --no-pager

# またはアプリのログファイル
tail -n 30 ~/club-bot/logs/*.log
```

起動時に次のような行が出ます。

- 正常: `.env を読み込みました: /home/ubuntu/club-bot/.env`
- 異常時: `必須設定が不足しています: ...` に続き、読み込んだ `.env` のパス、または「.env が見つかりませんでした」と正しい置き場所が表示されます。

---

## 5. それでも直らないときのチェックリスト

| 症状 | 確認すること |
|------|--------------|
| `.env が見つかりませんでした` と出る | `~/club-bot/.env` または `~/club-bot/app/.env` にファイルが実在するか（`ls -la ~/club-bot/.env`）。ファイル名が `.env` 以外（例: `.env.txt`）になっていないか。 |
| `.env` は読めているが特定キーだけ不足 | `.env` 内のキー名のスペルを確認（`DISCORD_TOKEN` / `GUILD_ID`）。行頭に余計な空白や `#`（コメント化）が付いていないか。 |
| systemd で起動すると読めない | サービスは `EnvironmentFile` を使う設定です。`deploy/club-bot.service` の `EnvironmentFile=` のパスが実際の `.env` の場所と一致しているか確認し、`sudo systemctl daemon-reload` 後に再起動。 |
| 値に余計な文字が混ざる | 新コードで自動除去されますが、`cat -A ~/club-bot/.env` で `^M`（CR）や見えない文字を目視確認できます。 |

---

これで、`.env` の置き場所や編集環境（Windows / Mac）に左右されず、安定して起動できます。
