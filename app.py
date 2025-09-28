# -*- coding: utf-8 -*-
"""
製品調達AIエージェント Streamlitアプリケーション
（最終アクセス強化・超詳細デバッグ版）
"""

# ==============================================================================
# ライブラリのインポート
# ==============================================================================
import streamlit as st
import pandas as pd
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup
import json
import concurrent.futures

# urllib3のSSL警告を非表示にする
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
# === Bright Data API 連携関数 (プロキシアクセス + 超詳細デバッグ) ===
# ==============================================================================

def get_page_content_with_brightdata(url: str, api_key: str, debug_mode: bool = False) -> tuple[str | None, str | None]:
    """
    [最終修正] Bright Dataのプロキシマネージャーを経由してHTMLを取得する。
    失敗時には詳細なデバッグ情報を表示する。
    """
    # ### ▼▼▼ あなたのBright Data接続情報に更新済みです ▼▼▼ ###
    BRD_HOST = 'brd.superproxy.io'
    BRD_PORT = 22225
    BRD_USERNAME = 'brd-customer-hl_3c49a4bb-zone-scraping_browser1'
    BRD_PASSWORD = 'c6v8ohrtd8zf'
    # ### ▲▲▲ 設定完了 ▲▲▲ ###

    proxy_url = f'http://{BRD_USERNAME}:{BRD_PASSWORD}@{BRD_HOST}:{BRD_PORT}'
    proxies = {'http': proxy_url, 'https': proxy_url}
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'
    }

    try:
        response = requests.get(url, headers=headers, proxies=proxies, verify=False, timeout=60)
        
        # [新機能] 超詳細デバッグログ
        if debug_mode:
            with st.expander(f"【詳細デバッグ】プロキシアクセス試行 (URL: {url})"):
                st.write(f"**ステータスコード:** `{response.status_code}`")
                st.write("**レスポンスヘッダー:**")
                st.json(dict(response.headers))
                st.write("**レスポンスボディ (最初の1000文字):**")
                st.code(response.text[:1000] if response.text else "レスポンスボディは空です。", language='html')

        response.raise_for_status()
        return response.text, None

    except requests.exceptions.RequestException as e:
        error_message = f"プロキシ経由でのアクセスに失敗しました: {e}"
        # エラーレスポンスがあれば、その内容も表示
        if hasattr(e, 'response') and e.response is not None:
             error_message += f"\n**ステータスコード:** `{e.response.status_code}`\n**レスポンス:** `{e.response.text[:200]}`"
        return None, error_message


def search_product_urls_with_brightdata(query: str, api_key: str, debug_mode: bool = False) -> list:
    st.info(f"【Bright Data】クエリ「{query}」で検索リクエストを送信...")
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    google_search_url = f"https://www.google.co.jp/search?q={urllib.parse.quote(query)}&hl=ja&gl=jp&ceid=JP:ja"
    payload = {'zone': 'serp_api1', 'url': google_search_url}

    try:
        initial_response = requests.post('https://api.brightdata.com/serp/req', headers=headers, json=payload, timeout=30)
        initial_response.raise_for_status()
        response_id = initial_response.headers.get('x-response-id')
        if not response_id:
            st.error("エラー: SERP APIからのresponse_idが取得できませんでした。")
            return []
        st.info(f"【Bright Data】リクエスト受付完了 (Response ID: {response_id})。結果を待機します...")
        result_url = f'https://api.brightdata.com/serp/get_result?response_id={response_id}'
        for i in range(1, 16):
            time.sleep(2)
            try:
                result_response = requests.get(result_url, headers={'Authorization': f'Bearer {api_key}'}, timeout=30)
                if result_response.status_code == 200:
                    if not result_response.text: return []
                    soup = BeautifulSoup(result_response.text, 'html.parser')
                    urls = [ a.get('href') for a in soup.find_all('a', href=True) if a.get('href') and a.get('href').startswith('http') and not a.get('href').startswith('https://www.google.com') ]
                    unique_urls = list(dict.fromkeys(urls))
                    st.success(f"【Bright Data】「{query}」から{len(unique_urls)}件のURLを抽出しました。")
                    return unique_urls
                elif result_response.status_code != 202:
                    st.error(f"結果取得エラー: 予期しないステータスコード {result_response.status_code} を受け取りました。")
                    return []
            except requests.exceptions.RequestException as e:
                st.error(f"結果取得試行中にネットワークエラーが発生しました: {e}")
                return []
        st.error("検索結果の取得がタイムアウトしました。")
        return []
    except requests.exceptions.RequestException as e:
        st.error(f"Bright Data APIの初期呼び出しエラー: {e}")
        return []

# ==============================================================================
# === AIエージェントによる情報抽出関数 (ブロック検知機能付き) ===
# ==============================================================================

def is_blocked_page(html_content: str) -> bool:
    if not html_content: return False
    block_keywords = [
        "cloudflare", "access denied", "site is protected", "checking your browser",
        "captcha", "are you a human", "アクセスが拒否されました", "サイト所有者によってブロック"
    ]
    lower_content = html_content.lower()
    for keyword in block_keywords:
        if keyword in lower_content:
            return True
    return False

def analyze_page_and_extract_info(url: str, product_name: str, gemini_api_key: str, brightdata_api_key: str, debug_mode: bool = False) -> dict | None:
    html_content, error = get_page_content_with_brightdata(url, brightdata_api_key, debug_mode)
    if error or not html_content:
        st.warning(f"URL {url} のコンテンツ取得に失敗しました: {error}")
        return None

    if is_blocked_page(html_content):
        st.error(f"URL {url} はセキュリティシステムによりブロックされました。")
        return None

    soup = BeautifulSoup(html_content, 'html.parser')
    for s in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'form']):
        s.decompose()
    body_text = soup.body.get_text(separator=' ', strip=True) if soup.body else ''
    
    if not body_text:
        st.info(f"URL {url} からテキスト情報を抽出できませんでした。")
        return None
    if len(body_text) > 18000:
        body_text = body_text[:18000]

    prompt = f"""
    You are an Analyst Agent. Your task is to analyze... (省略)
    """
    # (プロンプトは変更なしのため省略)
    prompt = f"""
    You are an Analyst Agent. Your task is to analyze the following text content from a product webpage and extract key information about the specified product.
    **Product to find:** "{product_name}"
    **Webpage Content:**
    ---
    {body_text}
    ---
    **Instructions:**
    1.  First, identify the main product details like `productName`, `modelNumber`, and `manufacturer`.
    2.  Next, find all available purchasing options (sizes, packages, capacities, etc.) for this product.
    3.  For each option, extract its size/specification, price, and stock status.
    4.  Compile this information into a list of objects under the `offers` key. Each object in the list should represent one purchase option.
    5.  **CRITICAL RULE for `price`:** The price MUST be in Japanese Yen. Look for numbers clearly labeled with Japanese price words (「価格」, 「値段」, 「販売価格」, 「定価」) or symbols (「￥」, 「円」). If a price is in a foreign currency (like $, €, USD, EUR), you MUST ignore it and set the price to 0. If no Japanese Yen price is found, use 0.
    6.  For `inStock` in each offer, determine the stock status. `true` if words like "在庫あり", "カートに入れる", "購入可能", "in stock" are present. `false` if "在庫なし", "入荷待ち", "out of stock" are found.
    7.  If no specific options are listed and there is only a single price for the product, create a single entry in the `offers` list. Use "N/A" for the `size` if it's not specified.
    8.  If you cannot find any relevant product information, return an empty list for the `offers` key.
    9.  Your response MUST be a single, valid JSON object.

    **JSON Output Structure:**
    {{
      "productName": "string",
      "modelNumber": "string",
      "manufacturer": "string",
      "offers": [
        {{
          "size": "string",
          "price": number,
          "inStock": boolean
        }}
      ]
    }}
    """
    try:
        payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}}
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={gemini_api_key}"
        response = requests.post(api_url, headers={'Content-Type': 'application/json'}, json=payload, timeout=45)
        response.raise_for_status()
        result = response.json()
        if not result.get('candidates'):
            st.warning(f"Gemini APIから候補が返されませんでした。URL: {url}")
            return None
        response_text = result['candidates'][0]['content']['parts'][0]['text']
        raw_data = json.loads(response_text)
        return raw_data if isinstance(raw_data, dict) else None
    except json.JSONDecodeError:
        st.error(f"Gemini APIからのレスポンスがJSON形式ではありませんでした。URL: {url}, Response: {response_text[:200]}...")
        return None
    except Exception as e:
        st.error(f"Gemini API呼び出しでエラーが発生しました: {e}。URL: {url}")
        return None

# ==============================================================================
# === 統括エージェント ===
# ==============================================================================
def orchestrator_agent(product_info: dict, gemini_api_key: str, brightdata_api_key: str, preferred_sites: list, debug_mode: bool = False) -> list:
    product_name = product_info['ProductName']
    manufacturer = product_info.get('Manufacturer', '')
    st.subheader(f"【統括エージェント】 \"{product_name}\" の情報収集を開始します。")

    base_query = f"{manufacturer} {product_name}"
    site_map = {
        'コスモバイオ': 'cosmobio.co.jp', 'フナコシ': 'funakoshi.co.jp', 'AXEL': 'axel.as-1.co.jp',
        'Selleck': 'selleck.co.jp', 'MCE': 'medchemexpress.com', 'Nakarai': 'nacalai.co.jp',
        'FUJIFILM': 'labchem-wako.fujifilm.com', '関東化学': 'kanto.co.jp', 
        'TCI': 'tcichemicals.com', 'Merck': 'merck.com', '和光純薬': 'hpc-j.co.jp'
    }
    search_queries = [f"site:{site_map[site_name]} {base_query}" for site_name in preferred_sites if site_name in site_map]
    search_queries.append(base_query)

    all_urls = []
    for query in search_queries:
        all_urls.extend(search_product_urls_with_brightdata(query, brightdata_api_key, debug_mode))
    
    unique_urls = list(dict.fromkeys(all_urls))
    html_urls = [url for url in unique_urls if not url.lower().endswith(('.pdf', '.xls', '.xlsx', '.doc', '.docx'))]
    if not html_urls:
        st.warning("関連するWebページのURLが見つかりませんでした。")
        return []
    
    found_pages = []
    st.info(f"{len(html_urls)}件のHTMLページを並列で分析します...")
    progress_text = "Webページを分析中..."
    my_bar = st.progress(0, text=progress_text)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_to_url = {
            executor.submit(analyze_page_and_extract_info, url, product_name, gemini_api_key, brightdata_api_key, debug_mode): url 
            for url in html_urls
        }
        
        for i, future in enumerate(concurrent.futures.as_completed(future_to_url)):
            url = future_to_url[future]
            try:
                page_details = future.result()
                if page_details and page_details.get("offers"):
                    page_details['sourceUrl'] = url
                    found_pages.append(page_details)
            except Exception as exc:
                st.error(f"URL {url} の処理中にエラーが発生しました: {exc}")
            
            my_bar.progress((i + 1) / len(html_urls), text=f"{progress_text} ({i + 1}/{len(html_urls)} ページ処理済み)")
            
    st.success(f"【統括エージェント】{len(found_pages)}ページから製品情報を抽出しました。")
    return found_pages

# ==============================================================================
# === Streamlit UI アプリケーション部分 ===
# ==============================================================================

st.set_page_config(layout="wide")
st.title("製品調達AIエージェント")

st.sidebar.header("APIキー設定")
try:
    gemini_api_key = st.secrets["GOOGLE_API_KEY"]
    brightdata_api_key = st.secrets["BRIGHTDATA_API_KEY"]
    st.sidebar.success("APIキーが設定されています。")
except KeyError:
    st.sidebar.error("StreamlitにAPIキーが設定されていません。")
    gemini_api_key = ""
    brightdata_api_key = ""

st.sidebar.header("検索条件")
product_name_input = st.sidebar.text_input("製品名 (必須)", placeholder="例: Y27632")
manufacturer_input = st.sidebar.text_input("メーカー", placeholder="例: Selleck")
min_price_input = st.sidebar.number_input("最低価格 (円)", min_value=0, value=0, step=100)
max_price_input = st.sidebar.number_input("最高価格 (円)", min_value=0, value=0, step=100)
debug_mode_checkbox = st.sidebar.checkbox("デバッグモードを有効にする (詳細ログ表示)")
search_button = st.sidebar.button("検索開始", type="primary")

if search_button:
    if not gemini_api_key or not brightdata_api_key:
        st.error("APIキーが設定されていません。StreamlitのSecretsにキーを登録してください。")
    elif not product_name_input:
        st.error("製品名を入力してください。")
    else:
        with st.spinner('AIエージェントが情報収集中...しばらくお待ちください。'):
            product_info = {
                'ProductName': product_name_input,
                'Manufacturer': manufacturer_input,
            }
            
            preferred_sites = ['コスモバイオ', 'フナコシ', 'AXEL', 'Selleck', 'MCE', 'Nakarai', 'FUJIFILM', '関東化学', 'TCI', 'Merck', '和光純薬']
            pages_list = orchestrator_agent(product_info, gemini_api_key, brightdata_api_key, preferred_sites, debug_mode_checkbox)
            
            final_results = []
            input_date = pd.Timestamp.now().strftime('%Y-%m-%d')

            if pages_list:
                for page_data in pages_list:
                    for offer_item in page_data.get('offers', []):
                        try:
                            price = int(float(offer_item.get('price', 0)))
                        except (ValueError, TypeError):
                            price = 0
                        
                        final_results.append({
                            '入力日': input_date,
                            '製品名': page_data.get('productName', 'N/A'),
                            '型番/製品番号': page_data.get('modelNumber', 'N/A'),
                            '仕様': offer_item.get('size', 'N/A'),
                            'メーカー': page_data.get('manufacturer', 'N/A'),
                            'リスト単価': price,
                            '在庫': 'あり' if offer_item.get('inStock') else 'なし/不明',
                            '情報元URL': page_data.get('sourceUrl', 'N/A')
                        })
            
            if not final_results:
                st.warning("検索結果から有効な製品情報が見つかりませんでした。")
                query_for_url = f"{product_info.get('Manufacturer', '')} {product_info.get('ProductName', '')}"
                final_results.append({
                    '入力日': input_date, '製品名': product_info['ProductName'], '型番/製品番号': 'N/A',
                    '仕様': 'N/A', 'メーカー': product_info['Manufacturer'], 'リスト単価': 0, 
                    '在庫': 'なし/不明', '情報元URL': f"https://www.google.com/search?q={urllib.parse.quote(query_for_url)}"
                })
            
            st.success("全製品の情報収集が完了しました。")

            df_results = pd.DataFrame(final_results)
            
            if max_price_input > 0:
                df_results = df_results[df_results['リスト単価'] <= max_price_input]
            if min_price_input > 0:
                df_results = df_results[df_results['リスト単価'] >= min_price_input]

            st.subheader("検索結果")
            st.dataframe(
                df_results,
                column_config={
                    "リスト単価": st.column_config.NumberColumn(format="¥%d"),
                    "情報元URL": st.column_config.LinkColumn("Link", display_text="開く")
                },
                use_container_width=True,
                hide_index=True
            )

            @st.cache_data
            def convert_df_to_csv(df: pd.DataFrame) -> bytes:
                return df.to_csv(index=False).encode('utf-8-sig')

            csv = convert_df_to_csv(df_results)
            st.download_button(
                label="結果をCSVでダウンロード",
                data=csv,
                file_name=f"purchase_list_{pd.Timestamp.now().strftime('%Y%m%d')}.csv",
                mime='text/csv',
            )
