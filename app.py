# -*- coding: utf-8 -*-
"""
製品調達AIエージェント Streamlitアプリケーション
（プロキシ接続によるStreamlit Cloud制約回避・最終確定版）
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
import urllib3
import random  # 修正: ランダム遅延用追加

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
# === Bright Data API 連携関数 ===
# ==============================================================================

def get_page_content_with_brightdata(url: str, brd_username: str, brd_password: str) -> dict:
    """
    [最終修正] Streamlit Cloudの制約を回避するため、プロキシ接続に再度変更。
    認証情報バグを修正した最終版。
    """
    BRD_HOST = 'brd.superproxy.io'
    BRD_PORT = 9515
    session_id = f'session_{int(time.time())}'  # 修正: IPローテーション用
    proxy_url = f'http://{brd_username}-{session_id}:{brd_password}@{BRD_HOST}:{BRD_PORT}'
    proxies = {'http': proxy_url, 'https': proxy_url}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',  # 修正: 最新UA
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
        'Sec-Fetch-Site': 'none',  # 追加: 検知回避ヘッダー
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Dest': 'document',
        'Sec-CH-UA': '"Not_A Brand";v="8", "Chromium";v="130", "Google Chrome";v="130"',
        'Sec-CH-UA-Mobile': '?0',
        'Sec-CH-UA-Platform': '"Windows"'
    }
    result = {"url": url, "status_code": None, "content": None, "error": None}
    
    for attempt in range(2): # 念のため2回リトライ
        try:
            response = requests.get(url, headers=headers, proxies=proxies, verify=False, timeout=60)
            result["status_code"] = response.status_code
            response.raise_for_status()
            result["content"] = response.text
            result["error"] = None
            return result
        except requests.exceptions.RequestException as e:
            result["error"] = str(e)
            if hasattr(e, 'response') and e.response:
                 result["content"] = e.response.text[:1000] if e.response.text else ""
            time.sleep(2)  # 修正: リトライ遅延強化
    
    return result


def search_product_urls_with_brightdata(query: str, api_key: str) -> list:
    """Bright DataのSERP APIでGoogle検索を実行し、URLリストを取得する。"""
    # (この部分は変更なし)
    st.info(f"【Bright Data】クエリ「{query}」で検索リクエストを送信...")
    headers = {  # 修正: ヘッダー強化
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
    }
    google_search_url = f"https://www.google.co.jp/search?q={urllib.parse.quote(query)}&hl=ja&gl=jp&ceid=JP:ja"
    payload = {  # 修正: JSレンダリング追加
        'zone': 'serp_api1',
        'url': google_search_url,
        'render': 'js'
    }
    time.sleep(random.uniform(1, 3))  # 修正: 初回遅延
    try:
        initial_response = requests.post('https://api.brightdata.com/serp/req', headers=headers, json=payload, timeout=30)
        initial_response.raise_for_status()
        response_id = initial_response.headers.get('x-response-id')
        if not response_id: return []
        result_url = f'https://api.brightdata.com/serp/get_result?response_id={response_id}'
        for _ in range(15):
            time.sleep(random.uniform(2, 5))  # 修正: ランダム遅延
            try:
                result_response = requests.get(result_url, headers=headers, timeout=30)  # 修正: ヘッダー適用
                if result_response.status_code == 200:
                    if not result_response.text: return []
                    soup = BeautifulSoup(result_response.text, 'html.parser')
                    # 修正: セレクタ強化（Google 2025構造対応）
                    result_divs = soup.find_all('div', {'data-ved': True}) or soup.find_all('div', class_='g')
                    urls = []
                    for div in result_divs:
                        a_tag = div.find('a', href=True)
                        if a_tag and a_tag.get('href') and a_tag.get('href').startswith('http') and not a_tag.get('href').startswith('https://www.google.'):
                            urls.append(a_tag.get('href'))
                    unique_urls = list(dict.fromkeys(urls))[:10]  # 修正: 上位10件制限
                    st.success(f"【Bright Data】「{query}」から{len(unique_urls)}件のURLを抽出しました。")
                    return unique_urls
                elif result_response.status_code != 202: return []
            except requests.exceptions.RequestException: return []
        return []
    except requests.exceptions.RequestException: return []

# (以降のAIエージェント関連関数、統括エージェント、Streamlit UI部分は変更ありません)
# ==============================================================================
# === AIエージェント関連関数 ===
# ==============================================================================
def analyze_page_and_extract_info(page_content_result: dict, product_name: str, gemini_api_key: str) -> dict | None:
    """HTMLをGemini APIに渡し、製品情報を抽出する。"""
    html_content = page_content_result.get("content")
    if page_content_result.get("error") or not html_content:
        return None

    soup = BeautifulSoup(html_content, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'form']):
        tag.decompose()
    body_text = soup.body.get_text(separator=' ', strip=True) if soup.body else ''
    
    if not body_text: return None
    if len(body_text) > 18000: body_text = body_text[:18000]

    prompt = f"""
    You are an Analyst Agent. Your task is to analyze the following text content from a product webpage and extract key information about the specified product.
    **Product to find:** "{product_name}"
    **Webpage Content:**
    ---
    {body_text}
    ---
    **Instructions:**
    1. First, identify the main product details like `productName`, `modelNumber`, and `manufacturer`. 2. Next, find all available purchasing options (sizes, packages, capacities, etc.) for this product. 3. For each option, extract its size/specification, price, and stock status. 4. Compile this information into a list of objects under the `offers` key. Each object in the list should represent one purchase option. 5. **CRITICAL RULE for `price`:** The price MUST be in Japanese Yen. Look for numbers clearly labeled with Japanese price words (「価格」, 「値段」, 「販売価格」, 「定価」) or symbols (「￥」, 「円」). If a price is in a foreign currency (like $, €, USD, EUR), you MUST ignore it and set the price to 0. If no Japanese Yen price is found, use 0. "税込価格" や "送料無料" を考慮し、合計価格を優先。 6. For `inStock` in each offer, determine the stock status. `true` if words like "在庫あり", "カートに入れる", "購入可能", "in stock" are present. `false` if "在庫なし", "入荷待ち", "out of stock" are found. 7. If no specific options are listed and there is only a single price for the product, create a single entry in the `offers` list. Use "N/A" for the `size` if it's not specified. 8. If you cannot find any relevant product information, return an empty list for the `offers` key. 9. Your response MUST be a single, valid JSON object.
    **JSON Output Structure:**
    {{ "productName": "string", "modelNumber": "string", "manufacturer": "string", "offers": [ {{ "size": "string", "price": number, "inStock": boolean }} ] }}
    """
    try:
        payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}}
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={gemini_api_key}"
        response = requests.post(api_url, headers={'Content-Type': 'application/json'}, json=payload, timeout=45)
        response.raise_for_status()
        result = response.json()
        if not result.get('candidates'): return None
        response_text = result['candidates'][0]['content']['parts'][0]['text']
        raw_data = json.loads(response_text)
        return raw_data if isinstance(raw_data, dict) else None
    except (json.JSONDecodeError, requests.exceptions.RequestException):
        return None

# ==============================================================================
# === 統括エージェント ===
# ==============================================================================
def orchestrator_agent(product_info: dict, gemini_api_key: str, brightdata_api_key: str, brd_username: str, brd_password: str, preferred_sites: list, debug_mode: bool = False) -> tuple[list, list]:
    """一連の処理を統括するエージェント。"""
    product_name = product_info['ProductName']
    manufacturer = product_info.get('Manufacturer', '')
    st.subheader(f"【統括エージェント】 \"{product_name}\" の情報収集を開始します。")

    base_query = f"{manufacturer} {product_name}"
    site_map = { 'コスモバイオ': 'cosmobio.co.jp', 'フナコシ': 'funakoshi.co.jp', 'AXEL': 'axel.as-1.co.jp', 'Selleck': 'selleck.co.jp', 'MCE': 'medchemexpress.com', 'Nakarai': 'nacalai.co.jp', 'FUJIFILM': 'labchem-wako.fujifilm.com', '関東化学': 'kanto.co.jp', 'TCI': 'tcichemicals.com', 'Merck': 'merck.com', '和光純薬': 'hpc-j.co.jp' }
    search_queries = [f"site:{site_map[site_name]} {base_query}" for site_name in preferred_sites if site_name in site_map]
    search_queries.append(base_query)

    all_urls = []
    for query in search_queries:
        urls = search_product_urls_with_brightdata(query, brightdata_api_key)
        all_urls.extend(urls)
        if urls and debug_mode:  # 修正: デバッグ用サンプル表示
            st.info(f"抽出URLサンプル: {urls[:3]}")
    
    unique_urls = list(dict.fromkeys(all_urls))
    if not unique_urls: return [], []
    
    st.info(f"{len(unique_urls)}件のHTMLページを並列で取得・分析します...")
    my_bar = st.progress(0, text="Webページを取得中...")
    all_page_content_results, found_pages_data = [], []

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:  # 修正: workers減らし検知回避
        future_to_url = {executor.submit(get_page_content_with_brightdata, url, brd_username, brd_password): url for url in unique_urls}
        for i, future in enumerate(concurrent.futures.as_completed(future_to_url)):
            all_page_content_results.append(future.result())
            my_bar.progress((i + 1) / len(unique_urls), text=f"Webページを取得中... ({i + 1}/{len(unique_urls)})")

        # 追加: デバッグ用content長さログ（常に表示、短い場合警告）
        short_contents = 0
        for res in all_page_content_results:
            if res.get("content"):
                content_len = len(res["content"])
                if debug_mode:
                    st.info(f"URL: {res['url']}, Content長: {content_len}文字")
                if content_len < 1000:  # 閾値: 短すぎる場合
                    short_contents += 1
                    st.warning(f"短いコンテンツ検知: {res['url']} ({content_len}文字) - ブロックの可能性")
        if short_contents > 0:
            st.warning(f"合計{short_contents}件の短いページを検知。JSレンダリング不足かブロック？")

        successful_contents = [res for res in all_page_content_results if res.get("content") and not res.get("error") and len(res.get("content", "")) > 1000]  # 追加: 長さフィルタ
        if successful_contents:
            my_bar.progress(0, text="AIでページを分析中...")
            future_to_content = {executor.submit(analyze_page_and_extract_info, content_res, product_name, gemini_api_key): content_res for content_res in successful_contents}
            for i, future in enumerate(concurrent.futures.as_completed(future_to_content)):
                page_details = future.result()
                if page_details and page_details.get("offers"):
                    page_details['sourceUrl'] = future_to_content[future].get("url")
                    found_pages_data.append(page_details)
                my_bar.progress((i + 1) / len(successful_contents), text=f"AIでページを分析中... ({i + 1}/{len(successful_contents)})")

    st.success(f"【統括エージェント】{len(found_pages_data)}ページから製品情報を抽出しました。")
    return found_pages_data, all_page_content_results

# ==============================================================================
# === Streamlit UI アプリケーション部分 ===
# ==============================================================================
st.set_page_config(layout="wide")
st.title("製品調達AIエージェント")

st.sidebar.header("APIキー設定")
try:
    gemini_api_key = st.secrets["GOOGLE_API_KEY"]
    brightdata_api_key = st.secrets["BRIGHTDATA_API_KEY"]
    brightdata_username = st.secrets["BRIGHTDATA_USERNAME"]
    brightdata_password = st.secrets["BRIGHTDATA_PASSWORD"]
    st.sidebar.success("APIキーと認証情報が設定されています。")
except KeyError:
    st.sidebar.error("Streamlit Secretsに必要な情報が設定されていません。")
    gemini_api_key, brightdata_api_key, brightdata_username, brightdata_password = "", "", "", ""

st.sidebar.header("検索条件")
product_name_input = st.sidebar.text_input("製品名 (必須)", placeholder="例: Y27632")
manufacturer_input = st.sidebar.text_input("メーカー", placeholder="例: Selleck")
min_price_input = st.sidebar.number_input("最低価格 (円)", min_value=0, value=0, step=100)
max_price_input = st.sidebar.number_input("最高価格 (円)", min_value=0, value=0, step=100)
debug_mode_checkbox = st.sidebar.checkbox("デバッグモードを有効にする (詳細ログ表示)")
search_button = st.sidebar.button("検索開始", type="primary")

if search_button:
    if not all([gemini_api_key, brightdata_api_key, brightdata_username, brightdata_password]):
        st.error("APIキーまたは認証情報が設定されていません。")
    elif not product_name_input:
        st.error("製品名を入力してください。")
    else:
        with st.spinner('AIエージェントが情報収集中...'):
            product_info = {'ProductName': product_name_input, 'Manufacturer': manufacturer_input}
            preferred_sites = ['コスモバイオ', 'フナコシ', 'AXEL', 'Selleck', 'MCE', 'Nakarai', 'FUJIFILM', '関東化学', 'TCI', 'Merck', '和光純薬']
            
            pages_list, log_data = orchestrator_agent(product_info, gemini_api_key, brightdata_api_key, brightdata_username, brightdata_password, preferred_sites, debug_mode=debug_mode_checkbox)
            
            final_results = []
            input_date = pd.Timestamp.now().strftime('%Y-%m-%d')
            if pages_list:
                for page_data in pages_list:
                    for offer_item in page_data.get('offers', []):
                        try: price = int(float(offer_item.get('price', 0)))
                        except (ValueError, TypeError): price = 0
                        final_results.append({ '入力日': input_date, '製品名': page_data.get('productName', 'N/A'), '型番/製品番号': page_data.get('modelNumber', 'N/A'), '仕様': offer_item.get('size', 'N/A'), 'メーカー': page_data.get('manufacturer', 'N/A'), 'リスト単価': price, '在庫': 'あり' if offer_item.get('inStock') else 'なし/不明', '情報元URL': page_data.get('sourceUrl', 'N/A') })
            
            if not final_results:
                st.warning("検索結果から有効な製品情報が見つかりませんでした。")
                search_term = f"{product_info.get('Manufacturer', '')} {product_info['ProductName']}"
                query_url = f"https://www.google.com/search?q={urllib.parse.quote(search_term)}"
                final_results.append({ '入力日': input_date, '製品名': product_info['ProductName'], '型番/製品番号': 'N/A', '仕様': 'N/A', 'メーカー': product_info.get('Manufacturer', ''), 'リスト単価': 0, '在庫': 'なし/不明', '情報元URL': query_url })
            
            st.success("全製品の情報収集が完了しました。")

            df_results = pd.DataFrame(final_results)
            if max_price_input > 0: df_results = df_results[df_results['リスト単価'] <= max_price_input]
            if min_price_input > 0: df_results = df_results[df_results['リスト単価'] >= min_price_input]

            st.subheader("検索結果")
            st.dataframe( df_results, column_config={ "リスト単価": st.column_config.NumberColumn(format="¥%d"), "情報元URL": st.column_config.LinkColumn("Link", display_text="開く") }, use_container_width=True, hide_index=True )
            
            @st.cache_data
            def convert_df_to_csv(df: pd.DataFrame) -> bytes: return df.to_csv(index=False).encode('utf-8-sig')
            csv = convert_df_to_csv(df_results)
            st.download_button( label="結果をCSVでダウンロード", data=csv, file_name=f"purchase_list_{pd.Timestamp.now().strftime('%Y%m%d')}.csv", mime='text/csv' )

        if debug_mode_checkbox and log_data:
            st.subheader("詳細デバッグログ")
            for log in log_data:
                status, is_error = log.get('status_code'), log.get('error') is not None
                
                if is_error: st.error(f"接続エラー: {log['url']}")
                elif status != 200 and status is not None:
                    st.warning(f"ステータスコード異常 ({status}): {log['url']}")
                else:
                    st.success(f"取得成功 ({status}): {log['url']}")

                with st.expander("詳細を表示"):
                    if is_error: st.write(f"**エラー内容:** `{log['error']}`")
                    log_display = log.copy()
                    if log_display.get('content'):
                        content_len = len(log_display['content'])
                        log_display['content'] = (log_display['content'][:1000] + "...") if len(log_display['content']) > 1000 else log_display['content']
                        # 追加: contentサマリー表示
                        st.write(f"**Content長:** {content_len}文字")
                        if content_len < 1000:
                            st.warning("このページのコンテンツが短すぎます（ブロック疑い）。")
                    st.json(log_display)