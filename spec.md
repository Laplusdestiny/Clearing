# 🚀 AI Generation Prompt: カップル用バーチャル家計簿アプリ（Flet）

## [Context & Goal]

Googleフォームから入力されたGoogleスプレッドシートのデータをデータベースとして読み込み、PythonのUIライブラリ「Flet」を用いたローカル運用の家計簿ダッシュボードアプリを作成してください 。
実際の共通口座は作らず、データ上で各々の立替（支出）と拠出（共通口座への入金）を記録し、毎月20日締め（21日〜翌20日）で相殺・清算する「バーチャル共通口座」の仕組みを実装します 。ログイン認証は不要です 。

---

## 1. System Requirements & Tech Stack

* **Language:** Python 3.x
* **UI Framework:** `flet` (モダンなマテリアルデザイン、レスポンシブ対応)
* **Data Library:** `pandas`
* **Database Connection:** `gspread` (Google Sheets API / Service Account 認証を使用。認証ファイル名: `credentials.json`)

---

## 2. Database Schema (Google Sheets)

読み込むスプレッドシートは以下の5列構成です 。

1. **タイムスタンプ (Column A):** `YYYY/MM/DD HH:MM:SS` (日時型としてパース)

1. **金額 (Column B):** 整数型 (Int)

1. **支払った人 (Column C):** 文字列型 (`Aさん` または `Bさん`)

1. **カテゴリ/メモ (Column D):** 文字列型（例: `食費`, `日用品`, `共通口座への入金（収入）`）

1. **対象清算月 (Column E):** 文字列型 (形式: `YYYY/MM`)。通常は空欄 。

---

## 3. Core Logic & Calculation (Strict Specification)

毎月20日締めの計算ロジックを以下のように厳密に実装してください 。

### Step 1: 集計対象月 (`billing_month`) の判定

各行のデータに対し、以下のルールで集計対象月（`YYYY/MM`）を割り当てます 。

1. **E列（対象清算月）に値がある場合:** その値を最優先とする（例: `2026/07`） 。

1. **E列が空欄の場合:** A列（タイムスタンプ）の日付 $D$ から自動判定 。

    * $D$ の日（Day） $\le 20$ の場合 $\rightarrow$ 当月の年月（`YYYY/MM`）

    * $D$ の日（Day） $\ge 21$ の場合 $\rightarrow$ 翌月の年月（`YYYY/MM`） *(例: 2026/05/21 〜 2026/06/20 の期間のデータはすべて `2026/06` として集計)*

### Step 2: データの分類と集計

選択された集計月内において、データを「入金（収入）」と「共通支出」に分類します 。

* **入金（収入）データの集計:**
  * カテゴリが `共通口座への入金（収入）` である行 。
  * $A_{inc}$ = Aさんの入金合計額
  * $B_{inc}$ = Bさんの入金合計額

* **共通支出データの集計:**
  * カテゴリが `共通口座への入金（収入）` **以外**である行 。
  * $A_{exp}$ = Aさんの立替（支出）合計額
  * $B_{exp}$ = Bさんの立替（支出）合計額

### Step 3: 清算金額 (`clearing_amount`) の計算（入金比率按分ロジック）

各自の入金額の比率を「負担比率」とし、共通支出（立替）の総額をその比率で分け合い、実際の立替額との差額から最終的な送金額と送金方向を決定します。

* **プール金総額 ($Total_{inc}$):**
$$Total_{inc} = A_{inc} + B_{inc}$$

* **共通支出総額 ($Total_{exp}$):**
$$Total_{exp} = A_{exp} + B_{exp}$$

* **負担比率 ($R_A, R_B$):**
  * $Total_{inc} > 0$ の場合:
    $$R_A = \frac{A_{inc}}{Total_{inc}}$$
    $$R_B = \frac{B_{inc}}{Total_{inc}}$$
  * $Total_{inc} == 0$ の場合:
    $$R_A = 0.5$$
    $$R_B = 0.5$$

* **各自の負担額 ($Burden_A, Burden_B$):**
  $$Burden_A = \text{round}(Total_{exp} \times R_A)$$
  $$Burden_B = Total_{exp} - Burden_A$$

* **清算差額 ($Diff_A, Diff_B$):**
  $$Diff_A = A_{exp} - Burden_A$$
  $$Diff_B = B_{exp} - Burden_B$$

* **送金判定:**
  * $Diff_A > 0$ の場合: **BさんからAさんへ** $Diff_A$ 円を送金
  * $Diff_A < 0$ の場合: **AさんからBさんへ** $|Diff_A|$ 円を送金
  * $Diff_A = 0$ の場合: 送金なし（清算不要）

---

## 4. UI/UX Dashboard Requirements (Flet)

ノンデザイナーでも直感的に理解できる、クリーンで視覚的な1画面ダッシュボード（スマホ・Web両対応 of コンテナサイズ）を構築してください 。

* **Header Component:**
* タイトル（例: 「🏠 ふたりのバーチャル家計簿」）

* 現在表示中の集計月と対象期間（例: `2026/06分 (2026/05/21 〜 2026/06/20)`)

* **Main Settlement Card (Most Important):**
  * Fletの `Card` コンポーネントを使用し、画面中央に強調表示 。

  * 例: **「Bさん ➔ Aさん へ 【 10,000 円 】 送金してください」**

  * 送金方向に応じてカードの背景色をソフトに変える（例: Aさんへの送金なら薄いグリーン、Bさんへの送金なら薄いオレンジ） 。

* **Virtual Shared Account Card:**
  * 二人まとめての共通口座の状況を示すカードを追加表示。
  * 表示項目：プール金総額（入金合計）、共通支出総額（立替合計）、現在の口座残高（プール金総額 - 支出総額）。

* **Detail & History Component:**
  * **今月の実績サマリー:** `ListTile` や `Row` を用い、[Aさん・Bさんそれぞれの入金合計（負担比率%） / 立替合計] を綺麗に整列して表示 。
  * **履歴テーブル:** `DataTable` または `ListView` を使用し、直近のデータを最新順にソートして、日付・金額・支払者・カテゴリ/メモをグリッド表示 。

---

## 5. Coding Constraints (AIへの指示制限)

* スプレッドシート連携（`gspread`経由のデータ取得）と、UI描画の処理を適切に関数化（リファクタリング）してください 。

* 金額表示にはすべてカンマ区切り（例: `10,000円`）を適用してください 。

* ローカルで即使えるように、`if __name__ == "__main__": ft.app(target=main)` で締めくくられた、単一の完結したPythonスクリプトファイルとして出力してください 。
