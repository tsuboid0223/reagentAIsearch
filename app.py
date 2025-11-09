import streamlit as st
import requests
import google.generativeai as genai
import time
import re
import json
import pandas as pd
from io import StringIO
from datetime import datetime
from playwright.sync_api import sync_playwright
import urllib.parse
from urllib.parse import quote_plus

# ページ設定
st.set_page_config(
    page_title="化学試薬情報収集システム v3.7 （超高速化版）",
    page_icon="🧪",
    layout="wide"
)

# カスタムCSS
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #1f77b4;
        text-align: center;
        margin-bottom: 2rem;
    }
    .api-status {
        padding: 0.5rem 1rem;
        border-radius: 0.3rem;
        margin: 0.5rem 0;
        font-weight: bold;
    }
    .api-success {
        background-color: #d4edda;
        color: #155724;
        border: 1px solid #c3e6cb;
    }
</style>
""", unsafe_allow_html=True)

# 設定定数
SIMILARITY_THRESHOLD = 0.5  # 製品名類似度の閾値
MIN_HTML_SIZE = 5000  # 最小HTMLサイズ（バイト）

# リアルタイムログクラス
class RealTimeLogger:
    def __init__(self, container):
        self.container = container
        self.logs = []
        
    def log(self, message, level="INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] [{level}] {message}"
        self.logs.append(log_entry)
        
        with self.container:
            st.code("\n".join(self.logs[-50:]), language="log")

# Gemini API設定
def setup_gemini():
    try:
        api_key = st.secrets["GOOGLE_API_KEY"]
        genai.configure(api_key=api_key)
        # gemini-2.5-proに変更（最新モデル）
        return genai.GenerativeModel('gemini-2.5-pro')
    except Exception as e:
        st.error(f"❌ Gemini API設定エラー: {str(e)}")
        return None

# SERP API設定（Google検索用）
def check_serp_api_config():
    try:
        if "BRIGHTDATA_API_KEY" in st.secrets:
            return {
                'api_key': st.secrets["BRIGHTDATA_API_KEY"],
                'zone_name': st.secrets.get("BRIGHTDATA_ZONE_NAME", "serp_api1"),
                'available': True
            }
    except:
        pass
    return {'available': False}

# Browser API設定（ページ取得用）
BROWSER_API_CONFIG = {
    'ws_endpoint': 'wss://brd-customer-hl_3c49a4bb-zone-scraping_browser1:lokq2uz6vn5q@brd.superproxy.io:9222',
    'available': True
}

# 対象ECサイトの定義（11サイト）
TARGET_SITES = {
    "cosmobio": {"name": "コスモバイオ", "domain": "cosmobio.co.jp"},
    "funakoshi": {"name": "フナコシ", "domain": "funakoshi.co.jp"},
    "axel": {"name": "AXEL", "domain": "axel.as-1.co.jp"},
    "selleck": {"name": "Selleck", "domain": "selleck.co.jp"},
    "mce": {"name": "MCE", "domain": "medchemexpress.com"},
    "nakarai": {"name": "ナカライ", "domain": "nacalai.co.jp"},
    "fujifilm": {"name": "富士フイルム和光", "domain": "labchem-wako.fujifilm.com"},
    "kanto": {"name": "関東化学", "domain": "kanto.co.jp"},
    "tci": {"name": "TCI", "domain": "tcichemicals.com"},
    "merck": {"name": "Merck", "domain": "merck.com"},
    "wako": {"name": "和光純薬", "domain": "hpc-j.co.jp"}
}

def search_google_with_serp(query, serp_config, logger):
    """SERP API経由でGoogle検索を実行"""
    try:
        logger.log(f"  🔍 SERP API経由でGoogle検索: {query[:60]}...", "DEBUG")
        
        api_url = "https://api.brightdata.com/request"
        search_url = f"https://www.google.com/search?q={quote_plus(query)}&num=10&hl=ja&gl=jp"
        
        headers = {
            'Authorization': f'Bearer {serp_config["api_key"]}',
            'Content-Type': 'application/json'
        }
        
        payload = {
            'zone': serp_config['zone_name'],
            'url': search_url,
            'format': 'raw'
        }
        
        response = requests.post(api_url, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            logger.log(f"  ✅ Google検索成功 (HTML: {len(response.text)} chars)", "DEBUG")
            return response.text
        else:
            logger.log(f"  ⚠️ SERP API HTTP {response.status_code}", "WARNING")
            return None
            
    except Exception as e:
        logger.log(f"  ❌ SERP API検索エラー: {str(e)}", "ERROR")
        return None

def extract_urls_from_html(html_content, domain, logger):
    """HTMLからURLを抽出"""
    urls = []
    
    try:
        patterns = [
            rf'href=["\']?(https?://(?:www\.)?{re.escape(domain)}[^"\'\s>]*)["\']?',
            rf'(https?://(?:www\.)?{re.escape(domain)}[^\s<>"\'()]*)',
        ]
        
        all_urls = set()
        
        for pattern in patterns:
            matches = re.findall(pattern, html_content, re.IGNORECASE)
            
            for match in matches:
                url = match[0] if isinstance(match, tuple) else match
                
                # URLクリーニング
                # Googleトラッキングパラメータを削除
                if '&ved=' in url:
                    url = url.split('&ved=')[0]
                elif '?ved=' in url:
                    url = url.split('?ved=')[0]
                
                # その他のトラッキングパラメータ
                for param in ['&hl=', '?hl=', '&sl=', '&tl=', '&client=']:
                    if param in url:
                        url = url.split(param)[0]
                
                # 末尾の記号削除
                url = url.rstrip('.,;:)"\'')
                
                # 有効性チェック
                if url.startswith('http') and len(url) > 20:
                    exclude_patterns = ['google.com', 'youtube.com', 'translate.google', 'webcache']
                    if not any(ex in url.lower() for ex in exclude_patterns):
                        all_urls.add(url)
        
        logger.log(f"    合計 {len(all_urls)} 件のユニークURL発見", "DEBUG")
        
        # URL品質スコアリング
        scored_urls = []
        for url in all_urls:
            score = 0
            url_lower = url.lower()
            
            if any(kw in url_lower for kw in ['product', 'item', 'detail', 'catalog', 'contents']):
                score += 10
            if re.search(r'\d{3,}', url):
                score += 5
            
            scored_urls.append((url, score))
        
        scored_urls.sort(key=lambda x: x[1], reverse=True)
        
        for url, score in scored_urls[:10]:
            urls.append({
                'url': url,
                'score': score
            })
            logger.log(f"    ✓ URL (スコア:{score}): {url[:80]}...", "DEBUG")
        
        if urls:
            logger.log(f"  ✅ {len(urls)}件のURL抽出成功", "INFO")
        else:
            logger.log(f"  ⚠️ 該当URLなし", "WARNING")
        
        return urls
        
    except Exception as e:
        logger.log(f"  ❌ URL抽出エラー: {str(e)}", "ERROR")
        return []

def clean_url(url):
    """
    URLを徹底的にクリーニング
    - HTMLエンティティのデコード
    - Unicodeエスケープシーケンスのデコード（u0026 → &）
    - トラッキングパラメータの削除
    - URLの正規化とバリデーション
    """
    try:
        import html as html_module
        import re
        
        # 1. HTMLエンティティをデコード（&amp; → &）
        url = html_module.unescape(url)
        
        # 2. URLエンコーディングをデコード（%26 → &）
        url = urllib.parse.unquote(url)
        
        # 3. Unicodeエスケープシーケンスのデコード（TCIタイムアウト問題の原因）
        unicode_escapes = {
            'u0026': '&', '/u0026': '&',
            'u003d': '=', '/u003d': '=',
            'u003f': '?', '/u003f': '?',
            'u0023': '#', '/u0023': '#',
            'u002f': '/', '/u002f': '/',
            'u003a': ':', '/u003a': ':',
            'u002b': '+', '/u002b': '+',
        }
        for escape, char in unicode_escapes.items():
            url = url.replace(escape, char)
        
        # 4. Googleトラッキングパラメータを削除
        tracking_params = ['&ved=', '?ved=', '&hl=', '?hl=', '&sl=', '&tl=', '&client=', '&prev=', '&sa=', '&source=', '&usg=']
        for param in tracking_params:
            if param in url:
                url = url.split(param)[0]
        
        # 5. 末尾の記号を削除
        url = url.rstrip('.,;:)"\'')  
        
        # 6. URLの末尾スラッシュを統一（正規化）
        if url.endswith('/'):
            url = url.rstrip('/')
        
        # 7. 不正な制御文字を削除
        url = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', url)
        
        # 8. URLバリデーション（基本的な形式チェック）
        if not url.startswith(('http://', 'https://')):
            return None
        
        return url
    except Exception as e:
        return url

def detect_404_page(html_content):
    """404エラーページを検出"""
    if not html_content or len(html_content) < 100:
        return False
    
    # HTMLの先頭1000文字をチェック
    html_head = html_content[:1000].lower()
    
    # 404検出パターン
    error_patterns = [
        '404 not found',
        '404 error',
        'page not found',
        'ページが見つかりません',
        'お探しのページは見つかりませんでした',
        'the page you requested was not found',
        '<title>404',
    ]
    
    for pattern in error_patterns:
        if pattern in html_head:
            return True
    
    return False

def fetch_page_with_browser(url, logger):
    """Browser API経由でページ取得（エラー検出強化版）"""
    clean_url_str = clean_url(url)
    if not clean_url_str:
        logger.log(f"  ❌ URLクリーニング失敗", "ERROR")
        return None, None
    
    logger.log(f"  🌐 Browser API経由でページ取得", "DEBUG")
    if url != clean_url_str:
        logger.log(f"    元URL: {url[:80]}...", "DEBUG")
        logger.log(f"    クリーンURL: {clean_url_str[:80]}...", "DEBUG")
    else:
        logger.log(f"    URL: {clean_url_str[:80]}...", "DEBUG")
    
    # 複数戦略でリトライ
    strategies = [
        ('domcontentloaded', 30000),  # 高速化: domcontentloaded優先
        ('load', 45000),  # 60秒 → 45秒に短縮
        ('networkidle', 40000)  # 45秒 → 40秒に短縮
    ]
    
    for wait_type, timeout_ms in strategies:
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(BROWSER_API_CONFIG['ws_endpoint'])
                page = browser.contexts[0].new_page()
                page.goto(clean_url_str, timeout=timeout_ms, wait_until=wait_type)
                
                # JavaScript動的レンダリングの待機（高速化版v3.7）
                time.sleep(1)  # 2秒→1秒に短縮
                
                # v3.7高速化: 価格要素の明示的待機を削除（-15秒×複数回）
                # 理由: ログで「タイムアウト→成功」のパターン多数。不要な待機と判断
                
                html_content = page.content()
                page.close()
                browser.close()
                
                # HTMLサイズ検証（v3.7高速化: 早期失敗検出）
                if len(html_content) < MIN_HTML_SIZE:
                    logger.log(f"  ⚠️ HTML内容が小さすぎる（{len(html_content)} chars < {MIN_HTML_SIZE}）。", "WARNING")
                    # v3.7高速化: 1回目の失敗で即座に諦める（次のURLを試行）
                    if wait_type == 'domcontentloaded':  # 最初の戦略
                        logger.log(f"  🚫 初回試行で失敗。このURLをスキップし次のURLへ", "WARNING")
                        return None, None
                    # 2回目以降は次の戦略を試行
                    continue
                
                # 404エラーページ検出
                if detect_404_page(html_content):
                    logger.log(f"  🚫 404エラーページを検出。URLが無効です。", "ERROR")
                    return None, None
                
                logger.log(f"  ✅ ページ取得成功 [{wait_type}] ({len(html_content)} chars)", "INFO")
                return html_content, clean_url_str  # クリーンURLを返す
                
        except Exception as e:
            if 'Timeout' in str(e):
                logger.log(f"  ⚠️ タイムアウト[{wait_type}]、次戦略試行", "DEBUG")
                continue
            logger.log(f"  ❌ エラー[{wait_type}]: {str(e)[:100]}", "ERROR")
            break
    
    logger.log(f"  ❌ 全戦略失敗", "ERROR")
    return None, None


def search_with_strategy(product_name, site_info, serp_config, logger):
    """検索戦略（SERP API使用）"""
    site_name = site_info["name"]
    domain = site_info["domain"]
    
    logger.log(f"🔍 {site_name} ({domain})を検索中", "INFO")
    
    if not serp_config['available']:
        logger.log(f"  ❌ SERP API未設定", "ERROR")
        return []
    
    search_queries = [
        f"{product_name} site:{domain}",
        f"{product_name} price site:{domain}",
        f"{product_name} 価格 site:{domain}",
    ]
    
    all_results = []
    
    for query_idx, query in enumerate(search_queries):
        logger.log(f"  🔎 検索クエリ{query_idx+1}/3: {query}", "DEBUG")
        
        html = search_google_with_serp(query, serp_config, logger)
        
        if not html:
            time.sleep(1)
            continue
        
        urls = extract_urls_from_html(html, domain, logger)
        
        if urls:
            for url_data in urls[:5]:
                all_results.append({
                    'url': url_data['url'],
                    'site': site_name,
                    'score': url_data.get('score', 0)
                })
            
            logger.log(f"  ✅ {len(urls)}件のURL取得成功", "INFO")
            break
        
        time.sleep(1)
    
    if all_results:
        logger.log(f"✅ {site_name}: {len(all_results)}件のURL取得", "INFO")
    else:
        logger.log(f"❌ {site_name}: URL未発見", "ERROR")
    
    return all_results

def calculate_product_name_similarity(name1, name2):
    """製品名の類似度を簡易計算（0.0〜1.0）"""
    if not name1 or not name2:
        return 0.0
    
    # 正規化（小文字化、スペース削除、ハイフン削除）
    name1_norm = name1.lower().replace(' ', '').replace('-', '').replace('_', '')
    name2_norm = name2.lower().replace(' ', '').replace('-', '').replace('_', '')
    
    # 完全一致
    if name1_norm == name2_norm:
        return 1.0
    
    # 片方が他方を含む
    if name1_norm in name2_norm or name2_norm in name1_norm:
        return 0.8
    
    # 共通文字数の割合
    common_chars = set(name1_norm) & set(name2_norm)
    max_len = max(len(name1_norm), len(name2_norm))
    if max_len > 0:
        return len(common_chars) / max_len
    
    return 0.0

def extract_product_info_from_page(html_content, product_name, url, site_name, model, logger):
    """ページHTMLから製品情報を抽出（フィルタリング強化版）"""
    logger.log(f"  🤖 Gemini AIで製品情報を抽出中...", "DEBUG")
    
    try:
        # v3.7高速化: HTML切り詰めの簡素化（チャンク抽出削除）
        if len(html_content) > 150000:
            logger.log(f"  🔍 HTML解析: {len(html_content)} chars", "DEBUG")
            # 単純に前半150KBを使用（チャンク抽出は削除）
            html_content = html_content[:150000]
            logger.log(f"  ✂️ HTML切り詰め: 150000 chars", "DEBUG")
        else:
            logger.log(f"  📄 HTML全体を使用: {len(html_content)} chars", "DEBUG")
        
        prompt = f"""
あなたは化学試薬のWebサイトからの製品情報抽出エキスパートです。
以下のHTMLから、製品の詳細情報と**特に価格情報**を徹底的に抽出してください。

【重要】価格情報の検索手順:
1. まず、以下のHTMLパターンを探してください:
   - <td>や<span>タグ内の「¥」「円」を含むテキスト
   - class="price"、class="product-price"等の価格関連クラス
   - JavaScriptの変数定義（price:、yen:等）
   - テーブル構造内の価格列
   - 「税込」「税抜」「販売価格」「単価」等のラベルの近く

2. 容量・サイズ情報も同時に抽出:
   - 「1mg」「5mg」「10mg」「100mg」「1g」「5g」等
   - 「1mL」「10mL」「100mL」「1L」等
   - サイズと価格は通常、同じ行や近接した要素にあります

3. 複数の価格がある場合:
   - **全ての価格とサイズの組み合わせを抽出**してください
   - 見つかった価格は1つも漏らさず全て記録してください

【抽出する情報】
- productName: 製品名（化合物名）
- modelNumber: カタログ番号またはCAS番号
- manufacturer: 製造元またはブランド名
- offers: 価格情報のリスト（**重要**: 見つかった価格は全て含める）

【offers配列の各要素】
- size: 容量・サイズ（例: "1mg", "5mg", "10mg", "100g"等）
- price: 価格（数値のみ、カンマなし）
- inStock: 在庫状況（真偽値: true/false、不明な場合はtrue）

【価格フォーマットの例】（これらを全て認識してください）:
- 日本語: "¥34,000", "34,000円", "税込¥32,000", "税抜 ¥30,000"
- 英語: "$340.00", "USD 340", "€300"
- テーブル形式: "1mg | ¥14,800", "5mg | ¥36,100"
- リスト形式: "• 1mg: 14,800円"

【価格抽出の変換規則】
- "¥34,000" → 34000
- "34,000円" → 34000  
- "$340.00" → 340
- "税抜 ¥32,000" → 32000
- カンマ、通貨記号は全て削除し、数値のみにする

【出力形式】必ずJSON形式で出力:
{{
  "productName": "Y-27632 dihydrochloride",
  "modelNumber": "146986-50-7",
  "manufacturer": "Sigma-Aldrich",
  "offers": [
    {{"size": "1mg", "price": 34000, "inStock": true}},
    {{"size": "5mg", "price": 54000, "inStock": true}},
    {{"size": "10mg", "price": 78000, "inStock": true}}
  ]
}}

**注意**: 価格が見つからない場合のみ offers を空配列 [] にしてください。
HTMLに価格情報がある場合は、必ず抽出してください。

【HTMLコンテンツ】
{html_content}

【ソースURL】
{url}

必ずJSON形式のみを返してください。説明文は不要です。
"""
        
        # デバッグ: HTMLに価格情報が含まれているかチェック
        price_indicators = [('¥', 'yen_symbol'), ('円', 'yen_kanji'), ('price', 'price_en'), 
                           ('価格', 'price_ja'), ('税込', 'tax_included'), ('税抜', 'tax_excluded')]
        found_indicators = []
        for indicator, name in price_indicators:
            count = html_content.count(indicator)
            if count > 0:
                found_indicators.append(f"{name}:{count}")
        
        if found_indicators:
            logger.log(f"  🔍 HTML内価格キーワード検出: {', '.join(found_indicators)}", "DEBUG")
        else:
            logger.log(f"  ⚠️ HTML内に価格関連キーワードが見つかりません", "WARNING")
        
        # Gemini API呼び出し（複数回試行）
        max_retries = 2
        best_response = None
        best_response_text = ""
        
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logger.log(f"  🔄 再試行 {attempt+1}/{max_retries}...", "DEBUG")
                
                # 試行回数に応じてgeneration_configを調整
                generation_config = {
                    "temperature": 0.1 + (attempt * 0.2),  # 0.1 -> 0.3
                    "top_p": 0.95,
                    "top_k": 40
                }
                
                response = model.generate_content(prompt, generation_config=generation_config)
                response_text = response.text.strip()
                
                logger.log(f"  📨 Gemini API応答受信 [{attempt+1}] ({len(response_text)} chars)", "DEBUG")
                
                # 有効なレスポンスかチェック（offersが含まれているか）
                if len(response_text) > 200 and '"offers"' in response_text:
                    # 価格が含まれている可能性が高い
                    best_response_text = response_text
                    logger.log(f"  ✅ 有効なレスポンスを取得", "DEBUG")
                    break
                elif len(response_text) > len(best_response_text):
                    # より長いレスポンスを保持
                    best_response_text = response_text
            except Exception as e:
                logger.log(f"  ⚠️ 試行{attempt+1}失敗: {str(e)}", "WARNING")
                continue
        
        response_text = best_response_text
        
        # レスポンスが異常に短い場合は詳細を表示
        if len(response_text) < 200:
            logger.log(f"  ⚠️ Geminiレスポンスが短い: {response_text}", "WARNING")
            # HTMLサンプルを表示（最初の500文字）
            html_sample = html_content[:500].replace('\n', ' ')[:200]
            logger.log(f"  📄 HTMLサンプル: {html_sample}...", "DEBUG")
        
        # JSONクリーニング
        response_text = re.sub(r'^```json\s*', '', response_text)
        response_text = re.sub(r'^```\s*', '', response_text)
        response_text = re.sub(r'\s*```$', '', response_text)
        response_text = response_text.strip()
        
        # JSONパース
        product_info = json.loads(response_text)
        
        # 製品名の類似度チェック（フィルタリング）
        extracted_name = product_info.get('productName', '')
        similarity = calculate_product_name_similarity(product_name, extracted_name)
        logger.log(f"  🔍 製品名類似度: {similarity:.2f} (検索: {product_name} vs 抽出: {extracted_name})", "DEBUG")
        
        # 類似度が閾値未満の場合、結果を破棄
        if similarity < SIMILARITY_THRESHOLD:
            logger.log(f"  🚫 製品名の類似度が閾値未満（{similarity:.2f} < {SIMILARITY_THRESHOLD}）。この結果をスキップします。", "WARNING")
            logger.log(f"  💡 ヒント: 検索結果が正しくない可能性があります。別のURLを試してください。", "INFO")
            return None
        
        # データ型検証
        if 'offers' in product_info and isinstance(product_info['offers'], list):
            valid_offers = []
            for offer in product_info['offers']:
                if 'price' in offer:
                    try:
                        if isinstance(offer['price'], str):
                            price_str = offer['price'].replace(',', '').replace('¥', '').replace('円', '').replace('$', '').replace('€', '').strip()
                            offer['price'] = float(price_str)
                        else:
                            offer['price'] = float(offer['price'])
                        
                        if offer['price'] > 0:
                            valid_offers.append(offer)
                    except:
                        pass
            
            product_info['offers'] = valid_offers
        
        if product_info.get('offers'):
            logger.log(f"  ✅ {len(product_info['offers'])}件の価格情報を抽出", "INFO")
            for i, offer in enumerate(product_info['offers'][:3]):
                logger.log(f"    - {offer.get('size', 'N/A')}: ¥{int(offer.get('price', 0)):,}", "DEBUG")
        else:
            logger.log(f"  ⚠️ 価格情報が見つかりませんでした", "WARNING")
            if found_indicators:
                logger.log(f"  💡 ヒント: HTML内に価格キーワードは存在しますが、Geminiが抽出できませんでした", "WARNING")
        
        return product_info
        
    except json.JSONDecodeError as e:
        logger.log(f"  ❌ JSON解析エラー: {str(e)}", "ERROR")
        logger.log(f"  📄 生レスポンス: {response_text[:500]}", "DEBUG")
        return None
    except Exception as e:
        logger.log(f"  ❌ 製品情報抽出エラー: {str(e)}", "ERROR")
        import traceback
        logger.log(f"  📋 詳細: {traceback.format_exc()[:500]}", "DEBUG")
        return None

def main():
    st.markdown('<h1 class="main-header">🧪 化学試薬情報収集システム v3.7 （超高速化版 + スーパーリンク）</h1>', unsafe_allow_html=True)
    
    serp_config = check_serp_api_config()
    
    if serp_config['available'] and BROWSER_API_CONFIG['available']:
        st.markdown(
            f'<div class="api-status api-success">✅ LLM: Gemini 2.5 Pro | SERP API: {serp_config["zone_name"]} | Browser API: scraping_browser1 | 類似度閾値: {SIMILARITY_THRESHOLD}</div>',
            unsafe_allow_html=True
        )
    else:
        st.markdown(
            '<div class="api-status api-warning">⚠️ API未設定</div>',
            unsafe_allow_html=True
        )
        return
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        product_name = st.text_input(
            "🔍 製品名を入力してください",
            value="Y-27632",
            placeholder="例: Y-27632, DMSO, Trizol, Quinpirole"
        )
    
    with col2:
        max_sites = st.number_input(
            "最大検索サイト数",
            min_value=1,
            max_value=11,
            value=11,
            step=1
        )
    
    st.markdown("---")
    
    if st.button("🚀 検索開始", type="primary", use_container_width=True):
        if not product_name:
            st.warning("⚠️ 製品名を入力してください")
            return
        
        st.markdown("### 📝 処理ログ")
        log_container = st.empty()
        logger = RealTimeLogger(log_container)
        
        start_time = time.time()
        logger.log(f"🚀 処理開始: {product_name}", "INFO")
        logger.log(f"🤖 LLM: Gemini 2.5 Pro", "INFO")
        logger.log(f"🎯 製品名類似度閾値: {SIMILARITY_THRESHOLD}", "INFO")
        logger.log(f"🔍 Google検索: SERP API (Zone: {serp_config['zone_name']})", "INFO")
        logger.log(f"🌐 ページ取得: Browser API (Zone: scraping_browser1)", "INFO")
        logger.log(f"🎯 対象サイト数: {max_sites}サイト", "INFO")
        
        model = setup_gemini()
        if not model:
            st.error("❌ Gemini APIの設定に失敗しました")
            return
        
        all_products = []
        filtered_count = 0  # フィルタリングされた結果の数
        sites_to_search = dict(list(TARGET_SITES.items())[:max_sites])
        
        for site_idx, (site_key, site_info) in enumerate(sites_to_search.items(), 1):
            logger.log(f"\n--- サイト {site_idx}/{max_sites} ---", "INFO")
            
            search_results = search_with_strategy(product_name, site_info, serp_config, logger)
            
            if not search_results:
                logger.log(f"⏭️  次のサイトへ", "DEBUG")
                time.sleep(2)
                continue
            
            # 最もスコアが高いURLを使用
            search_results.sort(key=lambda x: x.get('score', 0), reverse=True)
            result = search_results[0]
            
            logger.log(f"🎯 トップURL: {result['url'][:80]}...", "INFO")
            
            # Browser API経由でページ取得（クリーンURLを取得）
            html_content, clean_url = fetch_page_with_browser(result['url'], logger)
            
            if html_content and clean_url:
                page_info = extract_product_info_from_page(
                    html_content, 
                    product_name, 
                    clean_url,  # クリーンURLを使用
                    result.get('site', 'unknown'),
                    model, 
                    logger
                )
                
                if page_info:
                    page_info['source_site'] = result['site']
                    page_info['source_url'] = clean_url  # クリーンURLを保存
                    all_products.append(page_info)
                    logger.log(f"✅ {result['site']}: 製品情報取得成功", "INFO")
                else:
                    filtered_count += 1
                    logger.log(f"⚠️ {result['site']}: AI解析失敗またはフィルタリング", "WARNING")
            else:
                logger.log(f"❌ {result['site']}: ページ取得失敗", "ERROR")
            
            time.sleep(2)
        
        elapsed_time = time.time() - start_time
        logger.log(f"\n🎉 処理完了: {elapsed_time:.1f}秒", "INFO")
        logger.log(f"📊 取得成功: {len(all_products)}/{max_sites}サイト", "INFO")
        if filtered_count > 0:
            logger.log(f"🚫 フィルタリング除外: {filtered_count}件（類似度 < {SIMILARITY_THRESHOLD}）", "INFO")
        
        st.markdown("---")
        st.markdown("## 📋 検索結果")
        
        if not all_products:
            st.error("❌ 製品情報を抽出できませんでした")
            if filtered_count > 0:
                st.warning(f"⚠️ {filtered_count}件の結果が製品名類似度チェックでフィルタリングされました（閾値: {SIMILARITY_THRESHOLD}）")
            st.info("💡 ヒント: 製品名を変更するか、検索対象サイトを調整してください")
            return
        
        with_price = [p for p in all_products if p.get('offers')]
        without_price = [p for p in all_products if not p.get('offers')]
        
        success_msg = f"✅ {len(all_products)}件の製品情報を取得（価格情報あり: {len(with_price)}件、処理時間: {elapsed_time:.1f}秒）"
        if filtered_count > 0:
            success_msg += f"\n🚫 {filtered_count}件をフィルタリング除外"
        st.success(success_msg)
        
        # テーブル形式で表示
        table_data = []
        for product in all_products:
            base_info = {
                '製品名': product.get('productName', 'N/A'),
                '販売元': product.get('source_site', 'N/A'),
                '型番': product.get('modelNumber', 'N/A') or '',
                'メーカー': product.get('manufacturer', 'N/A'),
                'リンク先': product.get('source_url', 'N/A')
            }
            
            if 'offers' in product and product['offers']:
                for offer in product['offers']:
                    row = base_info.copy()
                    row['容量'] = offer.get('size', 'N/A')
                    
                    try:
                        price = offer.get('price', 0)
                        if isinstance(price, (int, float)) and price > 0:
                            row['価格'] = f"¥{int(price):,}"
                        else:
                            row['価格'] = 'N/A'
                    except:
                        row['価格'] = 'N/A'
                    
                    row['在庫有無'] = '有' if offer.get('inStock') else '無'
                    table_data.append(row)
            else:
                row = base_info.copy()
                row['容量'] = 'N/A'
                row['価格'] = 'N/A'
                row['在庫有無'] = 'N/A'
                table_data.append(row)
        
        if table_data:
            df_display = pd.DataFrame(table_data)
            # 列の順序を明示的に指定
            column_order = ['製品名', '販売元', '型番', 'メーカー', 'リンク先', '容量', '価格', '在庫有無']
            # 存在する列のみを選択
            existing_columns = [col for col in column_order if col in df_display.columns]
            df_display = df_display[existing_columns]
            
            # スーパーリンク対応（v3.6高速化版）
            if 'リンク先' in df_display.columns:
                df_html = df_display.copy()
                df_html['リンク先'] = df_html['リンク先'].apply(
                    lambda x: f'<a href="{x}" target="_blank">🔗 製品ページ</a>' if pd.notna(x) else ''
                )
                st.write(df_html.to_html(escape=False, index=False), unsafe_allow_html=True)
            else:
                st.dataframe(df_display, use_container_width=True, height=600)
        
        # CSV出力
        st.markdown("---")
        st.markdown("## 💾 データエクスポート")
        
        export_data = []
        for product in all_products:
            base_info = {
                '製品名': product.get('productName', 'N/A'),
                '販売元': product.get('source_site', 'N/A'),
                '型番': product.get('modelNumber', 'N/A') or '',
                'メーカー': product.get('manufacturer', 'N/A'),
                'リンク先': product.get('source_url', 'N/A')
            }
            
            if 'offers' in product and product['offers']:
                for offer in product['offers']:
                    row = base_info.copy()
                    row['容量'] = offer.get('size', 'N/A')
                    
                    try:
                        price = offer.get('price', 0)
                        if isinstance(price, (int, float)) and price > 0:
                            row['価格'] = f"¥{int(price):,}"
                        else:
                            row['価格'] = 'N/A'
                    except:
                        row['価格'] = 'N/A'
                    
                    row['在庫有無'] = '有' if offer.get('inStock') else '無'
                    export_data.append(row)
            else:
                row = base_info.copy()
                row['容量'] = 'N/A'
                row['価格'] = 'N/A'
                row['在庫有無'] = 'N/A'
                export_data.append(row)
        
        df = pd.DataFrame(export_data)
        
        # CSV出力の列順序を明示的に指定
        csv_column_order = ['製品名', '販売元', '型番', 'メーカー', 'リンク先', '容量', '価格', '在庫有無']
        existing_csv_columns = [col for col in csv_column_order if col in df.columns]
        df = df[existing_csv_columns]
        
        csv_buffer = StringIO()
        df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
        csv_data = csv_buffer.getvalue()
        
        st.download_button(
            label="📥 CSVダウンロード",
            data=csv_data,
            file_name=f"chemical_prices_{product_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            use_container_width=True
        )

if __name__ == "__main__":
    main()
