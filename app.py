# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup
import json
import re
import concurrent.futures

# === AIエージェントと関連関数の定義 ===

def translate_text_with_gemini(text_to_translate, gemini_api_key):
    try:
        prompt = f"Translate the following Japanese text to simple English for a product search query. Output only the translated text, nothing else.\n\nJapanese: '{text_to_translate}'\nEnglish:"
        payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        apiUrl = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={gemini_api_key}"
        response = requests.post(apiUrl, headers={'Content-Type': 'application/json'}, json=payload)
        response.raise_for_status()
        result = response.json()
        translated_text = result['candidates'][0]['content']['parts'][0]['text']
        st.info(f"「{text_to_translate}」を「{translated_text.strip()}」に翻訳しました。")
        return translated_text.strip()
    except Exception as e:
        st.warning(f"翻訳中にエラーが発生しました: {e}")
        return None

def search_product_urls_with_brightdata(query, api_key):
    """Bright Data APIを使い、Google検索結果のURLリストを取得する。"""
    st.info(f"【Bright Data】クエリ「{query}」で検索リクエストを送信...")
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    
    # ★★ ここを修正: お客様の環境で唯一成功した「URLを直接渡す方式」に戻します ★★
    GoogleSearch_url = f"https://www.google.co.jp/search?q={urllib.parse.quote(query)}&hl=ja"
    payload = {'zone': 'serp_api1', 'url': GoogleSearch_url}

    try:
        initial_response = requests.post('https://api.brightdata.com/serp/req', headers=headers, json=payload, timeout=30)
        initial_response.raise_for_status()
        response_id = initial_response.headers.get('x-response-id')
        if not response_id:
            st.error("エラー: APIからのresponse_idが取得できませんでした。")
            return []
        
        st.info(f"【Bright Data】リクエスト受付完了。結果を待機します...")
        result_url = f'https://api.brightdata.com/serp/get_result?response_id={response_id}'
        for _ in range(15):
            time.sleep(3)
            result_response = requests.get(result_url, headers={'Authorization': f'Bearer {api_key}'}, timeout=30)
            if result_response.status_code == 200:
                # 応答がHTML形式であることを前提に、BeautifulSoupで解析します
                html_content = result_response.text
                if not html_content:
                    st.warning("結果取得完了、しかしレスポンスが空でした。")
                    return []
                soup = BeautifulSoup(html_content, 'html.parser')
                urls = []
                # Google検索結果の<a>タグを抽出
                for a_tag in soup.find_all('a', href=True):
                    href = a_tag.get('href')
                    if href and href.startswith('/url?q='):
                        # /url?q= から始まるURLを整形
                        actual_url = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get('q')
                        if actual_url:
                            urls.append(actual_url[0])
                    elif href and href.startswith('http') and not href.startswith('https://www.google.com'):
                         urls.append(href)
                
                if urls:
                    unique_urls = list(dict.fromkeys(urls))
                    st.success(f"【Bright Data】合計{len(unique_urls)}件のURLをHTMLから抽出しました。")
                    return unique_urls
                else:
                    st.warning("検索結果のHTMLからURLを抽出できませんでした。")
                    return []
            elif result_response.status_code != 202:
                st.error(f"結果取得エラー: {result_response.status_code}")
                return []
        st.warning("結果取得がタイムアウトしました。")
        return []

    except requests.exceptions.RequestException as e:
        st.error(f"Bright Data API呼び出しエラー: {e}")
        if e.response:
            st.error(f"レスポンス: {e.response.text}")
        return []

def analyze_page_and_extract_info(url, product_name, gemini_api_key):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        html_content = response.text
    except requests.exceptions.RequestException:
        return None
    soup = BeautifulSoup(html_content, 'html.parser')
    for s in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
        s.decompose()
    body_text = soup.body.get_text(separator=' ', strip=True) if soup.body else ''
    if len(body_text) > 18000: body_text = body_text[:18000]
    prompt = f"""
    You are an Analyst Agent. Your task is to analyze the following text content from a product webpage and extract key information about the specified product.
    **Product to find:** "{product_name}"
    **Webpage Content:** --- {body_text} ---
    **Instructions:**
    1. Analyze the text content to find the specific product.
    2. Extract the following details. If a piece of information is not available, use "N/A".
    3. **CRITICAL RULE for `price`:** The price MUST be in Japanese Yen. Look for numbers clearly labeled with Japanese price words (「価格」, 「値段」, 「販売価格」, 「定価」) or symbols (「￥」, 「円」). If a price is in a foreign currency (like $, €, USD, EUR), you MUST ignore it and set the price to 0. If no Japanese Yen price is found, use 0.
    4. For `inStock`, determine the stock status. `true` if words like "在庫あり", "カートに入れる", "購入可能", "in stock" are present. `false` if "在庫なし", "入荷待ち", "out of stock" are found.
    5. Your response MUST be a single, valid JSON object.
    **JSON Output Structure:**
    {{ "productName": "string", "modelNumber": "string", "manufacturer": "string", "price": number, "inStock": boolean }}
    """
    try:
        payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],"generationConfig": {"responseMimeType": "application/json"}}
        apiUrl = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={gemini_api_key}"
        response = requests.post(apiUrl, headers={'Content-Type': 'application/json'}, json=payload)
        response.raise_for_status()
        result = response.json()
        if not result.get('candidates'): return None
        response_text = result['candidates'][0]['content']['parts'][0]['text']
        raw_data = json.loads(response_text)
        return raw_data if isinstance(raw_data, dict) else None
    except Exception:
        return None

def orchestrator_agent(product_info, gemini_api_key, brightdata_api_key, preferred_sites=[]):
    product_name = product_info['ProductName']
    manufacturer = product_info.get('Manufacturer', '')
    st.subheader(f"【統括エージェント】 \"{product_name}\" の情報収集を開始します。")
    is_japanese = bool(re.search(r'[\u3040-\u30ff\u4e00-\u9faf]', product_name))
    base_queries = [f"{manufacturer} {product_name}"]
    if is_japanese:
        english_product_name = translate_text_with_gemini(product_name, gemini_api_key)
        if english_product_name:
            base_queries.append(f"{manufacturer} {english_product_name}")
    site_map = {
        'コスモバイオ': 'cosmobio.co.jp', 'フナコシ': 'funakoshi.co.jp', 'AXEL': 'axel.as-1.co.jp',
        'Selleck': 'selleck.co.jp', 'MCE': 'medchemexpress.com', 'Nakarai': 'nacalai.co.jp',
        'FUJIFILM': 'labchem-wako.fujifilm.com'
    }
    search_queries = []
    for base_query in base_queries:
        if preferred_sites:
            for site_name in preferred_sites:
                if site_name in site_map:
                    search_queries.append(f"site:{site_map[site_name]} {base_query}")
        search_queries.append(base_query)
    all_urls = []
    for query in list(dict.fromkeys(search_queries)):
        all_urls.extend(search_product_urls_with_brightdata(query, brightdata_api_key))
    unique_urls = list(dict.fromkeys(all_urls))
    html_urls = [url for url in unique_urls if not url.lower().endswith(('.pdf', '.xls', '.xlsx', '.doc', '.docx'))]
    if not html_urls:
        st.warning("関連URLが見つかりませんでした。")
        return []
    found_offers = []
    st.info(f"{len(html_urls)}件のHTMLページを並列で分析します...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_to_url = {executor.submit(analyze_page_and_extract_info, url, product_name, gemini_api_key): url for url in html_urls}
        for future in concurrent.futures.as_completed(future_to_url):
            offer_details = future.result()
            if offer_details and offer_details.get("productName", "N/A") != "N/A":
                offer_details['sourceUrl'] = future_to_url[future]
                found_offers.append(offer_details)
    st.success(f"【統括エージェント】合計{len(found_offers)}件の製品情報を抽出しました。")
    return found_offers

# === Streamlit アプリケーションのメイン部分 ===
st.title("製品調達AIエージェント")
st.sidebar.header("APIキー設定")
try:
    gemini_api_key = st.secrets["GOOGLE_API_KEY"]
    brightdata_api_key = st.secrets["BRIGHTDATA_API_KEY"]
    st.sidebar.success("APIキーが設定されています。")
except KeyError:
    st.sidebar.error("StreamlitにAPIキーが設定されていません。")
    gemini_api_key, brightdata_api_key = "", ""

st.sidebar.header("検索条件")
product_name_input = st.sidebar.text_input("製品名 (必須)", placeholder="例: Y27632")
manufacturer_input = st.sidebar.text_input("メーカー", placeholder="例: Selleck")
specs_input = st.sidebar.text_input("仕様", placeholder="任意")
quantity_input = st.sidebar.number_input("数量", min_value=1, value=1)
unit_input = st.sidebar.text_input("単位", value="pcs")
min_price_input = st.sidebar.number_input("最低価格 (円)", min_value=0, value=0, step=100)
max_price_input = st.sidebar.number_input("最高価格 (円)", min_value=0, value=0, step=100)
preferred_sites_toggle = st.sidebar.checkbox("優先サイト検索 (コスモバイオ, フナコシ, AXEL, など)")
search_button = st.sidebar.button("検索開始", type="primary")

if search_button:
    if not gemini_api_key or not brightdata_api_key:
        st.error("APIキーが設定されていません。StreamlitのSecretsにキーを登録してください。")
    elif not product_name_input:
        st.error("製品名を入力してください。")
    else:
        with st.spinner('検索中です...しばらくお待ちください。'):
            product_info = {
                'ProductName': product_name_input, 'Manufacturer': manufacturer_input,
                'Specifications': specs_input, 'Quantity': quantity_input, 'Unit': unit_input
            }
            preferred_sites = ['コスモバイオ', 'フナコシ', 'AXEL', 'Selleck', 'MCE', 'Nakarai', 'FUJIFILM'] if preferred_sites_toggle else []
            offers_list = orchestrator_agent(product_info, gemini_api_key, brightdata_api_key, preferred_sites)
            final_results = []
            input_date = pd.Timestamp.now().strftime('%Y-%m-%d')
            query_words = product_name_input.strip().lower().split()
            if offers_list:
                for offer in offers_list:
                    result_name = offer.get('productName', '').lower()
                    if all(word in result_name for word in query_words):
                        price = int(offer.get('price', 0)) if str(offer.get('price', 0)).isdigit() else 0
                        if price > 0:
                            final_results.append({
                                '入力日': input_date, '製品名': offer.get('productName', 'N/A'),
                                '型番/製品番号': offer.get('modelNumber', 'N/A'), '仕様': product_info['Specifications'],
                                'メーカー': offer.get('manufacturer', 'N/A'), '数量': product_info['Quantity'],
                                '単位': product_info['Unit'], 'リスト単価': price,
                                '合計金額': price * product_info['Quantity'],
                                '在庫': 'あり' if offer.get('inStock') else 'なし/不明',
                                '情報元URL': offer.get('sourceUrl', 'N/A')
                            })
            if not final_results:
                 st.warning("有効な製品情報が見つかりませんでした。")
        st.success("全製品の情報収集が完了しました。")
        if final_results:
            df_results = pd.DataFrame(final_results)
            if max_price_input > 0:
                df_results = df_results[df_results['リスト単価'] <= max_price_input]
            if min_price_input > 0:
                 df_results = df_results[df_results['リスト単価'] >= min_price_input]
            st.subheader("検索結果")
            st.dataframe(
                df_results,
                column_config={"情報元URL": st.column_config.LinkColumn("Link", display_text="Click to Open")},
                use_container_width=True
            )
            @st.cache_data
            def convert_df_to_csv(df):
                return df.to_csv(index=False).encode('utf-8-sig')
            csv = convert_df_to_csv(df_results)
            st.download_button(
                label="結果をCSVでダウンロード", data=csv,
                file_name=f"purchase_list_{pd.Timestamp.now().strftime('%Y%m%d')}.csv", mime='text/csv'
            )