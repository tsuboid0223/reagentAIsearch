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

# === 3. AIエージェントと関連関数の定義 (Colab版から流用) ===

def search_product_urls_with_brightdata(query, api_key):
    st.info(f"【Bright Data】クエリ「{query}」で検索リクエストを送信...")

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    GoogleSearch_url = f"https://www.google.co.jp/search?q={urllib.parse.quote(query)}&hl=ja"
    payload = {'zone': 'serp_api1', 'url': GoogleSearch_url}

    try:
        # Initial request to Bright Data
        initial_response = requests.post('https://api.brightdata.com/serp/req', headers=headers, json=payload, timeout=30)
        initial_response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        # --- 追加のデバッグログ: 初期リクエストのステータスコードとレスポンス本文の一部 ---
        st.info(f"【Bright Data】初期リクエスト ステータスコード: {initial_response.status_code}")
        st.info(f"【Bright Data】初期リクエスト レスポンス本文 (最初の200文字): {initial_response.text[:200]}...")

        response_id = initial_response.headers.get('x-response-id')
        if not response_id:
            st.error("エラー: APIからのresponse_idが取得できませんでした。Bright Dataからの応答が不正です。")
            return []
        
        st.info(f"【Bright Data】リクエスト受付完了 (Response ID: {response_id})。結果を待機します...")

        result_url = f'https://api.brightdata.com/serp/get_result?response_id={response_id}'
        
        # Poll for results
        for i in range(1, 16): # Loop 15 times, total wait up to 30 seconds
            st.info(f"【Bright Data】結果取得試行 {i}/15...")
            time.sleep(2) # Wait 2 seconds before each attempt

            try:
                result_response = requests.get(result_url, headers={'Authorization': f'Bearer {api_key}'}, timeout=30)
                result_response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
                
                # --- 追加のデバッグログ: 各試行の結果取得ステータスコードとレスポンス本文の一部 ---
                st.info(f"【Bright Data】試行 {i} 結果取得ステータスコード: {result_response.status_code}")
                st.info(f"【Bright Data】試行 {i} 結果取得レスポンス本文 (最初の200文字): {result_response.text[:200]}...")

                # --- 既存の展開可能な生レスポンス表示部分 ---
                with st.expander(f"【Bright Data】試行 {i} の生レスポンス (ステータスコード: {result_response.status_code})"):
                    if result_response.text:
                        st.code(result_response.text, language='html')
                    else:
                        st.write("レスポンス本文は空でした。")
                # --- ここまで既存の展開可能な生レスポンス表示部分 ---

                if result_response.status_code == 200:
                    st.success(f"【Bright Data】結果取得完了 (ステータスコード: {result_response.status_code})。")
                    try:
                        if not result_response.text:
                            st.warning("結果取得完了、しかしレスポンスが空でした。Bright Dataからの検索結果がありませんでした。")
                            return []
                        
                        soup = BeautifulSoup(result_response.text, 'html.parser')
                        urls = []
                        for a_tag in soup.find_all('a', href=True):
                            href = a_tag.get('href')
                            # Filter out Google internal links and non-HTTP/HTTPS links
                            if href and href.startswith('http') and not href.startswith('https://www.google.com') and not href.startswith('https://accounts.google.com'):
                                urls.append(href)

                        if urls:
                            unique_urls = list(dict.fromkeys(urls))
                            st.success(f"【Bright Data】合計{len(unique_urls)}件のURLをHTMLから抽出しました。")
                            return unique_urls
                        else:
                            st.warning("検索結果のHTMLから有効なURLを抽出できませんでした。検索クエリに対する適切な結果が得られなかった可能性があります。")
                            return []
                    except requests.exceptions.JSONDecodeError: # This specific error might not happen if content-type is HTML, but good to keep for robustness
                        st.error(f"Bright Dataからのレスポンスが予期しない形式でした。内容: {result_response.text[:200]}...")
                        return []
                elif result_response.status_code == 202:
                    st.info(f"【Bright Data】結果はまだ準備中です (ステータスコード: {result_response.status_code})。次の試行を待ちます。")
                    # Continue loop if status is 202 (Accepted)
                else:
                    st.error(f"結果取得エラー: 予期しないステータスコード {result_response.status_code} を受け取りました。")
                    return []
            except requests.exceptions.Timeout:
                st.warning(f"結果取得試行 {i}/15 でタイムアウトしました (個別のリクエストタイムアウト)。Bright Dataからの応答が遅い可能性があります。")
                # Continue loop as it's an individual request timeout, not the overall polling timeout
            except requests.exceptions.RequestException as e:
                st.error(f"結果取得試行 {i}/15 でネットワークエラーが発生しました: {e}。Bright Dataへの接続に問題がある可能性があります。")
                return [] # Exit on other request exceptions during polling

        # If the loop finishes without returning (i.e., status 200 was never received)
        st.error("結果取得がタイムアウトしました。指定された時間内にBright Dataからの検索結果が準備されませんでした。")
        st.error("考えられる原因: Bright Data側の処理遅延、または検索クエリに対する結果生成に時間がかかっている可能性があります。")
        return []

    except requests.exceptions.Timeout:
        st.error(f"Bright Dataへの初期リクエストがタイムアウトしました。APIへの接続に問題があるか、非常に時間がかかっています。")
        return []
    except requests.exceptions.RequestException as e:
        st.error(f"Bright Data API呼び出しエラー: {e}。初期リクエストの送信に失敗しました。")
        return []

def analyze_page_and_extract_info(url, product_name, gemini_api_key):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        html_content = response.text
    except requests.exceptions.RequestException as e:
        st.warning(f"URL {url} のコンテンツ取得に失敗しました: {e}")
        return None

    soup = BeautifulSoup(html_content, 'html.parser')
    for s in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
        s.decompose()
    body_text = soup.body.get_text(separator=' ', strip=True) if soup.body else ''

    if len(body_text) > 18000: body_text = body_text[:18000]

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
        response = requests.post(apiUrl, headers={'Content-Type': 'application/json'}, json=payload)
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

def orchestrator_agent(product_info, gemini_api_key, brightdata_api_key, preferred_sites=[]):
    product_name = product_info['ProductName']
    manufacturer = product_info.get('Manufacturer', '')
    st.subheader(f"【統括エージェント】 \"{product_name}\" の情報収集を開始します。")

    base_query = f"{manufacturer} {product_name}"
    site_map = {
        'コスモバイオ': 'cosmobio.co.jp', 'フナコシ': 'funakoshi.co.jp', 'AXEL': 'axel.as-1.co.jp',
        'Selleck': 'selleck.co.jp', 'MCE': 'medchemexpress.com', 'Nakarai': 'nacalai.co.jp',
        'FUJIFILM': 'labchem-wako.fujifilm.com'
    }
    search_queries = []
    if preferred_sites:
        for site_name in preferred_sites:
            if site_name in site_map:
                search_queries.append(f"site:{site_map[site_name]} {base_query}")
    search_queries.append(base_query)
    all_urls = []
    for query in search_queries:
        all_urls.extend(search_product_urls_with_brightdata(query, brightdata_api_key))
    unique_urls = list(dict.fromkeys(all_urls))
    html_urls = [url for url in unique_urls if not url.lower().endswith(('.pdf', '.xls', '.xlsx', '.doc', '.docx'))]
    if not html_urls:
        st.warning("関連URLが見つかりませんでした。検索クエリやBright Dataの設定を確認してください。")
        return []
    
    found_offers = []
    st.info(f"{len(html_urls)}件のHTMLページを並列で分析します...")
    
    # Use tqdm for progress bar in a non-Streamlit context, or a custom Streamlit progress bar.
    # For Streamlit, st.progress is better.
    progress_text = "Webページを分析中..."
    my_bar = st.progress(0, text=progress_text)
    processed_count = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_to_url = {executor.submit(analyze_page_and_extract_info, url, product_name, gemini_api_key): url for url in html_urls}
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

# === Streamlit アプリケーションのメイン部分 ===

# アプリのタイトル
st.title("製品調達AIエージェント")

# サイドバーにAPIキー読み込み処理を設置
st.sidebar.header("APIキー設定")
try:
    gemini_api_key = st.secrets["GOOGLE_API_KEY"]
    brightdata_api_key = st.secrets["BRIGHTDATA_API_KEY"]
    st.sidebar.success("APIキーが設定されています。")
except KeyError:
    st.sidebar.error("StreamlitにAPIキーが設定されていません。`GOOGLE_API_KEY` と `BRIGHTDATA_API_KEY` をSecretsに登録してください。")
    # キーが設定されていない場合、処理が進まないように空文字をセット
    gemini_api_key = ""
    brightdata_api_key = ""

# サイドバーに検索条件入力欄を設置
st.sidebar.header("検索条件")
product_name_input = st.sidebar.text_input("製品名 (必須)", placeholder="例: Y27632")
manufacturer_input = st.sidebar.text_input("メーカー", placeholder="例: Selleck")
specs_input = st.sidebar.text_input("仕様", placeholder="任意")
quantity_input = st.sidebar.number_input("数量", min_value=1, value=1)
unit_input = st.sidebar.text_input("単位", value="pcs")
min_price_input = st.sidebar.number_input("最低価格 (円)", min_value=0, value=0, step=100)
max_price_input = st.sidebar.number_input("最高価格 (円)", min_value=0, value=0, step=100)

preferred_sites_toggle = st.sidebar.checkbox("優先サイト検索 (コスモバイオ, フナコシ, AXEL, など)")

# 検索ボタン
search_button = st.sidebar.button("検索開始", type="primary")

# 検索ボタンが押されたら処理を開始
if search_button:
    # --- 入力チェック ---
    if not gemini_api_key or not brightdata_api_key:
        st.error("APIキーが設定されていません。StreamlitのSecretsにキーを登録してください。")
    elif not product_name_input:
        st.error("製品名を入力してください。")
    else:
        with st.spinner('検索中です...しばらくお待ちください。'):
            # --- 検索処理の実行 ---
            product_info = {
                'ProductName': product_name_input,
                'Manufacturer': manufacturer_input,
                'Specifications': specs_input,
                'Quantity': quantity_input,
                'Unit': unit_input
            }
            
            preferred_sites = []
            if preferred_sites_toggle:
                preferred_sites = ['コスモバイオ', 'フナコシ', 'AXEL', 'Selleck', 'MCE', 'Nakarai', 'FUJIFILM']

            offers_list = orchestrator_agent(product_info, gemini_api_key, brightdata_api_key, preferred_sites)

            final_results = []
            input_date = pd.Timestamp.now().strftime('%Y-%m-%d')

            if offers_list:
                for offer in offers_list:
                    price = 0
                    try:
                        price = int(offer.get('price', 0))
                    except (ValueError, TypeError):
                        price = 0
                    
                    final_results.append({
                        '入力日': input_date, '製品名': offer.get('productName', 'N/A'),
                        '型番/製品番号': offer.get('modelNumber', 'N/A'), '仕様': product_info['Specifications'],
                        'メーカー': offer.get('manufacturer', 'N/A'), '数量': product_info['Quantity'],
                        '単位': product_info['Unit'], 'リスト単価': price,
                        '合計金額': price * product_info['Quantity'],
                        '在庫': 'あり' if offer.get('inStock') else 'なし/不明',
                        '情報元URL': offer.get('sourceUrl', 'N/A')
                    })
            else:
                st.warning("検索結果から有効な製品情報が見つかりませんでした。")
                query_for_url = f"{product_info.get('Manufacturer', '')} {product_info.get('ProductName', '')}"
                final_results.append({
                    '入力日': input_date, '製品名': product_info['ProductName'], '型番/製品番号': 'N/A',
                    '仕様': product_info['Specifications'], 'メーカー': product_info['Manufacturer'], '数量': product_info['Quantity'],
                    '単位': product_info['Unit'], 'リスト単価': 0, '合計金額': 0, '在庫': 'なし/不明',
                    '情報元URL': f"https://www.google.com/search?q={urllib.parse.quote(query_for_url)}"
                })
            
            st.success("全製品の情報収集が完了しました。")

            # --- 結果の表示と整形 ---
            if final_results:
                df_results = pd.DataFrame(final_results)

                if max_price_input > 0:
                    df_results = df_results[df_results['リスト単価'] <= max_price_input]
                if min_price_input > 0:
                    df_results = df_results[df_results['リスト単価'] >= min_price_input]

                st.subheader("検索結果")
                st.dataframe(
                    df_results,
                    column_config={
                        "情報元URL": st.column_config.LinkColumn("Link", display_text="Click to Open")
                    },
                    use_container_width=True
                )

                # --- CSVダウンロードボタン ---
                @st.cache_data
                def convert_df_to_csv(df):
                    return df.to_csv(index=False).encode('utf-8-sig')

                csv = convert_df_to_csv(df_results)
                st.download_button(
                    label="結果をCSVでダウンロード",
                    data=csv,
                    file_name=f"purchase_list_{pd.Timestamp.now().strftime('%Y%m%d')}.csv",
                    mime='text/csv',
                )
