// ページ計算の純粋関数（D1-1。DOM に触れない。テスト: tests_js/paging.test.mjs）。
//
// API 側の DEFAULT_LIMIT(200) / MAX_LIMIT(500) と揃えた選択肢。
export const PAGE_SIZES = [50, 100, 200, 500];

// 1ページ分の表示情報を計算する。
//   total: 絞り込み後の総件数（サーバーの count_rows）
//   limit / offset: 現在のページ指定
//   count: このページに実際に返った行数
export function pageInfo({ total, limit, offset, count }) {
  return {
    hasPrev: offset > 0,
    hasNext: offset + count < total,
    prevOffset: Math.max(0, offset - limit),
    nextOffset: offset + limit,
    rangeText:
      count === 0 ? "0 件" : `${offset + 1}〜${offset + count} 件 / 全 ${total} 件`,
  };
}
