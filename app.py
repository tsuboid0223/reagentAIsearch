import streamlit as st
import requests
import google.generativeai as genai
import time
import re
import json
import pandas as pd
from urllib.parse import quote_plus, unquote, urlparse
from io import StringIO
from datetime import datetime
import random

# ページ設定
st.set_page_config(
    page_title="化学試薬 価格比較システム",
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
    .product-card {
        background-color: #f8f9fa;
        border: 1px solid #dee2e6;
        border-radius: 0.5rem;
        padding: 1.5rem;
        margin: 1rem 0;
    }
    .product-title {
        font-size: 1.3rem;
        font-weight: bold;
        color: #2c3e50;
        margin-bottom: 0.5rem;
    }
    .product-info {
        font-size: 1rem;
        color: #495057;
        margin: 0.3rem 0;
    }
    .price-row {
        background-color: white;
        padding: 0.8rem;
        margin: 0.3rem 0;
        border-radius: 0.3rem;
        border-left: 4px solid #007bff;
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
</style>
""", unsafe_allow_html=True)

# User-Agent リスト
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
]

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
            st.code("\n".join(self.logs[-30:]), language="log")

# Gemini API設定
def setup_gemini():
    try:
        api_key = st.secrets["GOOGLE_API_KEY"]
        genai.configure(api_key=api_key)
        return genai.GenerativeModel('gemini-2.0-flash')
    except Exception as e:
        st.error(f"❌ Gemini API設定エラー: {str(e)}")
        return None

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

def clean_google_url(url):
    """GoogleリダイレクトURLをクリーンアップ"""
    try:
        # /url?q= 形式の処理
        if '/url?q=' in url or '/url?url=' in url:
            parsed = urlparse(url)
            from urllib.parse import parse_qs
            params = parse_qs(parsed.query)
            if 'q' in params:
                url = params['q'][0]
            elif 'url' in params:
                url = params['url'][0]
        
        # URLデコード
        url = unquote(url)
        
        # 不要なパラメータを除去
        url = url.split('&sa=')[0].split('&ved=')[0]
        
        return url
    except:
        return url

def extract_urls_from_html_improved(html_content, domain):
    """改善されたURL抽出ロジック"""
    
    # 複数のパターンで試行
    patterns = [
        # 標準的なURL
        rf'https?://(?:www\.)?{re.escape(domain)}[^\s<>"\'\)]*',
        # Googleリダイレクト形式
        rf'/url\?q=https?://(?:www\.)?{re.escape(domain)}[^&\s<>"\']*',
        rf'/url\?url=https?://(?:www\.)?{re.escape(domain)}[^&\s<>"\']*',
        # href属性内
        rf'href="(https?://(?:www\.)?{re.escape(domain)}[^"]*)"',
        rf"href='(https?://(?:www\.)?{re.escape(domain)}[^']*)'",
    ]
    
    all_urls = set()
    
    for pattern in patterns:
        matches = re.findall(pattern, html_content, re.IGNORECASE)
        for match in matches:
            # タプルの場合は最初の要素を取得
            url = match[0] if isinstance(match, tuple) else match
            
            # クリーンアップ
            url = clean_google_url(url)
            
            # 有効なURLのみ追加
            if url.startswith('http') and len(url) > 20:
                # 除外パターン
                if not any(x in url.lower() for x in ['google.com', 'youtube.com', 'facebook.com', 'twitter.com']):
                    all_urls.add(url)
    
    # URLの優先順位付け
    priority_keywords = [
        'product', 'detail', 'item', 'price', 'catalog',
        '製品', '商品', '価格', 'カタログ', 'p_view', 'view'
    ]
    
    prioritized = []
    others = []
    
    for url in all_urls:
        if any(keyword in url.lower() for keyword in priority_keywords):
            prioritized.append(url)
        else:
            others.append(url)
    
    result = (prioritized + others)[:10]
    
    return result

def search_with_retry(query, max_retries=3, logger=None):
    """リトライ機能付き検索"""
    
    for retry in range(max_retries):
        try:
            search_url = f"https://www.google.com/search?q={quote_plus(query)}&num=10"
            
            headers = {
                'User-Agent': random.choice(USER_AGENTS),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Referer': 'https://www.google.com/',
            }
            
            response = requests.get(
                search_url,
                headers=headers,
                timeout=15
            )
            
            if response.status_code == 200:
                if logger:
                    logger.log(f"  ✓ 検索成功（試行{retry+1}回目）", "DEBUG")
                return response.text
            elif response.status_code == 429:
                if logger:
                    logger.log(f"  レート制限検出、待機中...", "WARNING")
                wait_time = (retry + 1) * 10
                time.sleep(wait_time)
            else:
                if logger:
                    logger.log(f"  HTTP {response.status_code}、リトライ中...", "WARNING")
                time.sleep(random.uniform(3, 6))
                
        except Exception as e:
            if logger:
                logger.log(f"  検索エラー: {str(e)}", "WARNING")
            if retry < max_retries - 1:
                time.sleep(random.uniform(5, 10))
    
    return None

def search_with_strategy(product_name, site_info, logger):
    """多層戦略で検索（改善版）"""
    site_name = site_info["name"]
    domain = site_info["domain"]
    
    logger.log(f"🔍 {site_name}を検索中", "INFO")
    
    # 検索クエリパターン
    search_queries = [
        f"{product_name} 価格 site:{domain}",
        f"{product_name} site:{domain}",
        f"{product_name} カタログ site:{domain}",
    ]
    
    all_results = []
    
    for query_idx, query in enumerate(search_queries):
        logger.log(f"  検索パターン{query_idx+1}: {query[:60]}...", "DEBUG")
        
        # リトライ機能付き検索
        search_html = search_with_retry(query, max_retries=2, logger=logger)
        
        if not search_html:
            continue
        
        # 改善されたURL抽出
        urls = extract_urls_from_html_improved(search_html, domain)
        
        if urls:
            logger.log(f"  ✓ {len(urls)}件のURL発見", "INFO")
            
            for url in urls[:3]:
                all_results.append({
                    'url': url,
                    'site': site_name,
                    'search_html': search_html,
                    'query': query
                })
            
            # URLが見つかったら次のサイトへ
            break
        
        # レート制限対策
        time.sleep(random.uniform(2, 4))
    
    if all_results:
        logger.log(f"✅ {site_name}: {len(all_results)}件のURL取得", "INFO")
    else:
        logger.log(f"⚠️ {site_name}: URL未発見", "WARNING")
    
    return all_results

def extract_price_from_search_snippet(search_html, product_name, model, logger):
    """検索結果スニペットから価格情報を抽出"""
    logger.log(f"  💡 スニペットから価格抽出中", "DEBUG")
    
    try:
        search_html = search_html[:30000]
        
        prompt = f"""
以下はGoogle検索結果のHTMLです。化学試薬「{product_name}」に関する価格情報をスニペットから抽出してください。

【抽出する情報】
1. productName: 製品名
2. modelNumber: 型番・カタログ番号（あれば）
3. manufacturer: メーカー名（あれば）
4. offers: 価格情報のリスト
   - size: 容量・サイズ
   - price: 価格（数値のみ）
   - inStock: 在庫状況（不明な場合はtrue）

【重要】
- スニペットやタイトルに価格情報（¥、円、$、price）が含まれている場合は必ず抽出
- 型番と価格がセットで表示されている場合は対応付けて抽出
- 価格情報がない場合はoffersを空配列に

【出力形式】
JSON形式のみ。マークダウン不要。

検索結果HTML:
{search_html}
"""
        
        response = model.generate_content(prompt)
        response_text = response.text.strip()
        
        # クリーンアップ
        response_text = re.sub(r'^```json\s*', '', response_text)
        response_text = re.sub(r'^```\s*', '', response_text)
        response_text = re.sub(r'\s*```$', '', response_text)
        response_text = response_text.strip()
        
        price_info = json.loads(response_text)
        
        if price_info.get('offers'):
            logger.log(f"  ✅ スニペットから{len(price_info['offers'])}件の価格抽出", "INFO")
            return price_info
        else:
            logger.log(f"  ℹ️ スニペットに価格情報なし", "DEBUG")
            return None
            
    except Exception as e:
        logger.log(f"  スニペット解析エラー: {str(e)}", "DEBUG")
        return None

def fetch_page_content(url, logger):
    """ページコンテンツを取得"""
    try:
        headers = {
            'User-Agent': random.choice(USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.8',
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            return response.text
    except:
        pass
    
    return None

def extract_product_info_from_page(html_content, product_name, model, logger):
    """ページHTMLから製品情報を抽出"""
    logger.log(f"  🤖 ページ内容をAI分析中", "DEBUG")
    
    try:
        html_content = html_content[:50000]
        
        prompt = f"""
以下のHTMLから化学試薬「{product_name}」の製品情報を抽出してください。

【抽出情報】
1. productName: 製品名
2. modelNumber: 型番
3. manufacturer: メーカー名
4. offers: 価格情報
   - size, price, inStock

JSON形式で出力。

HTMLコンテンツ:
{html_content}
"""
        
        response = model.generate_content(prompt)
        response_text = response.text.strip()
        
        response_text = re.sub(r'^```json\s*', '', response_text)
        response_text = re.sub(r'^```\s*', '', response_text)
        response_text = re.sub(r'\s*```$', '', response_text)
        response_text = response_text.strip()
        
        product_info = json.loads(response_text)
        
        if product_info.get('offers'):
            logger.log(f"  ✅ ページから{len(product_info['offers'])}件の価格抽出", "INFO")
        else:
            logger.log(f"  ℹ️ ページに価格情報なし", "DEBUG")
        
        return product_info
        
    except Exception as e:
        logger.log(f"  ページ解析エラー: {str(e)}", "DEBUG")
        return None

def merge_product_info(snippet_info, page_info):
    """情報をマージ"""
    if not snippet_info and not page_info:
        return None
    
    if not snippet_info:
        return page_info
    
    if not page_info:
        return snippet_info
    
    # 価格情報が多い方をベースに
    if len(snippet_info.get('offers', [])) >= len(page_info.get('offers', [])):
        merged = snippet_info.copy()
        if not merged.get('productName'):
            merged['productName'] = page_info.get('productName')
        if not merged.get('modelNumber'):
            merged['modelNumber'] = page_info.get('modelNumber')
        if not merged.get('manufacturer'):
            merged['manufacturer'] = page_info.get('manufacturer')
    else:
        merged = page_info.copy()
        if not merged.get('productName'):
            merged['productName'] = snippet_info.get('productName')
        if not merged.get('modelNumber'):
            merged['modelNumber'] = snippet_info.get('modelNumber')
        if not merged.get('manufacturer'):
            merged['manufacturer'] = snippet_info.get('manufacturer')
    
    return merged

def display_product_card(product, idx):
    """製品情報を表示"""
    st.markdown(f'<div class="product-card">', unsafe_allow_html=True)
    
    product_name = product.get('productName', '製品名不明')
    site_name = product.get('source_site', '不明')
    st.markdown(f'<div class="product-title">📦 {product_name}</div>', unsafe_allow_html=True)
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.markdown(f'<div class="product-info"><strong>販売元:</strong> {site_name}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="product-info"><strong>型番:</strong> {product.get("modelNumber", "N/A")}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="product-info"><strong>メーカー:</strong> {product.get("manufacturer", "N/A")}</div>', unsafe_allow_html=True)
    
    with col2:
        source_url = product.get('source_url', '#')
        st.markdown(f'<div class="product-info"><strong>URL:</strong> <a href="{source_url}" target="_blank">製品ページを開く</a></div>', unsafe_allow_html=True)
    
    if 'offers' in product and product['offers']:
        st.markdown("**💰 価格情報:**")
        
        for offer in product['offers']:
            size = offer.get('size', 'N/A')
            price = offer.get('price', 0)
            price_str = f"¥{price:,}" if price else 'N/A'
            stock = offer.get('inStock', False)
            stock_icon = "✅" if stock else "❌"
            stock_text = "在庫あり" if stock else "在庫なし"
            
            st.markdown(
                f'<div class="price-row">'
                f'<strong>{size}</strong>: {price_str} {stock_icon} {stock_text}'
                f'</div>',
                unsafe_allow_html=True
            )
    else:
        st.warning("⚠️ 価格情報が取得できませんでした")
    
    st.markdown('</div>', unsafe_allow_html=True)

def main():
    st.markdown('<h1 class="main-header">🧪 化学試薬 価格比較システム（修正版）</h1>', unsafe_allow_html=True)
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        product_name = st.text_input(
            "🔍 製品名を入力してください",
            value="Quinpirole",
            placeholder="例: Y-27632, DMSO, Trizol, Quinpirole"
        )
    
    with col2:
        max_sites = st.number_input(
            "最大検索サイト数",
            min_value=1,
            max_value=11,
            value=5,
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
        logger.log(f"📊 改善: URL抽出ロジック強化、リトライ機能追加", "INFO")
        
        model = setup_gemini()
        if not model:
            st.error("❌ Gemini APIの設定に失敗しました")
            return
        
        all_products = []
        sites_to_search = dict(list(TARGET_SITES.items())[:max_sites])
        
        for site_key, site_info in sites_to_search.items():
            search_results = search_with_strategy(product_name, site_info, logger)
            
            if not search_results:
                time.sleep(random.uniform(2, 4))
                continue
            
            result = search_results[0]
            
            # スニペット分析
            snippet_info = extract_price_from_search_snippet(
                result['search_html'],
                product_name,
                model,
                logger
            )
            
            # ページ分析
            page_info = None
            html_content = fetch_page_content(result['url'], logger)
            if html_content:
                page_info = extract_product_info_from_page(html_content, product_name, model, logger)
            
            # マージ
            merged_info = merge_product_info(snippet_info, page_info)
            
            if merged_info:
                merged_info['source_site'] = result['site']
                merged_info['source_url'] = result['url']
                all_products.append(merged_info)
                logger.log(f"✅ {result['site']}: 製品情報取得成功", "INFO")
            else:
                logger.log(f"⚠️ {result['site']}: 製品情報取得失敗", "WARNING")
            
            time.sleep(random.uniform(3, 5))
        
        elapsed_time = time.time() - start_time
        logger.log(f"🎉 処理完了: {elapsed_time:.1f}秒", "INFO")
        
        st.markdown("---")
        st.markdown("## 📋 検索結果")
        
        if not all_products:
            st.warning("⚠️ 製品情報を抽出できませんでした")
            return
        
        with_price = [p for p in all_products if p.get('offers')]
        without_price = [p for p in all_products if not p.get('offers')]
        
        st.success(f"✅ {len(all_products)}件の製品情報を取得（価格情報あり: {len(with_price)}件、処理時間: {elapsed_time:.1f}秒）")
        
        for idx, product in enumerate(with_price + without_price):
            display_product_card(product, idx)
        
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
                    row['価格'] = offer.get('price', 0)
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
