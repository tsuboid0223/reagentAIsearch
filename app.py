import streamlit as st
import google.generativeai as genai
import time
import re
import json
import pandas as pd
from io import StringIO
from datetime import datetime
from playwright.sync_api import sync_playwright
import urllib.parse

# ページ設定
st.set_page_config(
    page_title="化学試薬 価格比較システム（Browser API版）",
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

# Browser API設定
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

def search_google_with_browser(query, logger):
    """Browser API経由でGoogle検索を実行"""
    try:
        logger.log(f"  🔍 Google検索: {query[:60]}...", "DEBUG")
        
        search_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}&num=10&hl=ja"
        
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(BROWSER_API_CONFIG['ws_endpoint'])
            context = browser.contexts[0]
            page = context.new_page()
            
            page.goto(search_url, timeout=30000, wait_until='domcontentloaded')
            time.sleep(2)  # ページ読み込み待機
            
            html_content = page.content()
            
            page.close()
            browser.close()
            
            logger.log(f"  ✅ Google検索成功 (HTML: {len(html_content)} chars)", "DEBUG")
            return html_content
            
    except Exception as e:
        logger.log(f"  ❌ Google検索エラー: {str(e)}", "ERROR")
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

def fetch_page_with_browser(url, logger):
    """Browser API経由でページ取得"""
    try:
        logger.log(f"  🌐 Browser API経由でページ取得", "DEBUG")
        logger.log(f"    URL: {url[:80]}...", "DEBUG")
        
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(BROWSER_API_CONFIG['ws_endpoint'])
            context = browser.contexts[0]
            page = context.new_page()
            
            # ページに移動
            page.goto(url, timeout=45000, wait_until='networkidle')
            time.sleep(2)  # 追加の読み込み待機
            
            # HTMLを取得
            html_content = page.content()
            html_size = len(html_content)
            
            page.close()
            browser.close()
            
            logger.log(f"  ✅ ページ取得成功 (HTML: {html_size} chars)", "INFO")
            
            # HTMLサイズチェック
            if html_size < 1000:
                logger.log(f"  ⚠️ HTMLサイズが異常に小さい ({html_size} chars)", "WARNING")
                return None
            
            return html_content
            
    except Exception as e:
        logger.log(f"  ❌ Browser API取得エラー: {str(e)}", "ERROR")
        return None

def search_with_strategy(product_name, site_info, logger):
    """検索戦略"""
    site_name = site_info["name"]
    domain = site_info["domain"]
    
    logger.log(f"🔍 {site_name} ({domain})を検索中", "INFO")
    
    search_queries = [
        f"{product_name} site:{domain}",
        f"{product_name} price site:{domain}",
        f"{product_name} 価格 site:{domain}",
    ]
    
    all_results = []
    
    for query_idx, query in enumerate(search_queries):
        logger.log(f"  🔎 検索クエリ{query_idx+1}/3: {query}", "DEBUG")
        
        html = search_google_with_browser(query, logger)
        
        if not html:
            time.sleep(2)
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
        
        time.sleep(2)
    
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
   - 「税抜 ¥32,000」→ 32000
   - 価格がない場合は offers を空配列 [] にする

4. 出力形式: 必ずJSON形式で出力してください。

【出力例】
{{
  "productName": "Y-27632 dihydrochloride",
  "modelNumber": "146986-50-7",
  "manufacturer": "Sigma-Aldrich",
  "offers": [
    {{"size": "1mg", "price": 34000, "inStock": true}},
    {{"size": "5mg", "price": 54000, "inStock": true}}
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
        
        # JSONクリーニング
        response_text = re.sub(r'^```json\s*', '', response_text)
        response_text = re.sub(r'^```\s*', '', response_text)
        response_text = re.sub(r'\s*```$', '', response_text)
        response_text = response_text.strip()
        
        # JSONパース
        product_info = json.loads(response_text)
        
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
        
        return product_info
        
    except json.JSONDecodeError as e:
        logger.log(f"  ❌ JSON解析エラー: {str(e)}", "ERROR")
        return None
    except Exception as e:
        logger.log(f"  ❌ 製品情報抽出エラー: {str(e)}", "ERROR")
        return None

def main():
    st.markdown('<h1 class="main-header">🧪 化学試薬 価格比較システム（Browser API版）</h1>', unsafe_allow_html=True)
    
    if BROWSER_API_CONFIG['available']:
        st.markdown(
            '<div class="api-status api-success">✅ Browser API接続: BRIGHT DATA (Zone: scraping_browser1)</div>',
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
    
    if st.button("🚀 検索開始", type="primary", use_container_width=True):
        if not product_name:
            st.warning("⚠️ 製品名を入力してください")
            return
        
        st.markdown("### 📝 処理ログ")
        log_container = st.empty()
        logger = RealTimeLogger(log_container)
        
        start_time = time.time()
        logger.log(f"🚀 処理開始: {product_name}", "INFO")
        logger.log(f"🌐 Browser API: BRIGHT DATA (Zone: scraping_browser1)", "INFO")
        logger.log(f"🎯 対象サイト数: {max_sites}サイト", "INFO")
        
        model = setup_gemini()
        if not model:
            st.error("❌ Gemini APIの設定に失敗しました")
            return
        
        all_products = []
        sites_to_search = dict(list(TARGET_SITES.items())[:max_sites])
        
        for site_idx, (site_key, site_info) in enumerate(sites_to_search.items(), 1):
            logger.log(f"\n--- サイト {site_idx}/{max_sites} ---", "INFO")
            
            search_results = search_with_strategy(product_name, site_info, logger)
            
            if not search_results:
                logger.log(f"⏭️  次のサイトへ", "DEBUG")
                time.sleep(2)
                continue
            
            # 最もスコアが高いURLを使用
            search_results.sort(key=lambda x: x.get('score', 0), reverse=True)
            result = search_results[0]
            
            logger.log(f"🎯 トップURL: {result['url'][:80]}...", "INFO")
            
            # Browser API経由でページ取得
            html_content = fetch_page_with_browser(result['url'], logger)
            
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
                logger.log(f"❌ {result['site']}: ページ取得失敗", "ERROR")
            
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
