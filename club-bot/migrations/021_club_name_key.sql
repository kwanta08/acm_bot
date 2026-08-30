-- =====================================================================
-- 021_club_name_key.sql
--
-- サークル名の設定キーを CLUB_NAME に統一する（スキーマバージョン 22。D2-1）。
--
-- 背景:
--   ダッシュボードの設定 API は「サークル名」を GUILD_NAME キーで保存していたが、
--   週次サマリー等が読むのは CLUB_NAME（config.py）。
--   **保存しても反映されない**不具合になっていた。
--
-- 既存データの扱い（ADR 0024「既定値で既存データを動かさない」に照らした判断）:
--   - GUILD_NAME だけのギルド: 値を CLUB_NAME へ**コピー**する。
--     利用者がダッシュボードで明示的に保存した値を初めて有効にする移行で、
--     「既定値で勝手に動かす」ものではない
--   - CLUB_NAME が既にあるギルド: **上書きしない**（現に効いている値を守る）
--   - どちらも無いギルド: 何も起きない
--   - 旧キー GUILD_NAME の行は**消さない**（安全側。以後どのコードも読まないが、
--     監査と巻き戻しの余地を残す。正はこの移行以降つねに CLUB_NAME）
--
-- 影響の注記:
--   GUILD_NAME は bot がギルド参加時に Discord サーバー名で自動設定するため、
--   ダッシュボードで一度も編集していないギルドにも値がある。CLUB_NAME 未設定の
--   ギルドでは週次サマリー等の表示が既定の「サークル」からサーバー名に変わる
--   （破壊なし・不可逆なし。/setup のサークル名モーダルでいつでも上書きできる）。
--
-- 適用方法:
--   通常は bot / dashboard 起動時に utils/db.py の
--   _migrate_v22_club_name_key() が自動適用する（冪等）。
--   手動で当てる場合のみ、以下を実行する。
-- =====================================================================

INSERT INTO settings (guild_id, setting_key, setting_value, updated_at)
SELECT s.guild_id, 'CLUB_NAME', s.setting_value, s.updated_at
FROM settings s
WHERE s.setting_key = 'GUILD_NAME'
  AND NOT EXISTS (
    SELECT 1 FROM settings c
    WHERE c.guild_id = s.guild_id AND c.setting_key = 'CLUB_NAME'
  );

-- スキーマバージョン更新（SQLite: PRAGMA user_version = 22 /
-- PostgreSQL: UPDATE schema_meta SET version = 22 WHERE id = 1）
