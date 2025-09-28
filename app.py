# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup
import json
from tqdm import tqdm
import concurrent.futures

# ==============================================================================
# === 修正箇所 1: Bright Dataを使った、より強力なHTML取得関数を新設 ===
# ==============================================================================
def get_page_content_with_brightdata(url, api_key):
    """
    requests.getの代わりにBright DataのScraping Browserを使って
    JavaScriptレンダリング後のHTMLを取得する。
    これにより、ボット対策を回避しやすくなる。
    """
    headers = {'Authorization': f'Bearer {api_key}'}
    payload = {'url': url, 'country': 'jp'} # 日本からのアクセスをエミュレート

    try:
        # Bright DataのScraping Browser APIエンドポイントを呼び出す
        # これは一般的な例であり、契約によってはプロキシ形式(例: brd.superproxy.io)を使う場合があります
        response = requests.post('https://api.brightdata.com/scraping/browser/request', headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        
        # レスポンスIDを取得して結果をポーリング
        response_id = response.headers.get('x-response-id')
        if not response_id:
            st.warning(f"【Bright Data Scraper】URL '{url}' のResponse IDが取得できませんでした。")
            return None, "Response ID not found"

        result_url = f'https://api.brightdata.com/scraping/browser/response?response_id={response_id}'
        
        for _ in range(10): # タイムアウトまでポーリング
            time.sleep(3)
            result_response = requests.get(result_url, headers=headers, timeout=30)
            if result_response.status_code == 200:
                return result_response.text, None # 成功：HTMLコンテンツを返す
            if result_response.status_code != 202: # 202は「処理中」
                return None, f"Unexpected status code: {result_response.status_code}"
        
        return None, "Polling timed out"

    except requests.exceptions.RequestException as e:
        return None, f"Request failed: {e}"


# ==============================================================================
# === 修正箇所 2: 元の分析関数を修正し、新しいHTML取得関数とデバッグ機能を追加 ===
# ==============================================================================
def analyze_page_and_extract_info(url, product_name, gemini_api_key, brightdata_api_key, debug_mode=False): # 引数を追加
    # 変更点: requests.getを新しい関数に置き換え
    html_content, error = get_page_content_with_brightdata(url, brightdata_api_key)

    if error or not html_content:
        st.warning(f"URL {url} のコンテンツ取得に失敗しました: {error}")
        return None

    # --- ここからデバッグ機能 ---
    if debug_mode:
        with st.expander(f"【デバッグ情報】URL: {url}"):
            st.subheader("取得した生HTML (最初の1000文字)")
            st.code(html_content[:1000], language='html')
    # --- ここまでデバッグ機能 ---

    soup = BeautifulSoup(html_content, 'html.parser')
    for s in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'form']): # formも除去対象に追加
        s.decompose()
    body_text = soup.body.get_text(separator=' ', strip=True) if soup.body else ''

    if not body_text:
        st.info(f"URL {url} からテキストを抽出できませんでした。ページが空か、構造が特殊である可能性があります。")
        return None

    if len(body_text) > 18000: body_text = body_text[:18000]

    # --- ここからデバッグ機能 ---
    if debug_mode:
        with st.expander(f"【デバッグ情報】URL: {url}"): # 同じexpanderに追記
            st.subheader("クリーンアップ後、AIに渡すテキスト (最初の1000文字)")
            st.text(body_text[:1000])
    # --- ここまでデバッグ機能 ---

    prompt = f"""
    You are an Analyst Agent. Your task is to analyze the following text content from a product webpage and extract key information about the specified product.
    **Product to find:** "{product_name}"
    **Webpage Content:**
    ---
    {body_text}
    ---
    **Instructions:**
    1. Analyze the text content to find the specific product.
    2. Extract the following details. If a piece of information is not available, use "N/A".
    3. **CRITICAL RULE for `price`:** The price MUST be in Japanese Yen. Look for numbers clearly labeled with Japanese price words (「価格」, 「値段」, 「販売価格」, 「定価」) or symbols (「￥」, 「円」). If a price is in a foreign currency (like $, €, USD, EUR), you MUST ignore it and set the price to 0. If no Japanese Yen price is found, use 0.
    4. For `inStock`, determine the stock status. `true` if words like "在庫あり", "カートに入れる", "購入可能", "in stock" are present. `false` if "在庫なし", "入荷待ち", "out of stock" are found.
    5. Your response MUST be a single, valid JSON object.
    **JSON Output Structure:**
    {{
      "productName": "string", "modelNumber": "string", "manufacturer": "string", "price": number, "inStock": boolean
    }}
    """
    try:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}
        }
        apiUrl = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={gemini_api_key}"
        response = requests.post(apiUrl, headers={'Content-Type': 'application/json'}, json=payload, timeout=45) # タイムアウトを延長
        response.raise_for_status()
        result = response.json()
        if not result.get('candidates'):
            st.warning(f"Gemini APIから候補が返されませんでした。URL: {url}")
            return None
        response_text = result['candidates'][0]['content']['parts'][0]['text']
        raw_data = json.loads(response_text)
        return raw_data if isinstance(raw_data, dict) else None
    except json.JSONDecodeError as e:
        st.error(f"Gemini APIからのレスポンスがJSON形式ではありませんでした。URL: {url}, エラー: {e}, レスポンス: {response_text[:200]}...")
        return None
    except Exception as e:
        st.error(f"Gemini API呼び出しエラー: {e}。URL: {url}")
        return None

# ==============================================================================
# === 修正箇所 3: orchestrator_agentから新しい引数を渡す ===
# ==============================================================================
def orchestrator_agent(product_info, gemini_api_key, brightdata_api_key, preferred_sites=[], debug_mode=False):
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
    search_queries = []
    if preferred_sites:
        for site_name in preferred_sites:
            if site_name in site_map:
                search_queries.append(f"site:{site_map[site_name]} {base_query}")
    search_queries.append(base_query)
    all_urls = []
    for query in search_queries:
        all_urls.extend(search_product_urls_with_brightdata(query, brightdata_api_key, debug_mode))
    unique_urls = list(dict.fromkeys(all_urls))
    html_urls = [url for url in unique_urls if not url.lower().endswith(('.pdf', '.xls', 'xlsx', '.doc', '.docx'))]
    if not html_urls:
        st.warning("関連URLが見つかりませんでした。検索クエリやBright Dataの設定を確認してください。")
        return []
    
    found_offers = []
    st.info(f"{len(html_urls)}件のHTMLページを並列で分析します...")
    
    progress_text = "Webページを分析中..."
    my_bar = st.progress(0, text=progress_text)
    processed_count = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        # 変更点: analyze_page_and_extract_info に必要な引数 (brightdata_api_key, debug_mode) を渡す
        future_to_url = {
            executor.submit(analyze_page_and_extract_info, url, product_name, gemini_api_key, brightdata_api_key, debug_mode): url 
            for url in html_urls
        }
        for future in concurrent.futures.as_completed(future_to_url):
            url = future_to_url[future]
            try:
                offer_details = future.result()
                if offer_details and offer_details.get("productName", "N/A") != "N/A":
                    offer_details['sourceUrl'] = url
                    found_offers.append(offer_details)
                else:
                    st.info(f"URL {url} から有効な製品情報が抽出できませんでした。")
            except Exception as exc:
                st.error(f"URL {url} の処理中にエラーが発生しました: {exc}")
            
            processed_count += 1
            my_bar.progress(processed_count / len(html_urls), text=f"{progress_text} ({processed_count}/{len(html_urls)} ページ処理済み)")
            
    st.success(f"【統括エージェント】合計{len(found_offers)}件の製品情報を抽出しました。")
    return found_offers


# 注: これより下のStreamlit UI部分のコードは変更不要です。
# ただし、`orchestrator_agent`を呼び出す際に`debug_mode_checkbox`を渡している部分は
# すでに正しく実装されているため、そのままで問題ありません。
