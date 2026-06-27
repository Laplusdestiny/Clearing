# /// script
# dependencies = [
#   "flet>=0.22.0",
#   "pandas>=2.0.0",
#   "gspread>=6.0.0",
# ]
# ///

import os
import re
import json
import datetime
import logging
import pandas as pd
import gspread
import flet as ft

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("ClearingApp")

# Config file location
CONFIG_FILE = 'config.json'

def load_config():
    """Load configuration from local JSON file."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                logger.info("Configuration loaded successfully: %s", config)
                return config
        except Exception as e:
            logger.exception("Failed to load config file: %s", CONFIG_FILE)
    else:
        logger.info("Configuration file %s does not exist. Using defaults.", CONFIG_FILE)
    return {"spreadsheet_key": "", "sheet_name": ""}

def save_config(spreadsheet_key, sheet_name=""):
    """Save configuration to local JSON file."""
    config = {
        "spreadsheet_key": spreadsheet_key.strip(),
        "sheet_name": sheet_name.strip()
    }
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        logger.info("Configuration saved successfully: %s", config)
    except Exception as e:
        logger.exception("Failed to save config file: %s", CONFIG_FILE)

def get_service_account_email():
    """Retrieve the client email from credentials.json if it exists."""
    if os.path.exists('credentials.json'):
        try:
            with open('credentials.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
                email = data.get('client_email', '')
                logger.info("Service account email retrieved from credentials.json: %s", email)
                return email
        except Exception as e:
            logger.exception("Failed to parse credentials.json")
    else:
        logger.warning("credentials.json does not exist. Cannot retrieve service account email.")
    return ''

def parse_spreadsheet_key(input_str):
    """Extract Google Spreadsheet Key from URL if pasted, otherwise return input."""
    input_str = input_str.strip()
    # Match the standard Google Spreadsheet ID pattern
    match = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', input_str)
    if match:
        return match.group(1)
    return input_str

def fetch_data(spreadsheet_key, sheet_name=""):
    """Fetch raw values from Google Spreadsheet using credentials.json."""
    logger.info("Attempting to fetch data from spreadsheet. Key: %s, Sheet Name: %s", spreadsheet_key, sheet_name)
    try:
        gc = gspread.service_account(filename='credentials.json')
        sh = gc.open_by_key(spreadsheet_key)
        if sheet_name:
            worksheet = sh.worksheet(sheet_name)
        else:
            worksheet = sh.get_worksheet(0)
        
        data = worksheet.get_all_values()
        logger.info("Successfully fetched %d rows from spreadsheet.", len(data) if data else 0)
    except Exception as e:
        logger.exception("Error occurred while fetching data from Google Spreadsheet")
        raise e
    
    if not data:
        return pd.DataFrame()
        
    headers = data[0]
    rows = data[1:]
    
    # Pad rows to match headers or minimum 9 columns
    max_cols = max(len(headers), 9)
    padded_rows = []
    for r in rows:
        if len(r) < max_cols:
            r = r + [''] * (max_cols - len(r))
        padded_rows.append(r[:max_cols])
        
    df = pd.DataFrame(padded_rows, columns=headers[:max_cols])
    return df

def clean_and_process_data(df):
    """
    Clean columns and calculate target billing months.
    Maps columns:
      0: Timestamp (タイムスタンプ)
      1: Date (日付)
      2: Transaction Type (分類（収入/支出）)
      3: Item Name (項目名（具体的な内容）)
      4: Amount (金額（円）)
      5: Category (大カテゴリー)
      6: Paid By (支払い者)
      7: Expense Division (費用の区分)
      8: Memo (メモ)
    """
    if df.empty:
        return pd.DataFrame()
        
    df_clean = pd.DataFrame()
    
    # 1. Parse Date/Timestamp
    # Try parsing index 1 (日付) first, fallback to index 0 (タイムスタンプ)
    raw_date = df.iloc[:, 1].astype(str).str.strip()
    raw_ts = df.iloc[:, 0].astype(str).str.strip()
    
    def parse_date(row):
        d_val = row['raw_date']
        ts_val = row['raw_ts']
        dt = pd.to_datetime(d_val, format='%Y/%m/%d', errors='coerce')
        if pd.isna(dt):
            dt = pd.to_datetime(d_val, errors='coerce')
        if pd.isna(dt):
            dt = pd.to_datetime(ts_val, errors='coerce')
        return dt

    temp_df = pd.DataFrame({'raw_date': raw_date, 'raw_ts': raw_ts})
    df_clean['timestamp_parsed'] = temp_df.apply(parse_date, axis=1)
    df_clean['raw_timestamp'] = df.iloc[:, 1].astype(str).str.strip()
    
    # 2. Parse Amount (Index 4: 金額（円）)
    def parse_amount(val):
        if not val or pd.isna(val):
            return 0
        clean = re.sub(r'[^\d-]', '', str(val))
        try:
            return int(clean) if clean else 0
        except ValueError:
            return 0
    df_clean['amount'] = df.iloc[:, 4].apply(parse_amount)
    
    # 3. Paid By (Index 6: 支払い者)
    df_clean['paid_by'] = df.iloc[:, 6].astype(str).str.strip()
    
    # 4. Category/Memo
    # Combine Index 5 (大カテゴリー), Index 3 (項目名), and Index 8 (メモ)
    def format_category(row):
        item = str(row.iloc[3]).strip()
        cat = str(row.iloc[5]).strip()
        memo = str(row.iloc[8]).strip()
        
        display = item
        if cat and cat.lower() != 'nan' and cat != '':
            display = f"[{cat}] {display}"
        if memo and memo.lower() != 'nan' and memo != '':
            display = f"{display} ({memo})"
        return display
        
    df_clean['category'] = df.apply(format_category, axis=1)
    
    # 5. Transaction Type (Index 2: 分類（収入/支出）)
    df_clean['tx_type'] = df.iloc[:, 2].astype(str).str.strip()
    
    # 6. Override Month (対象清算月) - None in this sheet, fallback to empty
    df_clean['override_month'] = ''
    
    # Calculate Billing Month
    def compute_billing_month(row):
        om = row['override_month']
        if om and re.match(r'^\d{4}/\d{2}$', om):
            return om
            
        dt = row['timestamp_parsed']
        if pd.notna(dt):
            if dt.day <= 20:
                return dt.strftime('%Y/%m')
            else:
                next_month = dt + pd.DateOffset(months=1)
                return next_month.strftime('%Y/%m')
        return None
        
    df_clean['billing_month'] = df_clean.apply(compute_billing_month, axis=1)
    df_clean = df_clean.dropna(subset=['billing_month'])
    
    return df_clean

def get_billing_period_text(billing_month):
    """Calculate the range YYYY/MM/21 - YYYY/MM/20 for a given YYYY/MM billing month."""
    try:
        y, m = map(int, billing_month.split('/'))
        if m == 1:
            start_y = y - 1
            start_m = 12
        else:
            start_y = y
            start_m = m - 1
        return f"{start_y:04d}/{start_m:02d}/21 〜 {y:04d}/{m:02d}/20"
    except Exception:
        return ""

def calculate_settlement(df, selected_month):
    """Calculate summary and settlement details for selected billing month."""
    month_df = df[df['billing_month'] == selected_month]
    
    is_income = month_df['tx_type'] == '収入'
    
    income_df = month_df[is_income]
    expense_df = month_df[~is_income]
    
    a_inc = income_df[income_df['paid_by'] == 'とおる']['amount'].sum()
    b_inc = income_df[income_df['paid_by'] == 'りお']['amount'].sum()
    
    a_exp = expense_df[expense_df['paid_by'] == 'とおる']['amount'].sum()
    b_exp = expense_df[expense_df['paid_by'] == 'りお']['amount'].sum()
    
    total_a = a_inc + a_exp
    total_b = b_inc + b_exp
    
    diff = total_a - total_b
    
    if diff > 0:
        sender = 'りお'
        receiver = 'とおる'
        amount = int(diff / 2)
    elif diff < 0:
        sender = 'とおる'
        receiver = 'りお'
        amount = int(abs(diff) / 2)
    else:
        sender = None
        receiver = None
        amount = 0
        
    return {
        'a_inc': a_inc,
        'b_inc': b_inc,
        'a_exp': a_exp,
        'b_exp': b_exp,
        'total_a': total_a,
        'total_b': total_b,
        'diff': diff,
        'sender': sender,
        'receiver': receiver,
        'amount': amount,
        'transactions': month_df.to_dict('records')
    }

def format_display_time(row):
    """Format datetime to MM/DD HH:MM for tight display, fallback to raw."""
    dt = row['timestamp_parsed']
    if pd.notna(dt):
        return dt.strftime('%m/%d %H:%M')
    return str(row['raw_timestamp'])

# --- Flet UI Main App ---
def main(page: ft.Page):
    page.title = "Clearing - ふたりのバーチャル家計簿"
    page.theme_mode = ft.ThemeMode.DARK
    page.window.width = 680
    page.window.height = 920
    page.window.resizable = True
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.scroll = ft.ScrollMode.AUTO
    
    # State tracking
    app_state = {
        "df": None,
        "selected_month": None,
        "spreadsheet_key": "",
        "sheet_name": "",
        "months": []
    }
    
    def check_and_load_app():
        # Loading view
        page.clean()
        page.add(
            ft.Container(
                content=ft.Column([
                    ft.ProgressRing(width=45, height=45, stroke_width=4, color=ft.Colors.BLUE_400),
                    ft.Text("スプレッドシートからデータを取得中...", size=16, color=ft.Colors.GREY_300, weight=ft.FontWeight.W_500)
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                alignment=ft.Alignment.CENTER,
                expand=True,
                height=300
            )
        )
        page.update()
        
        # 1. Credentials Check
        if not os.path.exists('credentials.json'):
            show_setup_view()
            return
            
        # 2. Config Check
        config = load_config()
        spreadsheet_key = config.get("spreadsheet_key", "")
        sheet_name = config.get("sheet_name", "")
        
        if not spreadsheet_key:
            show_setup_view()
            return
            
        # 3. Fetch & Process
        try:
            logger.info("Starting data fetch and process sequence.")
            raw_df = fetch_data(spreadsheet_key, sheet_name)
            df = clean_and_process_data(raw_df)
            
            app_state["df"] = df
            app_state["spreadsheet_key"] = spreadsheet_key
            app_state["sheet_name"] = sheet_name
            
            if not df.empty:
                months = sorted(df['billing_month'].dropna().unique().tolist(), reverse=True)
                app_state["months"] = months
                if not app_state["selected_month"] or app_state["selected_month"] not in months:
                    app_state["selected_month"] = months[0] if months else None
                logger.info("Data processed successfully. Found billing months: %s", months)
            else:
                app_state["months"] = []
                app_state["selected_month"] = None
                logger.warning("Processed DataFrame is empty.")
                
            show_dashboard_view()
        except Exception as e:
            logger.exception("Exception caught in check_and_load_app")
            error_msg = f"スプレッドシートの取得に失敗しました。キーや共有設定を確認してください。\nエラー詳細: {str(e)}"
            show_setup_view(error_message=error_msg)

    def show_setup_view(error_message=""):
        page.clean()
        
        creds_exist = os.path.exists('credentials.json')
        service_email = get_service_account_email() if creds_exist else ""
        
        config = load_config()
        current_key = config.get("spreadsheet_key", "")
        current_sheet = config.get("sheet_name", "")
        
        # Status Card
        status_controls = []
        if not creds_exist:
            status_controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Icon(ft.Icons.ERROR_OUTLINE, color=ft.Colors.RED_ACCENT, size=24),
                        ft.Text("credentials.json がフォルダに見つかりません", color=ft.Colors.RED_ACCENT, weight=ft.FontWeight.BOLD, size=14)
                    ]),
                    bgcolor="#3A1D1D",
                    padding=12,
                    border_radius=8,
                    border=ft.Border.all(1, ft.Colors.RED_900),
                    margin=ft.Margin.only(bottom=15)
                )
            )
        elif not current_key:
            status_controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=ft.Colors.ORANGE_ACCENT, size=24),
                        ft.Text("スプレッドシートが設定されていません", color=ft.Colors.ORANGE_ACCENT, weight=ft.FontWeight.BOLD, size=14)
                    ]),
                    bgcolor="#3A2D1D",
                    padding=12,
                    border_radius=8,
                    border=ft.Border.all(1, ft.Colors.ORANGE_900),
                    margin=ft.Margin.only(bottom=15)
                )
            )
            
        if error_message:
            status_controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Icon(ft.Icons.REPORT_PROBLEM_OUTLINED, color=ft.Colors.RED_ACCENT, size=24),
                        ft.Text(error_message, color=ft.Colors.RED_ACCENT, weight=ft.FontWeight.BOLD, expand=True, size=13)
                    ]),
                    bgcolor="#3A1D1D",
                    padding=12,
                    border_radius=8,
                    border=ft.Border.all(1, ft.Colors.RED_900),
                    margin=ft.Margin.only(bottom=15)
                )
            )
            
        # Instruction Panel
        setup_steps = [
            ft.Text("🏠 Clearing - アシスタント", size=22, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_300),
            ft.Text("Google Sheets と連携するために、以下の設定を行ってください。", size=14, color=ft.Colors.GREY_300),
            ft.Divider(color=ft.Colors.GREY_800)
        ]
        
        # Step 1: JSON File
        s1_icon = ft.Icons.CHECK_CIRCLE if creds_exist else ft.Icons.RADIO_BUTTON_UNCHECKED
        s1_color = ft.Colors.GREEN_400 if creds_exist else ft.Colors.GREY_500
        setup_steps.append(
            ft.Row([
                ft.Icon(s1_icon, color=s1_color, size=24),
                ft.Column([
                    ft.Text("1. API認証ファイルの配置", weight=ft.FontWeight.BOLD, size=14),
                    ft.Text("Google Cloud Console でサービスアカウントの鍵 (JSON) を生成し、", size=12, color=ft.Colors.GREY_400),
                    ft.Text("ファイル名を 'credentials.json' として本アプリと同じフォルダに置いてください。", size=12, color=ft.Colors.GREY_400)
                ], spacing=2, expand=True)
            ], vertical_alignment=ft.CrossAxisAlignment.START)
        )
        
        # Step 2: Sharing Sheet
        if creds_exist and service_email:
            async def copy_email_to_clipboard(e):
                await ft.Clipboard().set(service_email)
                snack = ft.SnackBar(content=ft.Text("コピーしました"))
                page.overlay.append(snack)
                snack.open = True
                page.update()

            setup_steps.append(
                ft.Row([
                    ft.Icon(ft.Icons.RADIO_BUTTON_UNCHECKED, color=ft.Colors.GREY_500, size=24),
                    ft.Column([
                        ft.Text("2. スプレッドシートの権限共有", weight=ft.FontWeight.BOLD, size=14),
                        ft.Text("対象のGoogleスプレッドシートを開き、「共有」から以下のサービスアカウントの", size=12, color=ft.Colors.GREY_400),
                        ft.Text("メールアドレスに対して閲覧・編集権限を付与してください：", size=12, color=ft.Colors.GREY_400),
                        ft.Row([
                            ft.TextField(value=service_email, read_only=True, dense=True, text_size=12, expand=True, content_padding=8),
                            ft.IconButton(
                                icon=ft.Icons.COPY_ALL, 
                                tooltip="メールアドレスをコピー",
                                on_click=lambda e: page.run_task(copy_email_to_clipboard, e)
                            )
                        ], spacing=5)
                    ], spacing=2, expand=True)
                ], vertical_alignment=ft.CrossAxisAlignment.START)
            )
        else:
            setup_steps.append(
                ft.Row([
                    ft.Icon(ft.Icons.RADIO_BUTTON_UNCHECKED, color=ft.Colors.GREY_500, size=24),
                    ft.Text("2. credentials.jsonの配置後に表示されるメールアドレスにシートを共有します。", size=13, color=ft.Colors.GREY_500)
                ])
            )
            
        # Form UI
        key_field = ft.TextField(
            label="GoogleスプレッドシートのURL または キー",
            value=current_key,
            hint_text="https://docs.google.com/spreadsheets/d/...",
            expand=True,
            border_color=ft.Colors.GREY_700,
            focused_border_color=ft.Colors.BLUE_400,
            text_size=14
        )
        sheet_field = ft.TextField(
            label="シート名 (空欄時は左端のシート)",
            value=current_sheet,
            hint_text="シート1",
            width=220,
            border_color=ft.Colors.GREY_700,
            focused_border_color=ft.Colors.BLUE_400,
            text_size=14
        )
        
        def on_save(e):
            if not key_field.value.strip():
                snack = ft.SnackBar(content=ft.Text("スプレッドシートのキーを入力してください。"))
                page.overlay.append(snack)
                snack.open = True
                page.update()
                return
            parsed_key = parse_spreadsheet_key(key_field.value)
            save_config(parsed_key, sheet_field.value)
            check_and_load_app()
            
        def on_creds_retry(e):
            check_and_load_app()

        action_controls = []
        if not creds_exist:
            action_controls.append(
                ft.ElevatedButton(
                    "ファイルを配置したので再試行する",
                    icon=ft.Icons.REFRESH,
                    on_click=on_creds_retry,
                    style=ft.ButtonStyle(
                        color=ft.Colors.WHITE,
                        bgcolor=ft.Colors.BLUE_600,
                        padding=15
                    )
                )
            )
        else:
            action_controls.append(ft.Row([key_field]))
            action_controls.append(
                ft.Row([
                    sheet_field,
                    ft.ElevatedButton(
                        "設定を保存して起動",
                        icon=ft.Icons.PLAY_ARROW_ROUNDED,
                        on_click=on_save,
                        height=50,
                        style=ft.ButtonStyle(
                            color=ft.Colors.WHITE,
                            bgcolor=ft.Colors.BLUE_700,
                            padding=ft.Padding.symmetric(horizontal=20)
                        )
                    )
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
            )

        page.add(
            ft.Container(
                content=ft.Column([
                    *status_controls,
                    *setup_steps,
                    ft.Divider(color=ft.Colors.GREY_800),
                    *action_controls
                ], spacing=15),
                padding=25,
                bgcolor=ft.Colors.SURFACE_CONTAINER,
                border_radius=12,
                border=ft.Border.all(1, ft.Colors.GREY_800),
                width=600,
                margin=ft.Margin.only(top=20)
            )
        )
        page.update()

    def show_dashboard_view():
        page.clean()
        
        months = app_state["months"]
        if not months:
            # Empty Sheet View
            page.add(
                ft.Container(
                    content=ft.Column([
                        ft.Icon(ft.Icons.FOLDER_OPEN_OUTLINED, size=50, color=ft.Colors.GREY_600),
                        ft.Text("集計対象のデータが見つかりません", size=18, weight=ft.FontWeight.BOLD),
                        ft.Text("Googleスプレッドシートが空か、正しい列構成か確認してください。", size=13, color=ft.Colors.GREY_400),
                        ft.VerticalDivider(height=10),
                        ft.Row([
                            ft.ElevatedButton("再読み込み", on_click=lambda e: check_and_load_app(), icon=ft.Icons.REFRESH),
                            ft.TextButton("設定を変更", on_click=lambda e: show_setup_view(), icon=ft.Icons.SETTINGS)
                        ], spacing=15, alignment=ft.MainAxisAlignment.CENTER)
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=35,
                    bgcolor=ft.Colors.SURFACE_CONTAINER,
                    border_radius=12,
                    width=600,
                    margin=ft.Margin.only(top=50),
                    alignment=ft.Alignment.CENTER
                )
            )
            page.update()
            return
            
        # 1. Calculation
        settlement = calculate_settlement(app_state["df"], app_state["selected_month"])
        
        # Month Selector Dropdown
        month_dropdown = ft.Dropdown(
            options=[ft.dropdown.Option(m) for m in months],
            value=app_state["selected_month"],
            width=140,
            dense=True,
            on_select=on_month_selected,
            border_radius=8,
            border_color=ft.Colors.BLUE_700,
            focused_border_color=ft.Colors.BLUE_400
        )
        
        # Title and Dropdown row
        title_section = ft.Row([
            ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.ACCOUNT_BALANCE_WALLET_ROUNDED, color=ft.Colors.BLUE_400, size=28),
                    ft.Text("Clearing", size=26, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                ], spacing=8),
                ft.Text("ふたりのバーチャル共通口座・家計簿ダッシュボード", size=12, color=ft.Colors.GREY_400)
            ]),
            ft.Row([
                month_dropdown,
                ft.IconButton(
                    icon=ft.Icons.REFRESH,
                    tooltip="スプレッドシートから再読み込み",
                    on_click=lambda e: check_and_load_app(),
                    icon_color=ft.Colors.BLUE_300,
                    bgcolor=ft.Colors.SURFACE_CONTAINER
                )
            ], spacing=5)
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
        
        # Period Info Bar
        period_text = get_billing_period_text(app_state["selected_month"])
        period_badge = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.CALENDAR_MONTH, size=16, color=ft.Colors.BLUE_200),
                ft.Text(f"{app_state['selected_month']}月分 集計対象期間:  {period_text}", size=12, color=ft.Colors.BLUE_200, weight=ft.FontWeight.W_500)
            ], spacing=6),
            bgcolor="#1A2D42",
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            border_radius=8,
            border=ft.Border.all(1, "#2C435F")
        )
        
        # 2. Main Action Card
        settlement_card = create_settlement_card(settlement)
        
        # 3. Monthly Summary Cards
        summary_row = create_summary_row(settlement)
        
        # 4. Transaction Log Table
        tx_logs = settlement['transactions']
        table_rows = update_table_rows(tx_logs)
        
        history_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("日時", weight=ft.FontWeight.BOLD, size=13)),
                ft.DataColumn(ft.Text("支払者", weight=ft.FontWeight.BOLD, size=13)),
                ft.DataColumn(ft.Text("カテゴリ/メモ", weight=ft.FontWeight.BOLD, size=13)),
                ft.DataColumn(ft.Text("金額", weight=ft.FontWeight.BOLD, size=13), numeric=True),
            ],
            rows=table_rows,
            column_spacing=25,
            horizontal_lines=ft.BorderSide(1, ft.Colors.GREY_800),
            heading_row_color=ft.Colors.SURFACE_CONTAINER
        )
        
        history_scroll_container = ft.Container(
            content=ft.ListView([history_table], expand=True, spacing=10),
            height=340,
            border=ft.Border.all(1, ft.Colors.GREY_800),
            border_radius=10,
            bgcolor="#11161D",
            padding=10
        )
        
        # Main dashboard vertical assembly
        page.add(
            ft.Container(
                content=ft.Column([
                    title_section,
                    period_badge,
                    ft.Divider(height=10, color=ft.Colors.GREY_800),
                    settlement_card,
                    ft.Text("今月の負担実績サマリー", size=15, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                    summary_row,
                    ft.Divider(height=15, color=ft.Colors.GREY_800),
                    ft.Row([
                        ft.Text("明細履歴一覧", size=15, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                        ft.Text(f"全 {len(tx_logs)} 件", size=12, color=ft.Colors.GREY_400)
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    history_scroll_container,
                    ft.Row([
                        ft.TextButton(
                            "スプレッドシート設定を変更",
                            icon=ft.Icons.SETTINGS_OUTLINED,
                            on_click=lambda e: show_setup_view(),
                            style=ft.ButtonStyle(color=ft.Colors.GREY_400)
                        )
                    ], alignment=ft.MainAxisAlignment.END)
                ], spacing=12),
                width=640,
                padding=ft.Padding.only(bottom=20)
            )
        )
        page.update()

    def on_month_selected(e):
        app_state["selected_month"] = e.control.value
        show_dashboard_view()

    def create_settlement_card(settlement):
        sender = settlement['sender']
        receiver = settlement['receiver']
        amount = settlement['amount']
        
        if sender and receiver and amount > 0:
            # Color variables according to direction:
            # Receiver is とおる -> Light green
            # Receiver is りお -> Light orange
            if receiver == 'とおる':
                bg_color = "#152E20"  # Soft forest green for dark theme
                border_color = "#388E3C"
                text_color = ft.Colors.GREEN_300
                accent_color = ft.Colors.GREEN_100
                icon_color = ft.Colors.GREEN_400
            else:
                bg_color = "#331E11"  # Soft warm copper/orange for dark theme
                border_color = "#E65100"
                text_color = ft.Colors.ORANGE_300
                accent_color = ft.Colors.ORANGE_100
                icon_color = ft.Colors.ORANGE_400
                
            card_content = ft.Row([
                ft.Icon(ft.Icons.SWAP_HORIZ_ROUNDED, color=icon_color, size=36),
                ft.Column([
                    ft.Text("今月の清算アクション", size=12, color=text_color, weight=ft.FontWeight.W_500),
                    ft.Row([
                        ft.Text(f"{sender} ", size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                        ft.Icon(ft.Icons.ARROW_RIGHT_ALT_ROUNDED, size=18, color=text_color),
                        ft.Text(f" {receiver}", size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                        ft.Text(" へ ", size=13, color=ft.Colors.GREY_300),
                        ft.Text(f"{amount:,}円", size=22, weight=ft.FontWeight.BOLD, color=accent_color),
                        ft.Text(" を送金してください", size=13, color=ft.Colors.GREY_300)
                    ], alignment=ft.MainAxisAlignment.START, vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True)
                ], expand=True, spacing=4)
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=15)
        else:
            bg_color = "#20252D"
            border_color = ft.Colors.BLUE_GREY_600
            card_content = ft.Row([
                ft.Icon(ft.Icons.DONE_ALL_ROUNDED, color=ft.Colors.BLUE_GREY_300, size=34),
                ft.Column([
                    ft.Text("今月の清算アクション", size=12, color=ft.Colors.BLUE_GREY_300, weight=ft.FontWeight.W_500),
                    ft.Text("清算の必要はありません (相殺差額 0円)", size=15, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE)
                ], expand=True, spacing=2)
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=15)
            
        return ft.Card(
            content=ft.Container(
                content=card_content,
                padding=20,
                border_radius=12,
                bgcolor=bg_color,
                border=ft.Border.all(1.5, border_color)
            ),
            elevation=6,
            margin=ft.Margin.only(bottom=5)
        )

    def create_summary_row(settlement):
        a_inc = settlement['a_inc']
        a_exp = settlement['a_exp']
        total_a = settlement['total_a']
        
        b_inc = settlement['b_inc']
        b_exp = settlement['b_exp']
        total_b = settlement['total_b']
        
        def build_card(name, inc, exp, total, theme_color, text_accent):
            return ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Icon(ft.Icons.FACE_ROUNDED, color=theme_color, size=20),
                        ft.Text(name, size=15, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE)
                    ], spacing=6),
                    ft.Divider(height=8, color=ft.Colors.GREY_800),
                    ft.Row([
                        ft.Text("入金 (収入)", size=12, color=ft.Colors.GREY_400),
                        ft.Text(f"{inc:,}円", size=12, color=ft.Colors.WHITE, weight=ft.FontWeight.W_500)
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    ft.Row([
                        ft.Text("立替 (支出)", size=12, color=ft.Colors.GREY_400),
                        ft.Text(f"{exp:,}円", size=12, color=ft.Colors.WHITE, weight=ft.FontWeight.W_500)
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    ft.Divider(height=6, color=ft.Colors.GREY_800),
                    ft.Row([
                        ft.Text("総貢献額", size=12, color=theme_color, weight=ft.FontWeight.BOLD),
                        ft.Text(f"{total:,}円", size=15, color=text_accent, weight=ft.FontWeight.BOLD)
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
                ], spacing=6),
                bgcolor="#15191E",
                border=ft.Border.all(1, ft.Colors.GREY_800),
                border_radius=10,
                padding=14,
                expand=True
            )
            
        card_a = build_card("とおる", a_inc, a_exp, total_a, ft.Colors.BLUE_400, ft.Colors.BLUE_200)
        card_b = build_card("りお", b_inc, b_exp, total_b, ft.Colors.PINK_400, ft.Colors.PINK_200)
        
        return ft.Row([card_a, card_b], spacing=15)

    def update_table_rows(transactions):
        rows = []
        # Sort newest first based on datetime object or minimal Timestamp
        sorted_txs = sorted(
            transactions,
            key=lambda x: x['timestamp_parsed'] if pd.notna(x['timestamp_parsed']) else pd.Timestamp.min,
            reverse=True
        )
        
        for tx in sorted_txs:
            paid_by = tx['paid_by']
            if paid_by == 'とおる':
                badge_bg = "#1A365D"
                badge_fg = ft.Colors.BLUE_300
            elif paid_by == 'りお':
                badge_bg = "#5B21B6"
                badge_fg = ft.Colors.PINK_300
            else:
                badge_bg = "#2D3748"
                badge_fg = ft.Colors.GREY_300
                
            badge = ft.Container(
                content=ft.Text(paid_by, color=badge_fg, size=11, weight=ft.FontWeight.BOLD),
                bgcolor=badge_bg,
                padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                border_radius=5
            )
            
            amount_str = f"{tx['amount']:,}円"
            
            rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(format_display_time(tx), size=12)),
                        ft.DataCell(badge),
                        ft.DataCell(ft.Text(tx['category'], size=12)),
                        ft.DataCell(ft.Text(amount_str, size=12, weight=ft.FontWeight.BOLD, text_align=ft.TextAlign.RIGHT))
                    ]
                )
            )
        return rows

    # Initial boot sequence
    check_and_load_app()

if __name__ == "__main__":
    ft.app(target=main, view=ft.AppView.WEB_BROWSER, host="0.0.0.0", port=8550)
