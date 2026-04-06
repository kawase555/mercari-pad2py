"""
kintone_register.py

ローカルJSONからkintoneのPickアプリにレコードを登録する。
- アイテムアプリ(app=210)をJANで検索して商品名・ブランド・価格を取得
- unique_idで重複チェック（存在すればスキップ）
- 登録失敗時はChatWorkに通知＋ログ記録
- kintone_env を config.json で dev/prod 切替可能

使い方:
  通常実行（output_dirの最新JSONを処理）:
    python kintone_register.py

  リカバリ実行（特定のJSONファイルを指定）:
    python kintone_register.py --file "C:\\path\\to\\file.json"
"""

import json
import os
import sys
import glob
import argparse
import requests
from loguru import logger
from datetime import datetime, timezone, timedelta


# ─────────────────────────────────────────────────────────────────────────────
# 設定読み込み
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

with open(CONFIG_PATH, encoding="utf-8") as f:
    cfg = json.load(f)

KINTONE_ENV      = cfg["kintone_env"]
KINTONE_CFG      = cfg[f"kintone_{KINTONE_ENV}"]
DOMAIN           = KINTONE_CFG["domain"]
APP_ID           = KINTONE_CFG["app_id"]
API_TOKEN        = KINTONE_CFG["api_token"]
OUTPUT_DIR       = cfg["output_dir"]
LOG_DIR          = cfg["log_dir"]
CHATWORK_TOKEN   = cfg["chatwork_token"]
CHATWORK_ROOM_ID = cfg["chatwork_room_id"]
SHOP_URL_ID      = cfg["shop_url_id"]
SHOP_ID          = cfg["shop_id"]

ITEM_APP_ID    = cfg["kintone_item"]["app_id"]
ITEM_API_TOKEN = cfg["kintone_item"]["api_token"]
ITEM_DOMAIN    = cfg["kintone_item"]["domain"]

KINTONE_RECORD_URL  = f"{DOMAIN}/k/v1/record.json"
KINTONE_RECORDS_URL = f"{DOMAIN}/k/v1/records.json"
ITEM_RECORDS_URL    = f"{ITEM_DOMAIN}/k/v1/records.json"

GET_KINTONE_HEADERS = {
    "X-Cybozu-API-Token": API_TOKEN,
}

GET_ITEM_HEADERS = {
    "X-Cybozu-API-Token": ITEM_API_TOKEN,
}

POST_KINTONE_HEADERS = {
    "Content-Type": "application/json",
    "X-Cybozu-API-Token": API_TOKEN,
}


# ─────────────────────────────────────────────────────────────────────────────
# ログ設定（DEBUG含む全レベル出力）
# ─────────────────────────────────────────────────────────────────────────────

os.makedirs(LOG_DIR, exist_ok=True)
now_local = datetime.now()
log_path = os.path.join(LOG_DIR, f"kintone_register_{now_local.strftime('%Y%m%d%H%M%S')}.log")

logger.remove()
logger.add(
    sys.stdout,
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {function}:{line} | {message}"
)
logger.add(
    log_path,
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {function}:{line} | {message}",
    encoding="utf-8"
)

logger.info("kintone_register 開始")
logger.info(f"環境: {KINTONE_ENV} / ドメイン: {DOMAIN} / app_id: {APP_ID}")
logger.info(f"アイテムアプリ: {ITEM_DOMAIN} / app_id: {ITEM_APP_ID}")
logger.info(f"ログファイル: {log_path}")


# ─────────────────────────────────────────────────────────────────────────────
# ChatWork通知
# ─────────────────────────────────────────────────────────────────────────────

def notify_chatwork(message: str):
    logger.debug(f"ChatWork通知送信開始: room_id={CHATWORK_ROOM_ID}")
    try:
        r = requests.post(
            f"https://api.chatwork.com/v2/rooms/{CHATWORK_ROOM_ID}/messages",
            headers={"X-ChatWorkToken": CHATWORK_TOKEN},
            data={"body": message},
            timeout=10,
        )
        logger.debug(f"ChatWork通知レスポンス: HTTP={r.status_code}")
        if r.status_code != 200:
            logger.warning(f"ChatWork通知失敗: {r.status_code} / {r.text}")
        else:
            logger.info("ChatWork通知成功")
    except Exception as e:
        logger.warning(f"ChatWork通知例外: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# アイテムアプリからJANで商品情報を取得
# ─────────────────────────────────────────────────────────────────────────────

def fetch_item_by_jan(jan: str) -> dict:
    url = f"{ITEM_RECORDS_URL}?app={ITEM_APP_ID}&query=jan%20%3D%20%22{jan}%22"
    logger.debug(f"アイテム取得リクエスト: jan={jan} / url={url}")
    try:
        r = requests.get(url, headers=GET_ITEM_HEADERS, timeout=15)
        logger.debug(f"アイテム取得レスポンス: jan={jan} / HTTP={r.status_code}")
        if r.status_code != 200:
            logger.warning(f"アイテム取得失敗: jan={jan} / {r.status_code} / {r.text}")
            return {}
        records = r.json().get("records", [])
        if not records:
            logger.warning(f"アイテム未登録: jan={jan}")
            return {}
        rec = records[0]

        lshop     = rec.get("lshop", {}).get("value", "")
        itemname  = rec.get("itemname", {}).get("value", "")
        c1        = rec.get("c1", {}).get("value", "")
        c2        = rec.get("c2", {}).get("value", "")
        brand_tag = rec.get("brand_tag", {}).get("value", "")
        brand     = rec.get("brand", {}).get("value", "")
        sellprice = rec.get("sellprice_n", {}).get("value", "")

        # 商品名を本番と同じフォーマットで結合
        item_name = f"【{lshop}】【】{itemname}{c1} {c2}{brand_tag}"

        logger.debug(f"アイテム取得成功: jan={jan} / item_name={item_name} / brand={brand} / sellprice={sellprice}")

        return {
            "item_name":    item_name,
            "brand":        brand,
            "number_order": str(sellprice),
        }
    except Exception as e:
        logger.warning(f"アイテム取得例外: jan={jan} / {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# unique_idで重複チェック
# ─────────────────────────────────────────────────────────────────────────────

def exists_in_kintone(unique_id: str) -> bool:
    url = f"{KINTONE_RECORDS_URL}?app={APP_ID}&query=unique_id%20%3D%20%22{unique_id}%22"
    logger.debug(f"重複チェックリクエスト: unique_id={unique_id}")
    try:
        r = requests.get(url, headers=GET_KINTONE_HEADERS, timeout=15)
        logger.debug(f"重複チェックレスポンス: HTTP={r.status_code}")
        if r.status_code != 200:
            logger.warning(f"重複チェック失敗: {r.status_code} / {r.text}")
            return False
        count = len(r.json().get("records", []))
        logger.debug(f"重複チェック結果: unique_id={unique_id} / 件数={count}")
        return count > 0
    except Exception as e:
        logger.warning(f"重複チェック例外: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# kintoneに1件登録
# ─────────────────────────────────────────────────────────────────────────────

def register_record(record: dict) -> bool:
    body = {
        "app": APP_ID,
        "record": {
            "unique_id":    {"value": record.get("unique_id", "")},
            "order_id":     {"value": record.get("order_id", "")},
            "order_link":   {"value": record.get("order_link", "")},
            "jan":          {"value": record.get("jan", "")},
            "online_link":  {"value": record.get("online_link", "")},
            "ec_shopcode":  {"value": record.get("ec_shopcode", "")},
            "mall":         {"value": record.get("mall", "")},
            "image_link":   {"value": record.get("image_link", "")},
            "image_link2":  {"value": record.get("image_link2", "")},
            "number_order": {"value": str(record.get("number_order", ""))},
            "item_name":    {"value": record.get("item_name", "")},
            "brand":        {"value": record.get("brand", "")},
            "pick_date":    {"value": record.get("pick_date", "")},
        }
    }
    logger.debug(f"kintone登録リクエスト: unique_id={record.get('unique_id')} / body={json.dumps(body, ensure_ascii=False)}")
    try:
        r = requests.post(KINTONE_RECORD_URL, headers=POST_KINTONE_HEADERS, json=body, timeout=15)
        logger.debug(f"kintone登録レスポンス: HTTP={r.status_code} / body={r.text}")
        if r.status_code == 200:
            logger.info(f"登録成功: unique_id={record.get('unique_id')}")
            return True
        else:
            logger.error(f"登録失敗: unique_id={record.get('unique_id')} / {r.status_code} / {r.text}")
            return False
    except Exception as e:
        logger.error(f"登録例外: unique_id={record.get('unique_id')} / {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# メルカリ受注JSONからレコードを組み立てる
# ─────────────────────────────────────────────────────────────────────────────

def build_record_from_edge(edge: dict) -> list:
    node = edge.get("node", {})
    order_id   = node.get("id", "")
    created_at = node.get("createdAt", "")
    products   = node.get("products", [])

    # createdAt(UTC)をJSTに変換してkintone DATETIME形式へ
    pick_date = ""
    if created_at:
        try:
            dt_utc = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                dt_utc = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                dt_utc = None
        if dt_utc:
            dt_jst = dt_utc.astimezone(timezone(timedelta(hours=9)))
            pick_date = f"{dt_jst.year}/{dt_jst.month}/{dt_jst.day} {dt_jst.strftime('%H:%M:%S')}"
            logger.debug(f"createdAt変換: {created_at} → {pick_date}")

    records = []
    for product in products:
        jan = product.get("variant", {}).get("janCode", "")
        unique_id = f"order_{order_id}_{jan}"

        logger.debug(f"レコード組み立て開始: order_id={order_id} / jan={jan} / unique_id={unique_id}")

        item_info = fetch_item_by_jan(jan)

        record = {
            "unique_id":    unique_id,
            "order_id":     f"order_{order_id}",
            "order_link":   f"https://mercari-shops.com/seller/shops/{SHOP_URL_ID}/orders/{order_id}?source=deeplink",
            "jan":          jan,
            "online_link":  f"https://ec.bazzstore.com/products/{jan}",
            "ec_shopcode":  SHOP_ID,
            "mall":         "メルカリShops",
            "image_link":   "",
            "image_link2":  "",
            "item_name":    item_info.get("item_name", ""),
            "brand":        item_info.get("brand", ""),
            "number_order": item_info.get("number_order", ""),
            "pick_date":    pick_date,
        }

        logger.debug(f"レコード組み立て完了: {json.dumps(record, ensure_ascii=False)}")
        records.append(record)
    return records


# ─────────────────────────────────────────────────────────────────────────────
# 最新のJSONファイルを取得
# ─────────────────────────────────────────────────────────────────────────────

def get_latest_json() -> str | None:
    pattern = os.path.join(OUTPUT_DIR, "mercari_orders_raw_*.json")
    files = glob.glob(pattern)
    if not files:
        logger.debug(f"JSONファイル検索パターン: {pattern} / 該当なし")
        return None
    latest = max(files, key=os.path.getmtime)
    logger.debug(f"最新JSONファイル: {latest}")
    return latest


# ─────────────────────────────────────────────────────────────────────────────
# メイン処理
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=str, default=None, help="処理対象のJSONファイルパス（省略時は最新ファイル）")
    args = parser.parse_args()

    if args.file:
        target_file = args.file
        logger.info(f"リカバリモード: 指定ファイル={target_file}")
    else:
        target_file = get_latest_json()
        logger.info("通常モード: 最新JSONファイルを処理")

    if not target_file or not os.path.exists(target_file):
        logger.info("処理対象のJSONファイルが見つかりません。終了します。")
        sys.exit(0)

    logger.info(f"処理対象ファイル: {target_file}")

    with open(target_file, encoding="utf-8") as f:
        data = json.load(f)

    edges = data.get("foreachItems", [])
    logger.info(f"取得件数: {len(edges)}件")

    if len(edges) == 0:
        logger.info("0件のため処理をスキップします。")
        sys.exit(0)

    all_records = []
    for edge in edges:
        all_records.extend(build_record_from_edge(edge))

    logger.info(f"登録対象レコード数（商品単位）: {len(all_records)}件")

    success = 0
    skip = 0
    fail = 0
    failed_unique_ids = []

    for record in all_records:
        unique_id = record.get("unique_id", "")

        if exists_in_kintone(unique_id):
            logger.info(f"スキップ（既存）: unique_id={unique_id}")
            skip += 1
            continue

        if register_record(record):
            success += 1
        else:
            fail += 1
            failed_unique_ids.append(unique_id)

    logger.info(f"完了: 成功={success}件 / スキップ={skip}件 / 失敗={fail}件")

    if fail > 0:
        msg = (
            f"【Pickアプリ登録失敗】\n"
            f"環境: {KINTONE_ENV}\n"
            f"失敗件数: {fail}件\n"
            f"対象ファイル: {target_file}\n"
            f"失敗unique_id:\n" + "\n".join(failed_unique_ids)
        )
        notify_chatwork(msg)
        logger.error(f"ChatWork通知送信: 失敗{fail}件")

    logger.info("kintone_register 終了")


if __name__ == "__main__":
    main()