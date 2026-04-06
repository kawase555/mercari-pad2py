# mercari-pad2py
メルカリShopsの受注データを GraphQL API から取得し、商品単位に展開して下流 webhook へ連携する Python スクリプトです。
既存の PAD（Power Automate Desktop）処理を置き換えることを目的とした、**1回実行型バッチ**として実装しています。
想定運用は **Windows タスクスケジューラから定期起動** です。コードコメント上でも「PAD完全置き換えスクリプト（1回実行型）」「タスクスケジューラで5分おきに起動して使う」と明示されています。

---

## 概要

このスクリプトは、指定時間帯のメルカリShops注文を取得し、注文内の商品ごとに 1 件ずつ webhook へ POST します。処理の大きな流れは以下のとおりです。

1. `config.json` を読み込む
2. 実行モードに応じて取得対象の時間窓を決定する
3. メルカリShops GraphQL API へ問い合わせる
4. ページネーションしながら注文一覧を全件取得する
5. 取得した生データを JSON ファイルとして保存する
6. 注文配下の `products` を商品単位に展開する
7. webhook に対して 1 商品ごとに POST する
8. 実行ログをファイルおよび標準出力へ出力する

---

## リポジトリ構成
```text
メルカリ受注/
├─ README.md
├─ fetch_mercari_orders_and_save.py   メルカリAPI取得→JSON保存→webhook POST
├─ kintone_register.py                JSONを読んでkintoneのPickアプリに登録
├─ config.json                        設定外出し（環境切替・APIトークン等）
├─ run_mercari.bat                    fetch_mercari_orders_and_save.py の起動用
├─ run_retry.bat                      登録失敗時のリカバリ用
├─ 処理確認テスト手順.txt
└─ log/
   ├─ mercari_ordersYYYYMMDDHHMMSS.log
   └─ kintone_registerYYYYMMDDHHMMSS.log
```

---

## 処理フロー全体
```
タスクスケジューラ（5分おき）
  → run_mercari.bat
    → fetch_mercari_orders_and_save.py
        メルカリGraphQL API から受注取得
        → ローカルにJSONを保存（C:\Users\bs00b\Desktop\実験\mercari_orders_raw_*.json）
        → 下流webhookへPOST（既存PADフローへ中継）
    → kintone_register.py
        最新のJSONを読み込む
        → JANでkintoneアイテムアプリ(app=210)を検索して商品情報取得
        → unique_idで重複チェック
        → kintone Pickアプリに登録
        → 失敗時はChatWorkに通知
```

---

## config.json 設定項目

| キー | 説明 |
|---|---|
| mode | `test`=固定日時で実行 / `live`=実行時刻から自動計算 |
| test_fday / test_sday | testモード時の固定日時（JST） |
| time_window_minutes | liveモード時の取得時間窓（分） |
| kintone_env | `dev`=疑似環境 / `prod`=本番環境 |
| kintone_dev | 疑似環境の接続情報（domain / app_id / api_token） |
| kintone_prod | 本番環境の接続情報（domain / app_id / api_token） |
| kintone_item | アイテムアプリの接続情報（本番固定） |
| chatwork_token | ChatWork APIトークン |
| chatwork_room_id | 通知先ChatWorkルームID |
| output_dir | JSON保存先フォルダ |
| log_dir | ログ保存先フォルダ |

---

## kintone環境情報

| 項目 | 疑似環境 | 本番環境 |
|---|---|---|
| ドメイン | q0hpqcpormys.cybozu.com | reinc.cybozu.com |
| アプリ名 | 21_Pick_疑似環境 | 21_Pick |
| app_id | 5 | 299 |

アイテムアプリ（本番固定）：
- ドメイン：reinc.cybozu.com
- アプリ名：アイテム
- app_id：210

---

## kintone_register.py の処理内容

1. output_dir の最新 `mercari_orders_raw_*.json` を読み込む
2. 取得件数が0件なら処理をスキップ
3. 注文ごとに商品単位でレコードを組み立てる
   - JANでアイテムアプリ(app=210)を検索
   - 商品名を以下のフォーマットで結合する
```
     【現店舗名】【】アイテム名カテゴリ1 カテゴリ2タグ
```
   - createdAt（UTC）をJST変換して pick_date に登録
```
     例：2026/4/6 11:18:58
```
4. unique_id で重複チェック（既存ならスキップ）
5. kintone Pickアプリにレコードを登録
6. 登録失敗時はChatWorkに通知

---

## kintone登録フィールド一覧

| フィールドコード | 内容 | 取得元 |
|---|---|---|
| unique_id | order_{order_id}_{jan} | メルカリAPI + アイテムアプリ |
| order_id | order_{order_id} | メルカリAPI |
| order_link | メルカリ注文URL | メルカリAPI |
| jan | JANコード | メルカリAPI |
| online_link | https://ec.bazzstore.com/products/{jan} | JANから生成 |
| ec_shopcode | shop_id | config.json |
| mall | メルカリShops（固定） | 固定値 |
| image_link | 本撮影URL | 未対応（空欄）※1 |
| image_link2 | 未撮影URL | 未対応（空欄）※1 |
| item_name | 商品名（結合） | アイテムアプリ |
| brand | ブランド名 | アイテムアプリ |
| number_order | 販売価格 | アイテムアプリ |
| pick_date | 注文日時（JST） | メルカリAPI createdAt |

※1 image_link / image_link2 はS3からの取得方法が未確定。上長確認中。

---

## リカバリ手順

ChatWorkに登録失敗の通知が来た場合：

1. 通知に書いてある対象ファイルパスをコピーする
2. `run_retry.bat` にそのファイルをドラッグ＆ドロップする
3. 自動で未登録分のレコードをリトライする

手動実行の場合：
```
python kintone_register.py --file "C:\Users\bs00b\Desktop\実験\mercari_orders_raw_YYYYMMDD_HHMMSS.json"
```

---

## 障害時の切り分け

ログファイルの場所：
```
C:\Users\bs00b\Desktop\実験\メルカリ受注\log\
```

| 症状 | 確認ポイント |
|---|---|
| 取得0件 | mercari_ordersログでfday/sdayの時間窓を確認。その時間帯に注文があったかメルカリ管理画面で確認 |
| アイテム取得失敗（400） | GETヘッダーにContent-Typeが含まれていないか確認。APIトークンの権限を確認 |
| 重複チェック失敗（400） | 同上 |
| kintone登録失敗 CB_VA01 | フィールドの値が不正。unique_idの重複を確認 |
| kintone登録失敗 GAIA_IA02 | APIトークンが違うまたは「アプリを更新」を押していない |
| ChatWork通知が来た | run_retry.batに対象ファイルをドラッグ＆ドロップ |

---

## 既知の制約・今後の対応予定

### 2026年8月：メルカリAPI仕様変更対応

メルカリShopsは旧API群（ordersクエリ）を廃止予定。

変更が必要なファイル：
- `fetch_mercari_orders_and_save.py`
  - GraphQLクエリを `orders` から `orderTransactions` に書き換え
  - 取得フィールドのマッピング変更

### コメント（取引メッセージ）について

本番Pickアプリのcommentフィールドにはメルカリの取引メッセージが入っている。

現状：
- メルカリAPIのOrderレベルの `messages` フィールドは複数個フェーズ（2026年2月）以降は常に `null` になっている
- OrderTransactionレベルで管理されるため旧APIでは取得不可

対応時期：
- 2026年8月のOrderTransaction移行時に合わせて対応する
- 移行後は `orderTransactions` の `messages` フィールドから取得する

### image_link / image_link2 について

S3のURLからの取得方法が未確定。上長確認中。確認後に追加対応する。

---

## 注意事項

- `kintone_env` を `prod` に変更すると本番環境に登録される。切り替え前に必ず確認すること
- タスクスケジューラの実行間隔は5分、取得時間窓は10分のため、前回と5分間重複して取得する。unique_idの重複チェックで二重登録を防いでいる
- NWが弱い環境のため登録失敗時はChatWork通知を確認してリカバリ対応すること
