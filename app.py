# -*- coding: utf-8 -*-
"""
製品調達AIエージェント Streamlitアプリケーション
（ハング回避・診断機能強化版）
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
import logging
import sys

# 診断オプション: ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
# === Bright Data API 連携関数 ===
# ==============================================================================

def get_page_content_with_brightdata(url: str, brd_username: str, brd_password: str, timeout: int = 15) -> dict:
    """
    Scraping Browserで生bodyテキスト抽出（ハング回避版）
    """
    BRD_HOST = 'brd.superproxy.io'
    BRD_PORT = 24000
    proxy_url = f'http://{brd_username}:{brd_password}@{BRD_HOST}:{BRD_PORT}'
    proxies = {'http': proxy_url, 'https': proxy_url}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
    }
    result = {"url": url, "status_code": None, "content": None, "error": None}
    
    # ログを蓄積してまとめて表示（ブロッキング回避）
    logs = []
    logs.append(f"🔍 接続開始: {url[:60]}...")
    logger.info(f"Starting connection to: {url}")
    
    # 1. Scraping Browser試行 (POST) - タイムアウト短縮
    payload = {
        'url': url,
        'renderJS': True,
        'waitFor': 3000,  # 5000 → 3000に短縮
        'proxy': 'residential'
    }
    
    try:
        logs.append(f"  📤 POST接続中 (timeout: {timeout}秒)...")
        logger.info(f"POST request attempt - timeout: {timeout}s")
        sys.stdout.flush()  # ログを即座に出力
        
        response = requests.post(
            proxy_url,
            json=payload,
            headers=headers,
            proxies=proxies,
            verify=False,
            timeout=timeout
        )
        response.raise_for_status()
        logs.append(f"  ✅ POST成功 (status: {response.status_code})")
        logger.info(f"POST success - status: {response.status_code}, length: {len(response.text)}")
        
        # レスポンス検証
        if len(response.text) < 500:
            logs.append(f"  ⚠️ レスポンスが短すぎる ({len(response.text)}文字) - フォールバック")
            logger.warning(f"Response too short: {len(response.text)} chars")
            raise ValueError("Response too short")
        
        # JSON or HTML判定
        try:
            data = response.json()
            html = data.get('content', response.text)
        except json.JSONDecodeError:
            html = response.text
        
    except Exception as e:
        logs.append(f"  ❌ POST失敗: {str(e)[:60]}")
        logger.error(f"POST failed: {str(e)[:100]}")
        
        # 2. フォールバック: シンプルプロキシGET
        full_url = f'{proxy_url}/{url}'
        try:
            logs.append(f"  📥 GETフォールバック中 (timeout: {timeout//2}秒)...")
            logger.info(f"GET fallback attempt - timeout: {timeout//2}s")
            sys.stdout.flush()
            
            response = requests.get(
                full_url,
                headers=headers,
                proxies=proxies,
                verify=False,
                timeout=timeout // 2
            )
            response.raise_for_status()
            logs.append(f"  ✅ GET成功 (status: {response.status_code})")
            logger.info(f"GET success - status: {response.status_code}, length: {len(response.text)}")
            
            # レスポンス検証
            if len(response.text) < 500:
                logs.append(f"  ⚠️ GETレスポンスも短い ({len(response.text)}文字) - スキップ")
                logger.warning(f"GET response also too short: {len(response.text)} chars")
                result["error"] = "Both POST and GET returned insufficient content"
                st.warning("\n".join(logs))
                return result
            
            html = response.text
            
        except Exception as e2:
            logs.append(f"  ❌ GET失敗: {str(e2)[:60]}")
            logger.error(f"GET failed: {str(e2)[:100]}")
            result["error"] = f"POST: {str(e)[:50]}; GET: {str(e2)[:50]}"
            st.error("\n".join(logs))
            return result
    
    # テキスト抽出
    logs.append(f"  🔧 HTML解析中...")
    logger.info("Starting HTML parsing")
    sys.stdout.flush()
    
    try:
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'iframe']):
            tag.decompose()
        
        body_text = soup.body.get_text(separator=' ', strip=True) if soup.body else soup.get_text(separator=' ', strip=True)
        result["content"] = body_text[:18000]
        result["status_code"] = 200
        
        logs.append(f"  ✅ 抽出完了: {len(result['content'])}文字")
        logger.info(f"Extraction complete: {len(result['content'])} chars")
        st.success("\n".join(logs))
        
    except Exception as e:
        logs.append(f"  ❌ 解析失敗: {str(e)}")
        logger.error(f"Parsing failed: {str(e)}")
        result["error"] = str(e)
        st.error("\n".join(logs))
    
    return result


def search_product_urls_with_brightdata(query: str, api_key: str) -> list:
    """Bright DataのSERP APIでGoogle検索を実行し、URLリストを取得する。"""
    st.info(f"【Bright Data】クエリ「{query}」で検索リクエストを送信...")
    logger.info(f"SERP API search: {query}")
    
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
        initial_response = requests.post(
            'https://api.brightdata.com/serp/req',
            headers=headers,
            json=payload,
            timeout=30
        )
        initial_response.raise_for_status()
        response_id = initial_response.headers.get('x-response-id')
        
        if not response_id:
            logger.warning("No response_id received from SERP API")
            return []
        
        logger.info(f"SERP response_id: {response_id}")
        result_url = f'https://api.brightdata.com/serp/get_result?response_id={response_id}'
        
        for attempt in range(15):
            time.sleep(random.uniform(2, 5))
            try:
                result_response = requests.get(result_url, headers=headers, timeout=30)
                
                if result_response.status_code == 200:
                    if not result_response.text:
                        logger.warning("Empty response from SERP API")
                        return []
                    
                    soup = BeautifulSoup(result_response.text, 'html.parser')
                    result_divs = soup.find_all('div', {'data-ved': True}) or soup.find_all('div', class_='g')
                    urls = []
                    
                    for div in result_divs:
                        a_tag = div.find('a', href=True)
                        if a_tag and a_tag.get('href') and a_tag.get('href').startswith('http') and not a_tag.get('href').startswith('https://www.google.'):
                            urls.append(a_tag.get('href'))
                    
                    unique_urls = list(dict.fromkeys(urls))[:10]
                    st.success(f"【Bright Data】「{query}」から{len(unique_urls)}件のURLを抽出しました。")
                    logger.info(f"Extracted {len(unique_urls)} URLs from SERP")
                    return unique_urls
                    
                elif result_response.status_code != 202:
                    logger.warning(f"Unexpected status code: {result_response.status_code}")
                    return []
                    
            except requests.exceptions.RequestException as e:
                logger.error(f"SERP result fetch error (attempt {attempt+1}): {str(e)}")
                return []
        
        logger.warning("SERP API timeout after 15 attempts")
        return []
        
    except requests.exceptions.RequestException as e:
        logger.error(f"SERP API request failed: {str(e)}")
        return []

# ==============================================================================
# === AIエージェント関連関数 ===
# ==============================================================================

def analyze_page_and_extract_info(page_content_result: dict, product_name: str, gemini_api_key: str, retry_count: int = 2) -> dict | None:
    """HTMLをGemini APIに渡し、製品情報を抽出する（リトライ機能付き）"""
    body_text = page_content_result.get("content")
    if page_content_result.get("error") or not body_text:
        return None

    prompt = f"""
あなたは化学試薬ECサイトの情報抽出エージェントです。

【抽出対象製品】
- 製品名: {product_name}

【Webページテキスト】
{body_text}

【抽出ルール】
1. productName: ページタイトルまたはh1要素の製品名
2. modelNumber: 型番/製品コード/カタログ番号（例: ALX-270-333-M001）
3. manufacturer: メーカー/サプライヤー名（例: ENZ, Selleck）
4. offers: 価格表から以下を抽出
   - size: 容量/規格（例: "1 mg", "5 mg", "1 MG"）
   - price: 価格の数値のみ（例: ¥34,000 → 34000）
   - inStock: 在庫状況（「在庫あり」「カートに入れる」リンクがあればtrue、それ以外はfalse）

【注意事項】
- 文字化け「��」は「¥」として処理してください
- 価格表が複数行ある場合は全て抽出してください
- 情報が見つからない項目はnullを返してください
- offers配列は必ず作成し、データがない場合は空配列を返してください

【出力形式】
{{
  "productName": "string or null",
  "modelNumber": "string or null", 
  "manufacturer": "string or null",
  "offers": [
    {{"size": "string", "price": number, "inStock": boolean}}
  ]
}}
"""
    
    for attempt in range(retry_count):
        try:
            logger.info(f"Gemini API call attempt {attempt+1}/{retry_count}")
            sys.stdout.flush()
            
            payload = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"}
            }
            api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={gemini_api_key}"
            
            response = requests.post(
                api_url,
                headers={'Content-Type': 'application/json'},
                json=payload,
                timeout=45
            )
            response.raise_for_status()
            result = response.json()
            
            if not result.get('candidates'):
                logger.warning(f"No candidates in Gemini response (attempt {attempt+1})")
                if attempt < retry_count - 1:
                    st.warning(f"⚠️ Gemini応答なし (試行{attempt+1}/{retry_count}) - リトライ中...")
                    time.sleep(2)
                    continue
                return None
            
            response_text = result['candidates'][0]['content']['parts'][0]['text']
            raw_data = json.loads(response_text)
            
            # バリデーション
            if raw_data and isinstance(raw_data, dict):
                if raw_data.get("offers") and len(raw_data["offers"]) > 0:
                    logger.info(f"Successfully extracted {len(raw_data['offers'])} offers")
                    return raw_data
                elif attempt < retry_count - 1:
                    st.warning(f"⚠️ offers抽出失敗 (試行{attempt+1}/{retry_count}) - リトライ中...")
                    logger.warning("No offers found in extracted data")
                    time.sleep(2)
                    continue
            
            return raw_data if isinstance(raw_data, dict) else None
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error (attempt {attempt+1}): {str(e)}")
            if attempt < retry_count - 1:
                st.warning(f"⚠️ JSON解析失敗 - リトライ中... ({str(e)[:50]})")
                time.sleep(2)
            else:
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Gemini API request error (attempt {attempt+1}): {str(e)}")
            if attempt < retry_count - 1:
                st.warning(f"⚠️ API呼び出し失敗 - リトライ中... ({str(e)[:50]})")
                time.sleep(2)
            else:
                return None
    
    return None

# ==============================================================================
# === 統括エージェント ===
# ==============================================================================

def orchestrator_agent(product_info: dict, gemini_api_key: str, brightdata_api_key: str, brd_username: str, brd_password: str, preferred_sites: list, debug_mode: bool = False) -> tuple[list, list]:
    """一連の処理を統括するエージェント（ハング回避版）"""
    product_name = product_info['ProductName']
    manufacturer = product_info.get('Manufacturer', '')
    st.subheader(f"【統括エージェント】 \"{product_name}\" の情報収集を開始します。")
    logger.info(f"Orchestrator started for: {product_name} (manufacturer: {manufacturer})")

    base_query = f"{manufacturer} {product_name}"
    site_map = {
        'コスモバイオ': 'cosmobio.co.jp',
        'フナコシ': 'funakoshi.co.jp',
        'AXEL': 'axel.as-1.co.jp',
        'Selleck': 'selleck.co.jp',
        'MCE': 'medchemexpress.com',
        'Nakarai': 'nacalai.co.jp',
        'FUJIFILM': 'labchem-wako.fujifilm.com',
        '関東化学': 'kanto.co.jp',
        'TCI': 'tcichemicals.com',
        'Merck': 'merck.com',
        '和光純薬': 'hpc-j.co.jp'
    }
    search_queries = [f"site:{site_map[site_name]} {base_query}" for site_name in preferred_sites if site_name in site_map]
    search_queries.append(base_query)

    # 進捗バー初期化 (0%)
    progress_bar = st.progress(0)
    status_text = st.empty()

    # ステップ1: URL抽出 (0-20%)
    status_text.text("URL抽出中...")
    progress_bar.progress(0.05)
    logger.info(f"Starting URL extraction - {len(search_queries)} queries")
    
    all_urls = []
    num_queries = len(search_queries)
    
    for i, query in enumerate(search_queries):
        urls = search_product_urls_with_brightdata(query, brightdata_api_key)
        all_urls.extend(urls)
        if urls and debug_mode:
            st.info(f"✅ クエリ{i+1}: {len(urls)}件取得")
        progress = 0.05 + (i / num_queries) * 0.15
        progress_bar.progress(progress)
        status_text.text(f"URL抽出中... ({i+1}/{num_queries})")
    
    unique_urls = list(dict.fromkeys(all_urls))[:15]  # 10 → 15に増加（失敗を見越して）
    
    if not unique_urls:
        st.error("検索結果からURLを取得できませんでした。")
        logger.error("No URLs extracted from search results")
        return [], []
    
    st.info(f"📊 {len(unique_urls)}件のURLを取得しました。順次アクセスします...")
    logger.info(f"Extracted {len(unique_urls)} unique URLs")
    progress_bar.progress(0.2)
    status_text.text("URL抽出完了 (20%)")

    # ステップ2: ページ取得 (20-70%) - タイムアウト制御追加
    status_text.text("Webページを取得中...")
    logger.info("Starting page content retrieval")
    all_page_content_results = []
    success_count = 0
    fail_count = 0

    for i, url in enumerate(unique_urls):
        status_text.text(f"📄 取得中 ({i + 1}/{len(unique_urls)}): {url[:50]}...")
        logger.info(f"Processing URL {i+1}/{len(unique_urls)}: {url}")
        
        # タイムアウトを動的に調整（失敗が多い場合は短くする）
        dynamic_timeout = 12 if fail_count < 3 else 8
        
        try:
            page_result = get_page_content_with_brightdata(url, brd_username, brd_password, timeout=dynamic_timeout)
            all_page_content_results.append(page_result)
            
            if page_result.get('content') and len(page_result.get('content', '')) > 1000:
                success_count += 1
                logger.info(f"Success: {url} ({len(page_result['content'])} chars)")
            else:
                fail_count += 1
                logger.warning(f"Insufficient content: {url} ({len(page_result.get('content', ''))} chars)")
                if debug_mode:
                    st.warning(f"⚠️ 内容不足: {url[:50]} ({len(page_result.get('content', ''))}文字)")
        
        except Exception as e:
            fail_count += 1
            logger.error(f"Unexpected error for {url}: {str(e)}")
            st.error(f"💥 予期しないエラー: {url[:50]} - {str(e)[:50]}")
            all_page_content_results.append({"url": url, "error": str(e)})
        
        # 進捗更新
        progress = 0.2 + (i + 1) / len(unique_urls) * 0.5
        progress_bar.progress(progress)
        
        # 早期終了判定（成功が5件以上あれば残りをスキップ可能）
        if success_count >= 5 and i >= 10:
            st.info(f"✅ 十分な情報を取得 ({success_count}件成功) - 残りをスキップします")
            logger.info(f"Early termination: {success_count} successes achieved")
            break

    st.info(f"📈 取得結果: 成功 {success_count}件 / 失敗 {fail_count}件")
    logger.info(f"Page retrieval complete - Success: {success_count}, Fail: {fail_count}")
    progress_bar.progress(0.7)
    status_text.text("ページ取得完了 (70%)")

    # ステップ3: AI解析 (70-100%)
    status_text.text("AIでページを分析中...")
    logger.info("Starting AI analysis")
    found_pages_data = []
    successful_contents = [
        res for res in all_page_content_results 
        if res.get("content") and len(res.get("content", "")) > 1000 and not res.get("error")
    ]
    
    if not successful_contents:
        st.warning("有効なコンテンツが取得できませんでした。")
        logger.warning("No valid content for AI analysis")
        progress_bar.progress(1.0)
        status_text.text("完了 (100%) - データなし")
        return [], all_page_content_results
    
    logger.info(f"Analyzing {len(successful_contents)} pages with AI")
    
    for i, content_res in enumerate(successful_contents):
        status_text.text(f"🤖 AI分析中... ({i + 1}/{len(successful_contents)})")
        
        try:
            page_details = analyze_page_and_extract_info(content_res, product_name, gemini_api_key)
            
            if page_details and page_details.get("offers"):
                page_details['sourceUrl'] = content_res.get("url")
                found_pages_data.append(page_details)
                logger.info(f"Extracted {len(page_details['offers'])} offers from {content_res.get('url')}")
                if debug_mode:
                    st.success(f"✅ 抽出成功: {len(page_details['offers'])}件のoffer")
                    with st.expander(f"AI解析結果: {content_res.get('url')[:50]}"):
                        st.json(page_details)
        
        except Exception as e:
            logger.error(f"AI analysis error: {str(e)}")
            st.warning(f"⚠️ AI解析エラー: {str(e)[:50]}")
        
        progress = 0.7 + (i + 1) / len(successful_contents) * 0.3
        progress_bar.progress(progress)

    progress_bar.progress(1.0)
    status_text.text("完了 (100%)")
    st.success(f"【統括エージェント】{len(found_pages_data)}ページから製品情報を抽出しました。")
    logger.info(f"Orchestrator complete - {len(found_pages_data)} pages with product info")
    
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
    st.sidebar.success("✅ APIキーと認証情報が設定されています。")
    logger.info("API credentials loaded successfully")
except KeyError as e:
    st.sidebar.error("❌ Streamlit Secretsに必要な情報が設定されていません。")
    logger.error(f"Missing secret key: {str(e)}")
    gemini_api_key, brightdata_api_key, brightdata_username, brightdata_password = "", "", "", ""

st.sidebar.header("検索条件")
product_name_input = st.sidebar.text_input("製品名 (必須)", placeholder="例: Y27632")
manufacturer_input = st.sidebar.text_input("メーカー", placeholder="例: Selleck")
min_price_input = st.sidebar.number_input("最低価格 (円)", min_value=0, value=0, step=100)
max_price_input = st.sidebar.number_input("最高価格 (円)", min_value=0, value=0, step=100)
debug_mode_checkbox = st.sidebar.checkbox("🔧 デバッグモードを有効にする (詳細ログ表示)")
search_button = st.sidebar.button("🚀 検索開始", type="primary")

if search_button:
    if not all([gemini_api_key, brightdata_api_key, brightdata_username, brightdata_password]):
        st.error("❌ APIキーまたは認証情報が設定されていません。")
        logger.error("API credentials not configured")
    elif not product_name_input:
        st.error("❌ 製品名を入力してください。")
        logger.error("Product name not provided")
    else:
        logger.info(f"Search started - Product: {product_name_input}, Manufacturer: {manufacturer_input}")
        
        with st.spinner('AIエージェントが情報収集中...'):
            product_info = {
                'ProductName': product_name_input,
                'Manufacturer': manufacturer_input
            }
            preferred_sites = [
                'コスモバイオ', 'フナコシ', 'AXEL', 'Selleck', 'MCE',
                'Nakarai', 'FUJIFILM', '関東化学', 'TCI', 'Merck', '和光純薬'
            ]
            
            pages_list, log_data = orchestrator_agent(
                product_info,
                gemini_api_key,
                brightdata_api_key,
                brightdata_username,
                brightdata_password,
                preferred_sites,
                debug_mode=debug_mode_checkbox
            )
            
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
                st.warning("⚠️ 検索結果から有効な製品情報が見つかりませんでした。")
                logger.warning("No product information extracted")
                search_term = f"{product_info.get('Manufacturer', '')} {product_info['ProductName']}"
                query_url = f"https://www.google.com/search?q={urllib.parse.quote(search_term)}"
                final_results.append({
                    '入力日': input_date,
                    '製品名': product_info['ProductName'],
                    '型番/製品番号': 'N/A',
                    '仕様': 'N/A',
                    'メーカー': product_info.get('Manufacturer', ''),
                    'リスト単価': 0,
                    '在庫': 'なし/不明',
                    '情報元URL': query_url
                })
            
            st.success("✅ 全製品の情報収集が完了しました。")
            logger.info(f"Search complete - {len(final_results)} results")

            df_results = pd.DataFrame(final_results)
            
            # 価格フィルタリング
            if max_price_input > 0:
                df_results = df_results[df_results['リスト単価'] <= max_price_input]
            if min_price_input > 0:
                df_results = df_results[df_results['リスト単価'] >= min_price_input]

            st.subheader("📊 検索結果")
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
                label="📥 結果をCSVでダウンロード",
                data=csv,
                file_name=f"purchase_list_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime='text/csv'
            )

        # デバッグモード: 詳細ログ表示
        if debug_mode_checkbox and log_data:
            st.subheader("🔍 詳細デバッグログ")
            logger.info("Displaying debug logs")
            
            for idx, log in enumerate(log_data):
                status = log.get('status_code')
                is_error = log.get('error') is not None
                
                if is_error:
                    st.error(f"❌ 接続エラー: {log['url']}")
                elif status != 200 and status is not None:
                    st.warning(f"⚠️ ステータスコード異常 ({status}): {log['url']}")
                else:
                    st.success(f"✅ 取得成功 ({status}): {log['url']}")

                with st.expander(f"詳細を表示 - URL {idx+1}"):
                    if is_error:
                        st.write(f"**エラー内容:** `{log['error']}`")
                    
                    log_display = log.copy()
                    if log_display.get('content'):
                        content_len = len(log_display['content'])
                        log_display['content'] = (
                            log_display['content'][:1000] + "..."
                            if len(log_display['content']) > 1000
                            else log_display['content']
                        )
                        st.write(f"**Content長:** {content_len}文字")
                        if content_len < 1000:
                            st.warning("⚠️ このページのコンテンツが短すぎます（ブロック疑い）。")
                    
                    st.json(log_display)

logger.info("Application ready")
