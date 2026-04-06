"""
mercari_orders.py

メルカリShops受注取得 → 下流webhookへPOST
PAD完全置き換えスクリプト（1回実行型）
タスクスケジューラで5分おきに起動して使う。

モード切替:
  config.json の "mode" を変える
  "test" → test_fday / test_sday の固定日時で実行（動作確認用）
  "live" → 実行時刻から自動でtime_window_minutes分さかのぼって実行（本番用）
"""

import json
import logging
import os
import sys
import requests
from datetime import datetime, timezone, timedelta


# ─────────────────────────────────────────────────────────────────────────────
# 設定ファイル読み込み
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

with open(CONFIG_PATH, encoding="utf-8") as f:
    cfg = json.load(f)

API_ENDPOINT     = cfg["api_endpoint"]
BEARER_TOKEN     = cfg["bearer_token"]
USER_AGENT       = cfg["user_agent"]
QUERY_FIRST      = cfg["query_first"]
TIME_WINDOW_MIN  = cfg["time_window_minutes"]
MODE             = cfg["mode"]                  # "test" or "live"
TEST_FDAY        = cfg.get("test_fday", "")
TEST_SDAY        = cfg.get("test_sday", "")
WEBHOOK_URL      = cfg["webhook_url"]
SHOP_ID          = cfg["shop_id"]
SHOP_URL_ID      = cfg["shop_url_id"]
OUTPUT_DIR       = cfg["output_dir"]
LOG_DIR          = cfg["log_dir"]
LOG_FILE_PREFIX  = cfg["log_file_prefix"]
OUTPUT_PREFIX    = cfg["output_file_prefix"]


# ─────────────────────────────────────────────────────────────────────────────
# ログ設定
# ─────────────────────────────────────────────────────────────────────────────

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

now_local = datetime.now()
log_filename = f"{LOG_FILE_PREFIX}{now_local.strftime('%Y%m%d%H%M%S')}.log"
log_path = os.path.join(LOG_DIR, log_filename)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 時間窓の決定
# ─────────────────────────────────────────────────────────────────────────────

log.info("実行開始")
log.info(f"実行時刻(ローカル): {now_local.strftime('%Y-%m-%d %H:%M:%S')}")
log.info(f"設定ファイル: {CONFIG_PATH}")
log.info(f"ログファイル: {log_path}")
log.info(f"モード: {MODE}")

if MODE == "test":
    JST = timezone(timedelta(hours=9))
    fday_str = datetime.strptime(TEST_FDAY, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=JST).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sday_str = datetime.strptime(TEST_SDAY, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=JST).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("テストモード: 固定日時を使用")
elif MODE == "live":
    sday_utc = datetime.now(timezone.utc)
    fday_utc = sday_utc - timedelta(minutes=TIME_WINDOW_MIN)
    sday_str = sday_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    fday_str = fday_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("本番モード: 実行時刻から自動計算")
else:
    log.error(f"不正なmode値: {MODE}  ('test' または 'live' を指定してください)")
    sys.exit(1)

log.info(f"API endpoint: {API_ENDPOINT}")
log.info(f"time_window_minutes: {TIME_WINDOW_MIN}")
log.info(f"fday(UTC): {fday_str}")
log.info(f"sday(UTC): {sday_str}")


# ─────────────────────────────────────────────────────────────────────────────
# GraphQL クエリビルド関数
# ─────────────────────────────────────────────────────────────────────────────

def build_query(fday: str, sday: str, first: int, after: str = None) -> str:
    after_clause = f'\n    after: "{after}"' if after else ""
    return f"""query {{
  orders(
    first: {first}{after_clause}
    orderedDateGte: "{fday}"
    orderedDateLt: "{sday}"
  ) {{
    edges {{
      node {{
        id
        createdAt
        status
        products {{
          variant {{
            janCode
          }}
        }}
      }}
    }}
    pageInfo {{
      endCursor
      hasNextPage
    }}
  }}
}}"""


# ─────────────────────────────────────────────────────────────────────────────
# メルカリAPI呼び出し（ページネーション対応）
# ─────────────────────────────────────────────────────────────────────────────

headers = {
    "Authorization": f"Bearer {BEARER_TOKEN}",
    "User-Agent": USER_AGENT,
    "Content-Type": "application/json",
}

all_edges = []
after_cursor = None
page = 1

while True:
    query_str = build_query(fday_str, sday_str, QUERY_FIRST, after_cursor)
    log.debug(f"GraphQL query (page {page}):\n{query_str}")

    try:
        response = requests.post(
            API_ENDPOINT,
            headers=headers,
            json={"query": query_str},
            timeout=30,
        )
    except requests.exceptions.RequestException as e:
        log.error(f"APIリクエスト失敗: {e}")
        sys.exit(1)

    log.info(f"HTTP status: {response.status_code} (page {page})")

    if response.status_code != 200:
        log.error(f"HTTPエラー: {response.status_code}")
        log.error(f"レスポンス本文: {response.text}")
        sys.exit(1)

    try:
        body = response.json()
    except json.JSONDecodeError as e:
        log.error(f"JSONパース失敗: {e}")
        log.error(f"レスポンス本文: {response.text}")
        sys.exit(1)

    # GraphQLエラー確認
    if "errors" in body:
        log.error(f"GraphQLエラー: {json.dumps(body['errors'], ensure_ascii=False)}")
        sys.exit(1)

    edges = body.get("data", {}).get("orders", {}).get("edges", [])
    page_info = body.get("data", {}).get("orders", {}).get("pageInfo", {})

    all_edges.extend(edges)
    log.info(f"page {page} 取得件数: {len(edges)}件 (累計: {len(all_edges)}件)")

    has_next = page_info.get("hasNextPage", False)
    end_cursor = page_info.get("endCursor", "")

    if not has_next or not end_cursor:
        break

    after_cursor = end_cursor
    page += 1

log.info(f"合計取得件数: {len(all_edges)}件")


# ─────────────────────────────────────────────────────────────────────────────
# 生データをJSONファイルに保存（ログ・監査用）
# ─────────────────────────────────────────────────────────────────────────────

output_filename = f"{OUTPUT_PREFIX}_{now_local.strftime('%Y%m%d_%H%M%S')}.json"
output_path = os.path.join(OUTPUT_DIR, output_filename)

with open(output_path, "w", encoding="utf-8") as f:
    json.dump({"foreachItems": all_edges}, f, ensure_ascii=False, indent=2)
    
log.info(f"生データ保存先: {output_path}")


# ─────────────────────────────────────────────────────────────────────────────
# 注文展開 → webhookへPOST
# PADと同じ構造:
#   外ループ: orders.edges[] (注文単位)
#   内ループ: node.products[] (商品単位)
#   1商品ごとに1件webhookへPOST
# ─────────────────────────────────────────────────────────────────────────────

if len(all_edges) == 0:
    log.info("取得件数0件のためwebhookへのPOSTはスキップします")
else:
    post_success = 0
    post_failure = 0

    for edge in all_edges:
        node = edge.get("node", {})
        order_id  = node.get("id", "")
        created_at = node.get("createdAt", "")
        status    = node.get("status", "")
        products  = node.get("products", [])

        log.info(f"注文処理: id={order_id}, createdAt={created_at}, status={status}, 商品数={len(products)}")

        for product in products:
            jan_code = product.get("variant", {}).get("janCode", "")

            payload = [
                {
                    "Pick_orderid": f"order_{order_id}",
                    "Pick_jan":     jan_code,
                    "Pick_shop":    SHOP_ID,
                    "Pick_orderurl": f"https://mercari-shops.com/seller/shops/{SHOP_URL_ID}/orders/{order_id}?source=deeplink",
                }
            ]

            log.debug(f"webhook POST payload: {json.dumps(payload, ensure_ascii=False)}")

            try:
                r = requests.post(
                    WEBHOOK_URL,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=30,
                )
                log.info(f"webhook POST: order_id={order_id}, jan={jan_code}, HTTP={r.status_code}")

                if r.status_code not in (200, 202):
                    log.warning(f"webhook 非正常応答: {r.status_code} / {r.text}")
                    post_failure += 1
                else:
                    post_success += 1

            except requests.exceptions.RequestException as e:
                log.error(f"webhook POSTリクエスト失敗: order_id={order_id}, jan={jan_code}, error={e}")
                post_failure += 1

    log.info(f"webhook POST完了: 成功={post_success}件, 失敗={post_failure}件")


# ─────────────────────────────────────────────────────────────────────────────
# 終了
# ─────────────────────────────────────────────────────────────────────────────

log.info("実行成功")
print(f"成功: 取得件数={len(all_edges)}, 保存先={output_path}")
