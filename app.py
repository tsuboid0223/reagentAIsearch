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
import urllib3
import random

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
# === Bright Data API 連携関数 ===
# ==============================================================================

def get_page_content_with_brightdata(url: str, brd_username: str, brd_password: str) -> dict:
    """
    Scraping Browserで生bodyテキスト抽出（ハング回避 + リアルタイムログ）。
    """
    BRD_HOST = 'brd.superproxy.io'
    BRD_PORT = 24000  # HTTP Browserポート
    proxy_url = f'http://{brd_username}:{brd_password}@{BRD_HOST}:{BRD_PORT}'
    proxies = {'http': proxy_url, 'https': proxy_url}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
    }
    result = {"url": url, "status_code": None, "content": None, "error": None}
    
    # リアルタイムログ表示
    st.write(f"  - 接続試行中: {url[:50]}...")
    
    # 1. Scraping Browser試行 (POST)
    payload = {'url': url, 'renderJS': True, 'waitFor': 5000, 'proxy': 'residential'}
    try:
        st.write(f"    - POST接続中...")
        response = requests.post(proxy_url, json=payload, headers=headers, proxies=proxies, verify=False, timeout=20)
        response.raise_for_status()
        st.write(f"    - POST成功 (status: {response.status_code})")
        data = response.json()
        html = data.get('content', response.text)
    except Exception as e:
        st.write(f"    - POST失敗: {str(e)[:50]}...")
        # 2. フォールバック: シンプルプロキシGET
        full_url = f'{proxy_url}/{url}'
        try:
            st.write(f"    - GETフォールバック中...")
            response = requests.get(full_url, headers=headers, proxies=proxies, verify=False, timeout=20)
            response.raise_for_status()
            st.write(f"    - GET成功 (status: {response.status_code})")
            html = response.text
        except Exception as e2:
            st.write(f"    - GET失敗: {str(e2)[:50]}...")
            result["error"] = str(e) + "; fallback: " + str(e2)
            return result
    
    # テキスト抽出
    st.write(f"    - テキスト抽出中...")
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'iframe']):
        tag.decompose()
    result["content"] = soup.body.get_text(separator=' ', strip=True) if soup.body else ''
    result["content"] = result["content"][:18000]
    result["status_code"] = 200
    st.write(f"    - 抽出完了 (長さ: {len(result['content'])}文字)")
    return result


def search_product_urls_with_brightdata(query: str, api_key: str) -> list:
    """Bright DataのSERP APIでGoogle検索を実行し、URLリストを取得する。"""
    st.info(f"【Bright Data】クエリ「{query}」で検索リクエストを送信...")
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
    }
    google_search_url = f"https://www.google.co.jp/search?q={urllib.parse.quote(query)}&hl=ja&gl=jp&ceid=JP:ja"
    payload = {
        'zone': 'serp_api1',
        'url': google_search_url,
        'render': 'js'
    }
    time.sleep(random.uniform(1, 3))
    try:
        initial_response = requests.post('https://api.brightdata.com/serp/req', headers=headers, json=payload, timeout=30)
        initial_response.raise_for_status()
        response_id = initial_response.headers.get('x-response-id')
        if not response_id: return []
        result_url = f'https://api.brightdata.com/serp/get_result?response_id={response_id}'
        for _ in range(15):
            time.sleep(random.uniform(2, 5))
            try:
                result_response = requests.get(result_url, headers=headers, timeout=30)
                if result_response.status_code == 200:
                    if not result_response.text: return []
                    soup = BeautifulSoup(result_response.text, 'html.parser')
                    result_divs = soup.find_all('div', {'data-ved': True}) or soup.find_all('div', class_='g')
                    urls = []
                    for div in result_divs:
                        a_tag = div.find('a', href=True)
                        if a_tag and a_tag.get('href') and a_tag.get('href').startswith('http') and not a_tag.get('href').startswith('https://www.google.'):
                            urls.append(a_tag.get('href'))
                    unique_urls = list(dict.fromkeys(urls))[:10]
                    st.success(f"【Bright Data】「{query}」から{len(unique_urls)}件のURLを抽出しました。")
                    return unique_urls
                elif result_response.status_code != 202: return []
            except requests.exceptions.RequestException: return []
        return []
    except requests.exceptions.RequestException: return []

# ==============================================================================
# === AIエージェント関連関数 ===
# ==============================================================================
def analyze_page_and_extract_info(page_content_result: dict, product_name: str, gemini_api_key: str) -> dict | None:
    """HTMLをGemini APIに渡し、製品情報を抽出する。"""
    body_text = page_content_result.get("content")
    if page_content_result.get("error") or not body_text:
        return None

    prompt = f"""
    You are an Analyst Agent. Parse this webpage text for "{product_name}" from cosmobio EC site.
    **Text (ignore garbled chars like �� for ¥):**
    {body_text}
    **Instructions:**
    1. productName: Main title (e.g., Y-27632 dihydrochloride).
    2. modelNumber: Code from table (e.g., ALX-270-333-M001).
    3. manufacturer: Supplier (e.g., ENZ).
    4. offers: From "規格 コード 容量 価格" table, extract rows:
       - size: Capacity (e.g., "1 MG").
       - price: ¥ number only (treat garbled �� as ¥, e.g., ��34,000 → 34000).
       - inStock: true if "在庫あり" or cart link; false otherwise.
    - Example row: ENZ ALX-270-333-M001 1 MG ¥34,000 → {{ "size": "1 MG", "price": 34000, "inStock": true }}
    - Create 1 entry per row (3 rows expected).
    Output single JSON: {{ "productName": "string", "modelNumber": "string", "manufacturer": "string", "offers": [ {{ "size": "string", "price": number, "inStock": boolean }} ] }}
    If no table, empty offers.
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

    # 進捗バー初期化 (0%)
    progress_bar = st.progress(0)
    status_text = st.empty()

    # ステップ1: URL抽出 (0-20%)
    status_text.text("URL抽出中...")
    progress_bar.progress(0.1)
    all_urls = []
    num_queries = len(search_queries)
    for i, query in enumerate(search_queries):
        urls = search_product_urls_with_brightdata(query, brightdata_api_key)
        all_urls.extend(urls)
        if urls and debug_mode:
            st.info(f"抽出URLサンプル: {urls[:3]}")
        # 進捗更新
        progress = 0.1 + (i / num_queries) * 0.1
        progress_bar.progress(progress)
        status_text.text(f"URL抽出中... ({i+1}/{num_queries})")
    
    unique_urls = list(dict.fromkeys(all_urls))
    if not unique_urls: return [], []
    
    st.info(f"{len(unique_urls)}件のHTMLページを並列で取得・分析します...")
    progress_bar.progress(0.2)
    status_text.text("URL抽出完了 (20%)")

    # ステップ2: ページ取得 (20-80%) - 非並列ループでハング回避
    status_text.text("Webページを取得中...")
    progress_bar.progress(0.2)
    all_page_content_results = []

    for i, url in enumerate(unique_urls):
        status_text.text(f"Webページを取得中... ({i + 1}/{len(unique_urls)}): {url[:50]}...")
        page_result = get_page_content_with_brightdata(url, brd_username, brd_password)
        all_page_content_results.append(page_result)
        if page_result.get('error'):
            st.warning(f"取得失敗: {url} - スキップして次へ")
        # 進捗更新
        progress = 0.2 + (i + 1) / len(unique_urls) * 0.6
        progress_bar.progress(progress)

    # デバッグ用content長さログ
    short_contents = 0
    for res in all_page_content_results:
        if res.get("content"):
            content_len = len(res["content"])
            if debug_mode:
                st.info(f"URL: {res['url']}, Content長: {content_len}文字")
            if content_len < 1000:
                short_contents += 1
                st.warning(f"短いコンテンツ検知: {res['url']} ({content_len}文字) - ブロックの可能性")
    if short_contents > 0:
        st.warning(f"合計{short_contents}件の短いページを検知。JSレンダリング不足かブロック？")

    progress_bar.progress(0.8)
    status_text.text("ページ取得完了 (80%)")

    # ステップ3: AI解析 (80-100%)
    status_text.text("AIでページを分析中...")
    found_pages_data = []
    successful_contents = [res for res in all_page_content_results if res.get("content") and len(res.get("content", "")) > 1000 and not res.get("error")]
    if successful_contents:
        for i, content_res in enumerate(successful_contents):
            status_text.text(f"AIでページを分析中... ({i + 1}/{len(successful_contents)})")
            page_details = analyze_page_and_extract