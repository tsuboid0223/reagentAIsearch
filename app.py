# -*- coding: utf-8 -*-
"""
製品調達AIエージェント Streamlitアプリケーション
（404対策版: URL検証 + 直接アクセスフォールバック）
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
from datetime import datetime

# 診断オプション: ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
# リアルタイムログ表示用のヘルパークラス
# ==============================================================================
class RealTimeLogger:
    """Streamlitでリアルタイムログを表示するクラス"""
    def __init__(self):
        self.log_container = st.empty()
        self.logs = []
    
    def add(self, message: str, level: str = "info"):
        """ログを追加して即座に表示"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        if level == "info":
            icon = "ℹ️"
        elif level == "success":
            icon = "✅"
        elif level == "warning":
            icon = "⚠️"
        elif level == "error":
            icon = "❌"
        else:
            icon = "📝"
        
        log_entry = f"{icon} [{timestamp}] {message}"
        self.logs.append(log_entry)
        
        # 最新20件のログを表示
        display_logs = self.logs[-20:]
        self.log_container.text_area(
            "リアルタイムログ",
            "\n".join(display_logs),
            height=300,
            key=f"log_{len(self.logs)}"
        )
        
        # ターミナルにも出力
        logger.info(message)
        sys.stdout.flush()
    
    def clear(self):
        """ログをクリア"""
        self.logs = []
        self.log_container.empty()

# ==============================================================================
# URL検証関数（新規追加）
# ==============================================================================

def validate_url_quick(url: str, rt_logger: RealTimeLogger, timeout: int = 5) -> bool:
    """
    URLが有効かを高速チェック（HEADリクエスト）
    """
    try:
        rt_logger.add(f"  URL検証中: {url[:60]}...", "info")
        
        # HEADリクエストで軽量チェック
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.head(url, headers=headers, allow_redirects=True, timeout=timeout)
        
        if response.status_code == 404:
            rt_logger.add(f"  ❌ 404 Not Found - スキップ", "warning")
            return False
        elif response.status_code >= 400:
            rt_logger.add(f"  ⚠️ エラーステータス {response.status_code} - スキップ", "warning")
            return False
        else:
            rt_logger.add(f"  ✅ URL有効 (status: {response.status_code})", "success")
            return True
            
    except requests.exceptions.Timeout:
        rt_logger.add(f"  ⚠️ 検証タイムアウト - 試行してみる", "warning")
        return True  # タイムアウトの場合は試行する
    except Exception as e:
        rt_logger.add(f"  ⚠️ 検証エラー: {str(e)[:50]} - 試行してみる", "warning")
        return True

# ==============================================================================
# 直接アクセス関数（新規追加）
# ==============================================================================

def get_page_content_direct(url: str, rt_logger: RealTimeLogger, timeout: int = 10) -> dict:
    """
    プロキシを使わず直接アクセス（最終フォールバック）
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
    }
    result = {"url": url, "status_code": None, "content": None, "error": None}
    
    try:
        rt_logger.add(f"  直接アクセス試行 (timeout: {timeout}秒)...", "info")
        start_time = time.time()
        
        response = requests.get(url, headers=headers, timeout=timeout)
        elapsed = time.time() - start_time
        
        rt_logger.add(f"  直接アクセス応答 ({elapsed:.1f}秒) - status: {response.status_code}", "info")
        response.raise_for_status()
        
        if len(response.text) < 500:
            rt_logger.add(f"  ⚠️ レスポンスが短い ({len(response.text)}文字)", "warning")
            result["error"] = "Response too short"
            return result
        
        rt_logger.add(f"  直接アクセス成功 - {len(response.text)}文字", "success")
        
        soup = BeautifulSoup(response.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'iframe']):
            tag.decompose()
        
        body_text = soup.body.get_text(separator=' ', strip=True) if soup.body else soup.get_text(separator=' ', strip=True)
        result["content"] = body_text[:18000]
        result["status_code"] = response.status_code
        
        return result
        
    except Exception as e:
        rt_logger.add(f"  直接アクセス失敗: {str(e)[:50]}", "error")
        result["error"] = str(e)
        return result

# ==============================================================================
# === Bright Data API 連携関数（改善版）===
# ==============================================================================

def get_page_content_with_brightdata(url: str, brd_username: str, brd_password: str, rt_logger: RealTimeLogger, timeout: int = 10) -> dict:
    """
    Scraping Browserで生bodyテキスト抽出（404対策版）
    """
    # ステップ0: URL検証
    if not validate_url_quick(url, rt_logger):
        return {"url": url, "status_code": 404, "content": None, "error": "URL validation failed (404)"}
    
    BRD_HOST = 'brd.superproxy.io'
    BRD_PORT = 24000
    proxy_url = f'http://{brd_username}:{brd_password}@{BRD_HOST}:{BRD_PORT}'
    proxies = {'http': proxy_url, 'https': proxy_url}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
    }
    result = {"url": url, "status_code": None, "content": None, "error": None}
    
    rt_logger.add(f"Bright Data接続開始: {url[:60]}...", "info")
    
    # 1. Scraping Browser試行 (POST) - シンプル化
    payload = {
        'url': url,
        'renderJS': False,  # True → False (高速化)
    }
    
    try:
        rt_logger.add(f"  POST接続中 (timeout: {timeout}秒)...", "info")
        start_time = time.time()
        
        response = requests.post(
            proxy_url,
            json=payload,
            headers=headers,
            proxies=proxies,
            verify=False,
            timeout=timeout
        )
        
        elapsed = time.time() - start_time
        rt_logger.add(f"  POST応答受信 ({elapsed:.1f}秒) - status: {response.status_code}", "info")
        
        response.raise_for_status()
        
        # レスポンス検証
        response_len = len(response.text)
        rt_logger.add(f"  レスポンス長: {response_len}文字", "info")
        
        if response_len < 500:
            rt_logger.add(f"  レスポンスが短すぎる - GETへ", "warning")
            raise ValueError("Response too short")
        
        # JSON or HTML判定
        try:
            data = response.json()
            html = data.get('content', response.text)
            rt_logger.add(f"  JSON形式で受信", "success")
        except json.JSONDecodeError:
            html = response.text
            rt_logger.add(f"  HTML形式で受信", "success")
        
    except requests.exceptions.Timeout:
        rt_logger.add(f"  POSTタイムアウト - GETへ", "error")
        
        # 2. GETフォールバック
        full_url = f'{proxy_url}/{url}'
        try:
            rt_logger.add(f"  GET接続中 (timeout: {timeout//2}秒)...", "info")
            start_time = time.time()
            
            response = requests.get(full_url, headers=headers, proxies=proxies, verify=False, timeout=timeout // 2)
            elapsed = time.time() - start_time
            rt_logger.add(f"  GET応答受信 ({elapsed:.1f}秒) - status: {response.status_code}", "info")
            
            response.raise_for_status()
            
            if len(response.text) < 500:
                rt_logger.add(f"  GETも短い - 直接アクセスへ", "warning")
                return get_page_content_direct(url, rt_logger)
            
            html = response.text
            rt_logger.add(f"  GET成功", "success")
            
        except Exception as e2:
            rt_logger.add(f"  GET失敗 - 直接アクセスへ", "error")
            return get_page_content_direct(url, rt_logger)
            
    except Exception as e:
        rt_logger.add(f"  POST失敗: {str(e)[:60]} - 直接アクセスへ", "error")
        return get_page_content_direct(url, rt_logger)
    
    # テキスト抽出
    rt_logger.add(f"  HTML解析中...", "info")
    
    try:
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'iframe']):
            tag.decompose()
        
        body_text = soup.body.get_text(separator=' ', strip=True) if soup.body else soup.get_text(separator=' ', strip=True)
        result["content"] = body_text[:18000]
        result["status_code"] = 200
        
        rt_logger.add(f"  抽出完了: {len(result['content'])}文字", "success")
        
    except Exception as e:
        rt_logger.add(f"  解析失敗: {str(e)}", "error")
        result["error"] = str(e)
    
    return result


def search_product_urls_with_brightdata(query: str, api_key: str, rt_logger: RealTimeLogger) -> list:
    """Bright DataのSERP APIでGoogle検索を実行し、URLリストを取得する"""
    rt_logger.add(f"SERP API検索開始: {query}", "info")
    
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
    
    wait_time = random.uniform(1, 2)
    rt_logger.add(f"  待機中 ({wait_time:.1f}秒)...", "info")
    time.sleep(wait_time)
    
    try:
        rt_logger.add(f"  SERP APIリクエスト送信中...", "info")
        start_time = time.time()
        
        initial_response = requests.post(
            'https://api.brightdata.com/serp/req',
            headers=headers,
            json=payload,
            timeout=30
        )
        
        elapsed = time.time() - start_time
        rt_logger.add(f"  初期応答受信 ({elapsed:.1f}秒) - status: {initial_response.status_code}", "info")
        
        initial_response.raise_for_status()
        response_id = initial_response.headers.get('x-response-id')
        
        if not response_id:
            rt_logger.add(f"  response_idなし - スキップ", "warning")
            return []
        
        rt_logger.add(f"  response_id取得: {response_id[:20]}...", "success")
        result_url = f'https://api.brightdata.com/serp/get_result?response_id={response_id}'
        
        for attempt in range(12):  # 15 → 12に短縮
            wait_time = random.uniform(2, 4)
            rt_logger.add(f"  結果取得待機 (試行{attempt+1}/12) - {wait_time:.1f}秒...", "info")
            time.sleep(wait_time)
            
            try:
                result_response = requests.get(result_url, headers=headers, timeout=30)
                rt_logger.add(f"  結果応答 - status: {result_response.status_code}", "info")
                
                if result_response.status_code == 200:
                    if not result_response.text:
                        rt_logger.add(f"  空のレスポンス", "warning")
                        return []
                    
                    rt_logger.add(f"  HTML解析中... (長さ: {len(result_response.text)}文字)", "info")
                    soup = BeautifulSoup(result_response.text, 'html.parser')
                    result_divs = soup.find_all('div', {'data-ved': True}) or soup.find_all('div', class_='g')
                    
                    rt_logger.add(f"  検索結果div数: {len(result_divs)}", "info")
                    
                    urls = []
                    for idx, div in enumerate(result_divs):
                        a_tag = div.find('a', href=True)
                        if a_tag and a_tag.get('href') and a_tag.get('href').startswith('http') and not a_tag.get('href').startswith('https://www.google.'):
                            urls.append(a_tag.get('href'))
                            if idx < 3:
                                rt_logger.add(f"    URL発見: {a_tag.get('href')[:60]}...", "info")
                    
                    unique_urls = list(dict.fromkeys(urls))[:10]
                    rt_logger.add(f"  {len(unique_urls)}件のURL抽出完了", "success")
                    return unique_urls
                    
                elif result_response.status_code == 202:
                    rt_logger.add(f"  まだ処理中 (202) - 再試行", "info")
                else:
                    rt_logger.add(f"  予期しないステータス: {result_response.status_code}", "warning")
                    return []
                    
            except requests.exceptions.RequestException as e:
                rt_logger.add(f"  結果取得エラー (試行{attempt+1}): {str(e)[:50]}", "error")
                return []
        
        rt_logger.add(f"  タイムアウト (12回試行)", "warning")
        return []
        
    except requests.exceptions.RequestException as e:
        rt_logger.add(f"  SERP APIエラー: {str(e)[:60]}", "error")
        return []

# ==============================================================================
# === AIエージェント関連関数 ===
# ==============================================================================

def analyze_page_and_extract_info(page_content_result: dict, product_name: str, gemini_api_key: str, rt_logger: RealTimeLogger, retry_count: int = 2) -> dict | None:
    """HTMLをGemini APIに渡し、製品情報を抽出する"""
    body_text = page_content_result.get("content")
    if page_content_result.get("error") or not body_text:
        rt_logger.add(f"  コンテンツなし - AI解析スキップ", "warning")
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
            rt_logger.add(f"  Gemini API呼び出し (試行{attempt+1}/{retry_count})...", "info")
            start_time = time.time()
            
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
            
            elapsed = time.time() - start_time
            rt_logger.add(f"  Gemini応答受信 ({elapsed:.1f}秒) - status: {response.status_code}", "info")
            
            response.raise_for_status()
            result = response.json()
            
            if not result.get('candidates'):
                rt_logger.add(f"  candidates なし - リトライ", "warning")
                if attempt < retry_count - 1:
                    time.sleep(2)
                    continue
                return None
            
            response_text = result['candidates'][0]['content']['parts'][0]['text']
            rt_logger.add(f"  JSON解析中... (長さ: {len(response_text)}文字)", "info")
            
            raw_data = json.loads(response_text)
            
            # バリデーション
            if raw_data and isinstance(raw_data, dict):
                offers_count = len(raw_data.get("offers", []))
                rt_logger.add(f"  抽出成功: offers {offers_count}件", "success")
                
                if offers_count > 0:
                    return raw_data
                elif attempt < retry_count - 1:
                    rt_logger.add(f"  offers なし - リトライ", "warning")
                    time.sleep(2)
                    continue
            
            return raw_data if isinstance(raw_data, dict) else None
            
        except json.JSONDecodeError as e:
            rt_logger.add(f"  JSON解析エラー: {str(e)[:50]}", "error")
            if attempt < retry_count - 1:
                time.sleep(2)
            else:
                return None
                
        except requests.exceptions.RequestException as e:
            rt_logger.add(f"  API呼び出しエラー: {str(e)[:50]}", "error")
            if attempt < retry_count - 1:
                time.sleep(2)
            else:
                return None
    
    return None

# ==============================================================================
# === 統括エージェント ===
# ==============================================================================

def orchestrator_agent(product_info: dict, gemini_api_key: str, brightdata_api_key: str, brd_username: str, brd_password: str, preferred_sites: list, debug_mode: bool = False) -> tuple[list, list]:
    """一連の処理を統括するエージェント（404対策版）"""
    product_name = product_info['ProductName']
    manufacturer = product_info.get('Manufacturer', '')
    
    st.subheader(f"【統括エージェント】 \"{product_name}\" の情報収集を開始します。")
    
    # リアルタイムログ初期化
    rt_logger = RealTimeLogger()
    rt_logger.add(f"=== 統括エージェント開始 ===", "success")
    rt_logger.add(f"製品名: {product_name}", "info")
    rt_logger.add(f"メーカー: {manufacturer}", "info")

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

    # 進捗バー初期化
    progress_bar = st.progress(0)
    status_text = st.empty()

    # ステップ1: URL抽出 (0-20%)
    status_text.text("⏳ URL抽出中...")
    progress_bar.progress(0.05)
    rt_logger.add(f"--- ステップ1: URL抽出 ({len(search_queries)}件のクエリ) ---", "success")
    
    all_urls = []
    num_queries = len(search_queries)
    
    for i, query in enumerate(search_queries):
        rt_logger.add(f"クエリ {i+1}/{num_queries}: {query}", "info")
        urls = search_product_urls_with_brightdata(query, brightdata_api_key, rt_logger)
        all_urls.extend(urls)
        
        progress = 0.05 + (i / num_queries) * 0.15
        progress_bar.progress(progress)
        status_text.text(f"⏳ URL抽出中... ({i+1}/{num_queries})")
    
    unique_urls = list(dict.fromkeys(all_urls))[:15]
    
    rt_logger.add(f"=== URL抽出完了: {len(unique_urls)}件 ===", "success")
    
    if not unique_urls:
        st.error("❌ 検索結果からURLを取得できませんでした。")
        return [], []
    
    # URL一覧表示
    with st.expander(f"📋 抽出されたURL一覧 ({len(unique_urls)}件)"):
        for idx, url in enumerate(unique_urls):
            st.text(f"{idx+1}. {url}")
    
    progress_bar.progress(0.2)
    status_text.text("✅ URL抽出完了 (20%)")

    # ステップ2: ページ取得 (20-70%)
    status_text.text("⏳ Webページを取得中...")
    rt_logger.add(f"--- ステップ2: ページ取得 ({len(unique_urls)}件) ---", "success")
    
    all_page_content_results = []
    success_count = 0
    fail_count = 0

    for i, url in enumerate(unique_urls):
        status_text.text(f"⏳ ページ取得中 ({i + 1}/{len(unique_urls)})")
        rt_logger.add(f"========== URL {i+1}/{len(unique_urls)} ==========", "info")
        rt_logger.add(f"{url}", "info")
        
        try:
            page_result = get_page_content_with_brightdata(url, brd_username, brd_password, rt_logger, timeout=10)
            all_page_content_results.append(page_result)
            
            if page_result.get('content') and len(page_result.get('content', '')) > 1000:
                success_count += 1
                rt_logger.add(f"✅ 成功カウント: {success_count}", "success")
            else:
                fail_count += 1
                rt_logger.add(f"⚠️ 失敗カウント: {fail_count}", "warning")
        
        except Exception as e:
            fail_count += 1
            rt_logger.add(f"❌ 予期しないエラー: {str(e)}", "error")
            all_page_content_results.append({"url": url, "error": str(e)})
        
        # 進捗更新
        progress = 0.2 + (i + 1) / len(unique_urls) * 0.5
        progress_bar.progress(progress)
        
        # 早期終了判定
        if success_count >= 5 and i >= 8:
            rt_logger.add(f"早期終了: {success_count}件成功", "success")
            break

    rt_logger.add(f"=== ページ取得完了: 成功 {success_count}件 / 失敗 {fail_count}件 ===", "success")
    progress_bar.progress(0.7)
    status_text.text("✅ ページ取得完了 (70%)")

    # ステップ3: AI解析 (70-100%)
    status_text.text("⏳ AIでページを分析中...")
    rt_logger.add(f"--- ステップ3: AI解析 ---", "success")
    
    found_pages_data = []
    successful_contents = [
        res for res in all_page_content_results 
        if res.get("content") and len(res.get("content", "")) > 1000 and not res.get("error")
    ]
    
    rt_logger.add(f"解析対象: {len(successful_contents)}件", "info")
    
    if not successful_contents:
        st.warning("⚠️ 有効なコンテンツが取得できませんでした。")
        progress_bar.progress(1.0)
        status_text.text("⚠️ 完了 (100%) - データなし")
        return [], all_page_content_results
    
    for i, content_res in enumerate(successful_contents):
        status_text.text(f"⏳ AI分析中... ({i + 1}/{len(successful_contents)})")
        rt_logger.add(f"========== AI解析 {i+1}/{len(successful_contents)} ==========", "info")
        rt_logger.add(f"{content_res.get('url')}", "info")
        
        try:
            page_details = analyze_page_and_extract_info(content_res, product_name, gemini_api_key, rt_logger)
            
            if page_details and page_details.get("offers"):
                page_details['sourceUrl'] = content_res.get("url")
                found_pages_data.append(page_details)
                rt_logger.add(f"✅ 製品情報追加: {len(found_pages_data)}件目", "success")
        
        except Exception as e:
            rt_logger.add(f"❌ AI解析エラー: {str(e)}", "error")
        
        progress = 0.7 + (i + 1) / len(successful_contents) * 0.3
        progress_bar.progress(progress)

    progress_bar.progress(1.0)
    status_text.text("✅ 完了 (100%)")
    rt_logger.add(f"=== 統括エージェント完了: {len(found_pages_data)}ページから製品情報を抽出 ===", "success")
    
    return found_pages_data, all_page_content_results

# ==============================================================================
# === Streamlit UI アプリケーション部分 ===
# ==============================================================================

st.set_page_config(layout="wide")
st.title("製品調達AIエージェント 🔬")

st.sidebar.header("⚙️ APIキー設定")
try:
    gemini_api_key = st.secrets["GOOGLE_API_KEY"]
    brightdata_api_key = st.secrets["BRIGHTDATA_API_KEY"]
    brightdata_username = st.secrets["BRIGHTDATA_USERNAME"]
    brightdata_password = st.secrets["BRIGHTDATA_PASSWORD"]
    st.sidebar.success("✅ APIキーと認証情報が設定されています。")
except KeyError as e:
    st.sidebar.error("❌ Streamlit Secretsに必要な情報が設定されていません。")
    logger.error(f"Missing secret key: {str(e)}")
    gemini_api_key, brightdata_api_key, brightdata_username, brightdata_password = "", "", "", ""

st.sidebar.header("🔍 検索条件")
product_name_input = st.sidebar.text_input("製品名 (必須)", placeholder="例: Y27632")
manufacturer_input = st.sidebar.text_input("メーカー", placeholder="例: Selleck")
min_price_input = st.sidebar.number_input("最低価格 (円)", min_value=0, value=0, step=100)
max_price_input = st.sidebar.number_input("最高価格 (円)", min_value=0, value=0, step=100)
debug_mode_checkbox = st.sidebar.checkbox("🔧 デバッグモードを有効にする")
search_button = st.sidebar.button("🚀 検索開始", type="primary")

if search_button:
    if not all([gemini_api_key, brightdata_api_key, brightdata_username, brightdata_password]):
        st.error("❌ APIキーまたは認証情報が設定されていません。")
    elif not product_name_input:
        st.error("❌ 製品名を入力してください。")
    else:
        logger.info(f"Search started - Product: {product_name_input}, Manufacturer: {manufacturer_input}")
        
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
        
        st.success(f"✅ 情報収集完了 - {len(final_results)}件の結果")

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

        # デバッグモード
        if debug_mode_checkbox and log_data:
            st.subheader("🔍 詳細デバッグログ")
            
            for idx, log in enumerate(log_data):
                status = log.get('status_code')
                is_error = log.get('error') is not None
                
                if is_error:
                    st.error(f"❌ 接続エラー: {log['url']}")
                elif status != 200 and status is not None:
                    st.warning(f"⚠️ ステータス異常 ({status}): {log['url']}")
                else:
                    st.success(f"✅ 成功 ({status}): {log['url']}")

                with st.expander(f"詳細 - URL {idx+1}"):
                    if is_error:
                        st.write(f"**エラー:** `{log['error']}`")
                    
                    log_display = log.copy()
                    if log_display.get('content'):
                        content_len = len(log_display['content'])
                        log_display['content'] = (
                            log_display['content'][:1000] + "..."
                            if len(log_display['content']) > 1000
                            else log_display['content']
                        )
                        st.write(f"**Content長:** {content_len}文字")
                    
                    st.json(log_display)

logger.info("Application ready")
