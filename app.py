import streamlit as st
import requests
import google.generativeai as genai
import time
import re
import json
import pandas as pd
from urllib.parse import quote_plus, urlparse, unquote
from io import StringIO
from datetime import datetime
import html

# ページ設定
st.set_page_config(
    page_title="化学試薬 価格比較システム（403/404対策版）",
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
    .log-container {
        background-color: #f8f9fa;
        border: 1px solid #dee2e6;
        border-radius: 0.5rem;
        padding: 1rem;
        font-family: monospace;
        font-size: 0.85rem;
        max-height: 400px;
        overflow-y: auto;
        margin: 1rem 0;
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
    .api-warning {
        background-color: #fff3cd;
        color: #856404;
        border: 1px solid #ffeeba;
    }
</style>
""", unsafe_allow_html=True)

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
        return genai.GenerativeModel('gemini-2.0-flash')
    except Exception as e:
        st.error(f"❌ Gemini API設定エラー: {str(e)}")
        return None

# SERP API設定チェック
def check_serp_api_config():
    """SERP API認証情報の確認"""
    try:
        if "BRIGHTDATA_API_KEY" in st.secrets:
            zone_name = st.secrets.get("BRIGHTDATA_ZONE_NAME", "serp_api1")
            return {
                'provider': 'brightdata',
                'auth_type': 'api_key',
                'api_key': st.secrets["BRIGHTDATA_API_KEY"],
                'zone_name': zone_name,
                'available': True
            }
    except:
        pass
    
    return {
        'provider': None,
        'available': False
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

def clean_url(url):
    """URLのクリーニング - HTMLエンティティのデコードとパラメータ削除"""
    try:
        # HTMLエンティティをデコード（&amp; → &）
        url = html.unescape(url)
        
        # URLデコード（%20 → スペース等）
        url = unquote(url)
        
        # URLをパース
        parsed = urlparse(url)
        
        # クエリパラメータを完全に削除
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        
        # 末尾のスラッシュを削除
        clean_url = clean_url.rstrip('/')
        
        # 末尾の記号を削除
        clean_url = clean_url.rstrip('.,;:)"\'')
        
        return clean_url
    except Exception as e:
        return url

def fetch_page_with_brightdata(url, serp_config, logger):
    """Bright Data経由でページを取得（403/404対策）"""
    try:
        # URLクリーニング（重要！）
        clean_url_str = clean_url(url)
        logger.log(f"  🌐 Bright Data経由でページ取得", "DEBUG")
        logger.log(f"    元URL: {url[:80]}...", "DEBUG")
        logger.log(f"    クリーンURL: {clean_url_str[:80]}...", "DEBUG")
        
        api_url = "https://api.brightdata.com/request"
        
        headers = {
            'Authorization': f'Bearer {serp_config["api_key"]}',
            'Content-Type': 'application/json'
        }
        
        payload = {
            'zone': serp_config['zone_name'],
            'url': clean_url_str,  # クリーンなURLを使用
            'format': 'raw'
        }
        
        response = requests.post(api_url, headers=headers, json=payload, timeout=45)
        
        if response.status_code == 200:
            html_size = len(response.text)
            logger.log(f"  ✅ Bright Data応答 (HTML: {html_size} chars)", "INFO")
            
            # HTMLサイズチェック（異常に小さい場合は警告）
            if html_size < 1000:
                logger.log(f"  ⚠️ HTMLサイズが異常に小さい ({html_size} chars)", "WARNING")
                logger.log(f"  レスポンス内容: {response.text[:200]}", "DEBUG")
                return None  # 直接HTTPにフォールバック
            
            return response.text
        else:
            logger.log(f"  ⚠️ Bright Data HTTP {response.status_code}", "WARNING")
            logger.log(f"  レスポンス: {response.text[:200]}", "DEBUG")
            return None
            
    except Exception as e:
        logger.log(f"  ❌ Bright Data取得エラー: {str(e)}", "ERROR")
        return None

def fetch_page_direct(url, logger):
    """直接HTTPリクエストでページ取得（フォールバック）"""
    try:
        # URLクリーニング
        clean_url_str = clean_url(url)
        logger.log(f"  🔄 直接HTTPリクエスト（フォールバック）", "DEBUG")
        logger.log(f"    URL: {clean_url_str[:80]}...", "DEBUG")
        
        # より詳細なヘッダー
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0',
            'Referer': 'https://www.google.com/'
        }
        
        # セッションを使用
        session = requests.Session()
        response = session.get(clean_url_str, headers=headers, timeout=15, allow_redirects=True)
        
        if response.status_code == 200:
            html_size = len(response.text)
            logger.log(f"  ✅ 直接取得成功 (HTML: {html_size} chars)", "INFO")
            
            # HTMLサイズチェック
            if html_size < 1000:
                logger.log(f"  ⚠️ HTMLサイズが異常に小さい ({html_size} chars)", "WARNING")
                logger.log(f"  レスポンス: {response.text[:200]}", "DEBUG")
                return None
            
            return response.text
        else:
            logger.log(f"  ⚠️ HTTP {response.status_code}", "WARNING")
            return None
            
    except Exception as e:
        logger.log(f"  ❌ 直接取得エラー: {str(e)}", "ERROR")
        return None

def search_with_brightdata_serp(query, serp_config, logger):
    """Bright Data SERP APIで検索"""
    try:
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
            logger.log(f"  ✓ SERP API応答成功 (HTML: {len(response.text)} chars)", "DEBUG")
            return {'html': response.text, 'status': 'success'}
        else:
            logger.log(f"  ⚠️ SERP API HTTP {response.status_code}", "WARNING")
            return None
            
    except Exception as e:
        logger.log(f"  ❌ SERP APIエラー: {str(e)}", "ERROR")
        return None

def extract_urls_from_html(html_content, domain, logger):
    """HTMLからURLを抽出（クリーニング強化版）"""
    urls = []
    
    try:
        patterns = [
            rf'href=["\']?(https?://(?:www\.)?{re.escape(domain)}[^"\'\s>]*)["\']?',
            rf'(https?://(?:www\.)?{re.escape(domain)}[^\s<>"\'()]*)',
            rf'location\.href\s*=\s*["\']?(https?://(?:www\.)?{re.escape(domain)}[^"\'\s]*)["\']?',
        ]
        
        all_urls = set()
        
        for pattern_idx, pattern in enumerate(patterns):
            matches = re.findall(pattern, html_content, re.IGNORECASE)
            
            for match in matches:
                url = match[0] if isinstance(match, tuple) else match
                
                # URLクリーニング
                url = clean_url(url)
                
                # 有効性チェック
                if url.startswith('http') and len(url) > 20:
                    exclude_patterns = ['google.com', 'youtube.com', 'facebook.com', 
                                       'twitter.com', 'linkedin.com', '/search', '/tag/', 
                                       'translate.google', 'webcache.googleusercontent']
                    if not any(ex in url.lower() for ex in exclude_patterns):
                        all_urls.add(url)
        
        logger.log(f"    合計 {len(all_urls)} 件のユニークURL発見", "DEBUG")
        
        # URL品質スコアリング
        scored_urls = []
        for url in all_urls:
            score = 0
            url_lower = url.lower()
            
            # 製品ページのキーワード
            if any(kw in url_lower for kw in ['product', 'item', 'goods', 'detail', 'catalog', 'contents']):
                score += 10
            # 数字を含む（製品コード）
            if re.search(r'\d{3,}', url):
                score += 5
            # 短すぎるURLは減点
            if len(url) < 40:
                score -= 5
            
            scored_urls.append((url, score))
        
        # スコアでソート
        scored_urls.sort(key=lambda x: x[1], reverse=True)
        
        # 上位10件を返す
        for url, score in scored_urls[:10]:
            urls.append({
                'url': url,
                'title': '',
                'snippet': '',
                'score': score
            })
            logger.log(f"    ✓ URL: {url[:80]}... (スコア:{score})", "DEBUG")
        
        if urls:
            logger.log(f"  ✅ {len(urls)}件のURL抽出成功", "INFO")
        else:
            logger.log(f"  ⚠️ 該当URLなし", "WARNING")
        
        return urls
        
    except Exception as e:
        logger.log(f"  ❌ URL抽出エラー: {str(e)}", "ERROR")
        return []

def search_with_strategy(product_name, site_info, serp_config, logger):
    """SERP APIを使用した検索戦略"""
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
        f"{product_name} product site:{domain}",
        f"{product_name} カタログ site:{domain}",
    ]
    
    all_results = []
    
    for query_idx, query in enumerate(search_queries):
        logger.log(f"  🔎 検索クエリ{query_idx+1}/5: {query}", "DEBUG")
        
        serp_data = search_with_brightdata_serp(query, serp_config, logger)
        
        if not serp_data:
            time.sleep(1)
            continue
        
        urls = extract_urls_from_html(serp_data['html'], domain, logger)
        
        if urls:
            for url_data in urls[:5]:
                all_results.append({
                    'url': url_data['url'],
                    'site': site_name,
                    'title': url_data.get('title', ''),
                    'snippet': url_data.get('snippet', ''),
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

def extract_product_info_from_page(html_content, product_name, url, model, logger):
    """ページHTMLから製品情報を抽出"""
    logger.log(f"  🤖 Gemini AIで製品情報を抽出中...", "DEBUG")
    
    try:
        html_content = html_content[:100000]
        
        prompt = f"""
あなたは化学試薬の製品情報抽出の専門家です。以下のHTMLから「{product_name}」の製品情報を正確に抽出してください。

【重要な指示】
1. HTMLから以下の情報を抽出:
   - productName: 製品名（文字列）
   - modelNumber: 型番、CAS番号、製品コード（文字列）
   - manufacturer: メーカー名（文字列）
   - offers: 価格情報の配列

2. offers配列の各要素:
   - size: 容量・サイズ（例: "1mg", "5mg", "10mL"）
   - price: 価格（必ず数値型、カンマなし整数または小数）
   - inStock: 在庫状況（真偽値: true/false）

3. 価格の抽出規則:
   - 「¥34,000」→ 34000
   - 「34,000円」→ 34000
   - 「$340.00」→ 340
   - 「€250.00」→ 250
   - 「税抜 ¥32,000」→ 32000
   - 価格がない場合は offers を空配列 [] にする

4. 在庫情報:
   - 「在庫あり」「In Stock」「Available」→ true
   - 「在庫なし」「Out of Stock」「品切れ」→ false
   - 不明な場合は true

5. 出力形式: 必ずJSON形式で出力してください。

【出力例】
{{
  "productName": "Y-27632 dihydrochloride",
  "modelNumber": "146986-50-7",
  "manufacturer": "Sigma-Aldrich",
  "offers": [
    {{"size": "1mg", "price": 34000, "inStock": true}},
    {{"size": "5mg", "price": 54000, "inStock": true}},
    {{"size": "25mg", "price": 164000, "inStock": false}}
  ]
}}

【HTMLコンテンツ】
{html_content}

【ソースURL】
{url}

必ずJSON形式のみを返してください。説明文は不要です。
"""
        
        response = model.generate_content(prompt)
        response_text = response.text.strip()
        
        logger.log(f"  📨 Gemini API応答受信 ({len(response_text)} chars)", "DEBUG")
        
        # JSONブロックのクリーニング
        response_text = re.sub(r'^```json\s*', '', response_text)
        response_text = re.sub(r'^```\s*', '', response_text)
        response_text = re.sub(r'\s*```$', '', response_text)
        response_text = response_text.strip()
        
        # JSONパース
        product_info = json.loads(response_text)
        
        # データ型の検証と修正
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
                        logger.log(f"    ⚠️ 価格変換エラー: {offer.get('price')}", "DEBUG")
            
            product_info['offers'] = valid_offers
        
        if product_info.get('offers'):
            logger.log(f"  ✅ {len(product_info['offers'])}件の価格情報を抽出", "INFO")
            for i, offer in enumerate(product_info['offers'][:3]):
                logger.log(f"    - {offer.get('size', 'N/A')}: ¥{int(offer.get('price', 0)):,}", "DEBUG")
        else:
            logger.log(f"  ⚠️ 価格情報が見つかりませんでした", "WARNING")
        
        return product_info
        
    except json.JSONDecodeError as e:
        logger.log(f"  ❌ JSON解析エラー: {str(e)}", "ERROR")
        logger.log(f"  レスポンス: {response_text[:300]}...", "DEBUG")
        return None
    except Exception as e:
        logger.log(f"  ❌ 製品情報抽出エラー: {str(e)}", "ERROR")
        return None

def main():
    st.markdown('<h1 class="main-header">🧪 化学試薬 価格比較システム（403/404対策版）</h1>', unsafe_allow_html=True)
    
    serp_config = check_serp_api_config()
    
    if serp_config['available']:
        st.markdown(
            f'<div class="api-status api-success">✅ SERP API接続: BRIGHTDATA (Zone: {serp_config["zone_name"]})</div>',
            unsafe_allow_html=True
        )
    else:
        st.markdown(
            '<div class="api-status api-warning">⚠️ SERP API未設定</div>',
            unsafe_allow_html=True
        )
    
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
    
    search_disabled = not serp_config['available']
    
    if st.button("🚀 検索開始", type="primary", use_container_width=True, disabled=search_disabled):
        if not product_name:
            st.warning("⚠️ 製品名を入力してください")
            return
        
        st.markdown("### 📝 処理ログ")
        log_container = st.empty()
        logger = RealTimeLogger(log_container)
        
        start_time = time.time()
        logger.log(f"🚀 処理開始: {product_name}", "INFO")
        logger.log(f"🔌 SERP API: BRIGHTDATA (Zone: {serp_config['zone_name']})", "INFO")
        logger.log(f"🎯 対象サイト数: {max_sites}サイト", "INFO")
        logger.log(f"🛡️ 403/404対策: Bright Data経由ページ取得 + 直接HTTPフォールバック", "INFO")
        
        model = setup_gemini()
        if not model:
            st.error("❌ Gemini APIの設定に失敗しました")
            return
        
        all_products = []
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
            
            # ページ取得戦略: 1. Bright Data経由 → 2. 直接HTTP
            html_content = None
            
            # 戦略1: Bright Data経由で取得
            html_content = fetch_page_with_brightdata(result['url'], serp_config, logger)
            
            # 戦略2: 失敗した場合は直接HTTPリクエスト
            if not html_content:
                logger.log(f"  🔄 フォールバック: 直接HTTPリクエスト", "INFO")
                html_content = fetch_page_direct(result['url'], logger)
            
            if html_content:
                page_info = extract_product_info_from_page(html_content, product_name, result['url'], model, logger)
                
                if page_info:
                    page_info['source_site'] = result['site']
                    page_info['source_url'] = result['url']
                    all_products.append(page_info)
                    logger.log(f"✅ {result['site']}: 製品情報取得成功", "INFO")
                else:
                    logger.log(f"⚠️ {result['site']}: AI解析失敗", "WARNING")
            else:
                logger.log(f"❌ {result['site']}: ページ取得失敗（全戦略失敗）", "ERROR")
            
            time.sleep(2)
        
        elapsed_time = time.time() - start_time
        logger.log(f"\n🎉 処理完了: {elapsed_time:.1f}秒", "INFO")
        logger.log(f"📊 取得成功: {len(all_products)}/{max_sites}サイト", "INFO")
        
        st.markdown("---")
        st.markdown("## 📋 検索結果")
        
        if not all_products:
            st.error("❌ 製品情報を抽出できませんでした")
            st.info("💡 ヒント: 製品名を変更するか、検索対象サイトを調整してください")
            return
        
        with_price = [p for p in all_products if p.get('offers')]
        without_price = [p for p in all_products if not p.get('offers')]
        
        st.success(f"✅ {len(all_products)}件の製品情報を取得（価格情報あり: {len(with_price)}件、処理時間: {elapsed_time:.1f}秒）")
        
        # テーブル形式で表示
        table_data = []
        for product in all_products:
            base_info = {
                '製品名': product.get('productName', 'N/A'),
                '販売元': product.get('source_site', 'N/A'),
                '型番': product.get('modelNumber', 'N/A'),
                'メーカー': product.get('manufacturer', 'N/A')
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
            st.dataframe(df_display, use_container_width=True, height=600)
        
        # CSV出力
        st.markdown("---")
        st.markdown("## 💾 データエクスポート")
        
        export_data = []
        for product in all_products:
            base_info = {
                '製品名': product.get('productName', 'N/A'),
                '型番': product.get('modelNumber', 'N/A'),
                'メーカー': product.get('manufacturer', 'N/A'),
                '販売元': product.get('source_site', 'N/A'),
                'URL': product.get('source_url', 'N/A')
            }
            
            if 'offers' in product and product['offers']:
                for offer in product['offers']:
                    row = base_info.copy()
                    row['サイズ'] = offer.get('size', 'N/A')
                    
                    try:
                        price = offer.get('price', 0)
                        row['価格'] = int(price) if isinstance(price, (int, float)) else 0
                    except:
                        row['価格'] = 0
                    
                    row['在庫'] = '有' if offer.get('inStock') else '無'
                    export_data.append(row)
            else:
                row = base_info.copy()
                row['サイズ'] = 'N/A'
                row['価格'] = 0
                row['在庫'] = 'N/A'
                export_data.append(row)
        
        df = pd.DataFrame(export_data)
        
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
