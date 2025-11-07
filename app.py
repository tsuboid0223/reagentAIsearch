import streamlit as st
import requests
import google.generativeai as genai
import time
import re
import json
import pandas as pd
from urllib.parse import quote_plus, urlparse
from io import StringIO
from datetime import datetime

# ページ設定
st.set_page_config(
    page_title="化学試薬 価格比較システム（本番版）",
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
    .site-badge {
        display: inline-block;
        padding: 0.25rem 0.75rem;
        margin: 0.25rem;
        border-radius: 1rem;
        background-color: #e3f2fd;
        color: #1565c0;
        font-size: 0.85rem;
    }
    .success-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #d4edda;
        border: 1px solid #c3e6cb;
        margin: 1rem 0;
    }
    .warning-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #fff3cd;
        border: 1px solid #ffeeba;
        margin: 1rem 0;
    }
    .error-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #f8d7da;
        border: 1px solid #f5c6cb;
        margin: 1rem 0;
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
    }
</style>
""", unsafe_allow_html=True)

# リアルタイムログクラス
class RealTimeLogger:
    def __init__(self, container, show_debug=True):
        self.container = container
        self.logs = []
        self.show_debug = show_debug
        
    def log(self, message, level="INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] [{level}] {message}"
        self.logs.append(log_entry)
        
        if self.show_debug:
            with self.container:
                st.code("\n".join(self.logs[-20:]), language="log")  # 最新20件を表示

# Gemini API設定
def setup_gemini():
    try:
        api_key = st.secrets["GOOGLE_API_KEY"]
        genai.configure(api_key=api_key)
        return genai.GenerativeModel('gemini-2.0-flash')
    except Exception as e:
        st.error(f"❌ Gemini API設定エラー: {str(e)}")
        return None

# Bright Data設定
def get_brightdata_config():
    try:
        return {
            'api_key': st.secrets["BRIGHTDATA_API_KEY"],
            'username': st.secrets["BRIGHTDATA_USERNAME"],
            'password': st.secrets["BRIGHTDATA_PASSWORD"]
        }
    except Exception as e:
        st.error(f"❌ Bright Data設定エラー: {str(e)}")
        return None

# 対象ECサイトの定義（11サイト）
TARGET_SITES = {
    "cosmobio": {"name": "コスモバイオ", "domain": "cosmobio.co.jp", "enabled": True},
    "funakoshi": {"name": "フナコシ", "domain": "funakoshi.co.jp", "enabled": True},
    "axel": {"name": "AXEL", "domain": "axel.as-1.co.jp", "enabled": True},
    "selleck": {"name": "Selleck", "domain": "selleck.co.jp", "enabled": True},
    "mce": {"name": "MCE", "domain": "medchemexpress.com", "enabled": True},
    "nakarai": {"name": "ナカライ", "domain": "nacalai.co.jp", "enabled": True},
    "fujifilm": {"name": "富士フイルム和光", "domain": "labchem-wako.fujifilm.com", "enabled": True},
    "kanto": {"name": "関東化学", "domain": "kanto.co.jp", "enabled": True},
    "tci": {"name": "TCI", "domain": "tcichemicals.com", "enabled": True},
    "merck": {"name": "Merck", "domain": "merck.com", "enabled": True},
    "wako": {"name": "和光純薬", "domain": "hpc-j.co.jp", "enabled": True}
}

# フォールバックURL（Y27632用）
FALLBACK_URLS = {
    "Y-27632": {
        "cosmobio": "https://www.cosmobio.co.jp/product/detail/y-27632-dihydrochloride-alx-270-333.asp",
        "funakoshi": "https://www.funakoshi.co.jp/contents/4567",
        "axel": "https://www.axel.as-1.co.jp/asone/d/62-3817-51/",
    }
}

def validate_url(url, logger):
    """URLが有効かチェック（404を除外）"""
    try:
        logger.log(f"URL検証中: {url}", "DEBUG")
        response = requests.head(url, timeout=5, allow_redirects=True)
        if response.status_code == 404:
            logger.log(f"404エラー: {url}", "WARNING")
            return False
        logger.log(f"有効なURL: {url} (status: {response.status_code})", "DEBUG")
        return True
    except Exception as e:
        logger.log(f"URL検証失敗: {url} - {str(e)}", "WARNING")
        return False

def search_product_urls(product_name, sites, logger, max_urls=10):
    """Google検索で製品URLを取得（Bright Data経由 + 直接アクセスフォールバック）"""
    logger.log(f"製品URL検索開始: {product_name} (最大{max_urls}件)", "INFO")
    
    all_urls = []
    config = get_brightdata_config()
    
    for site_key, site_info in sites.items():
        if not site_info["enabled"]:
            continue
            
        site_name = site_info["name"]
        domain = site_info["domain"]
        
        logger.log(f"検索中: {site_name} ({domain})", "INFO")
        
        # フォールバックURLの確認
        if product_name in FALLBACK_URLS and site_key in FALLBACK_URLS[product_name]:
            fallback_url = FALLBACK_URLS[product_name][site_key]
            if validate_url(fallback_url, logger):
                logger.log(f"✓ フォールバックURL使用: {fallback_url}", "INFO")
                all_urls.append({
                    'url': fallback_url,
                    'site': site_name,
                    'title': f"{product_name} - {site_name}"
                })
                if len(all_urls) >= max_urls:
                    return all_urls
                continue
        
        # Google検索クエリ
        query = f"{product_name} site:{domain}"
        search_url = f"https://www.google.com/search?q={quote_plus(query)}&num=3"
        
        # Bright Data経由で試行（POSTメソッド）
        urls_found = False
        if config:
            try:
                logger.log(f"Bright Data POST経由でアクセス試行...", "DEBUG")
                proxy_url = "http://brd.superproxy.io:33335"
                proxies = {
                    'http': f"http://{config['username']}:{config['password']}@brd.superproxy.io:33335",
                    'https': f"http://{config['username']}:{config['password']}@brd.superproxy.io:33335"
                }
                
                response = requests.post(
                    search_url,
                    proxies=proxies,
                    timeout=10,
                    headers={'User-Agent': 'Mozilla/5.0'}
                )
                
                if response.status_code == 200:
                    urls = extract_urls_from_html(response.text, domain)
                    if urls:
                        logger.log(f"✓ {len(urls)}件のURLを発見（Bright Data POST）", "INFO")
                        for url in urls[:2]:  # サイトごとに最大2件
                            if validate_url(url, logger):
                                all_urls.append({
                                    'url': url,
                                    'site': site_name,
                                    'title': f"{product_name} - {site_name}"
                                })
                                if len(all_urls) >= max_urls:
                                    return all_urls
                        urls_found = True
            except Exception as e:
                logger.log(f"Bright Data POST失敗: {str(e)}", "WARNING")
        
        # Bright Data GETメソッドで試行
        if not urls_found and config:
            try:
                logger.log(f"Bright Data GET経由でアクセス試行...", "DEBUG")
                response = requests.get(
                    search_url,
                    proxies=proxies,
                    timeout=10,
                    headers={'User-Agent': 'Mozilla/5.0'}
                )
                
                if response.status_code == 200:
                    urls = extract_urls_from_html(response.text, domain)
                    if urls:
                        logger.log(f"✓ {len(urls)}件のURLを発見（Bright Data GET）", "INFO")
                        for url in urls[:2]:
                            if validate_url(url, logger):
                                all_urls.append({
                                    'url': url,
                                    'site': site_name,
                                    'title': f"{product_name} - {site_name}"
                                })
                                if len(all_urls) >= max_urls:
                                    return all_urls
                        urls_found = True
            except Exception as e:
                logger.log(f"Bright Data GET失敗: {str(e)}", "WARNING")
        
        # 直接アクセス（フォールバック）
        if not urls_found:
            try:
                logger.log(f"直接アクセス試行...", "DEBUG")
                response = requests.get(
                    search_url,
                    timeout=10,
                    headers={'User-Agent': 'Mozilla/5.0'}
                )
                
                if response.status_code == 200:
                    urls = extract_urls_from_html(response.text, domain)
                    if urls:
                        logger.log(f"✓ {len(urls)}件のURLを発見（直接アクセス）", "INFO")
                        for url in urls[:2]:
                            if validate_url(url, logger):
                                all_urls.append({
                                    'url': url,
                                    'site': site_name,
                                    'title': f"{product_name} - {site_name}"
                                })
                                if len(all_urls) >= max_urls:
                                    return all_urls
            except Exception as e:
                logger.log(f"直接アクセス失敗: {str(e)}", "WARNING")
        
        time.sleep(2)  # レート制限対策
    
    logger.log(f"検索完了: {len(all_urls)}件のURL取得", "INFO")
    return all_urls

def extract_urls_from_html(html_content, domain):
    """HTML から指定ドメインのURLを抽出"""
    pattern = rf'https?://[^"\s]*{re.escape(domain)}[^"\s]*'
    urls = re.findall(pattern, html_content)
    # 重複除去とクリーニング
    clean_urls = []
    seen = set()
    for url in urls:
        # クエリパラメータを除去
        clean_url = url.split('&')[0].split('#')[0]
        if clean_url not in seen and len(clean_url) > 20:  # 短すぎるURLを除外
            seen.add(clean_url)
            clean_urls.append(clean_url)
    return clean_urls[:5]  # 最大5件

def fetch_page_content(url, logger):
    """ページコンテンツを取得（Bright Data経由 + 直接アクセスフォールバック）"""
    logger.log(f"ページ取得中: {url}", "DEBUG")
    
    config = get_brightdata_config()
    
    # Bright Data経由で試行（POSTメソッド）
    if config:
        try:
            logger.log(f"Bright Data POST経由でページ取得試行...", "DEBUG")
            proxies = {
                'http': f"http://{config['username']}:{config['password']}@brd.superproxy.io:33335",
                'https': f"http://{config['username']}:{config['password']}@brd.superproxy.io:33335"
            }
            
            response = requests.post(
                url,
                proxies=proxies,
                timeout=10,
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            
            if response.status_code == 200:
                logger.log(f"✓ ページ取得成功（Bright Data POST）", "DEBUG")
                return response.text
        except Exception as e:
            logger.log(f"Bright Data POST失敗: {str(e)}", "WARNING")
    
    # Bright Data GET メソッドで試行
    if config:
        try:
            logger.log(f"Bright Data GET経由でページ取得試行...", "DEBUG")
            response = requests.get(
                url,
                proxies=proxies,
                timeout=10,
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            
            if response.status_code == 200:
                logger.log(f"✓ ページ取得成功（Bright Data GET）", "DEBUG")
                return response.text
        except Exception as e:
            logger.log(f"Bright Data GET失敗: {str(e)}", "WARNING")
    
    # 直接アクセス（フォールバック）
    try:
        logger.log(f"直接アクセスでページ取得試行...", "DEBUG")
        response = requests.get(
            url,
            timeout=10,
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        
        if response.status_code == 200:
            logger.log(f"✓ ページ取得成功（直接アクセス）", "DEBUG")
            return response.text
    except Exception as e:
        logger.log(f"直接アクセス失敗: {str(e)}", "ERROR")
    
    return None

def extract_product_info_with_gemini(html_content, product_name, model, logger):
    """Gemini APIで製品情報を抽出"""
    logger.log(f"Gemini APIで情報抽出中...", "DEBUG")
    
    try:
        # HTMLを最初の50000文字に制限
        html_content = html_content[:50000]
        
        prompt = f"""
以下のHTMLコンテンツから、化学試薬「{product_name}」の製品情報を抽出してください。

【抽出する情報】
1. productName: 製品名（正式名称）
2. modelNumber: 型番・カタログ番号
3. manufacturer: メーカー名
4. offers: 価格情報のリスト
   - size: 容量・サイズ
   - price: 価格（数値のみ、カンマなし）
   - inStock: 在庫状況（true/false）

【出力形式】
必ずJSON形式で出力してください。マークダウンのコードブロック（```json）は使用しないでください。

{{
  "productName": "製品名",
  "modelNumber": "型番",
  "manufacturer": "メーカー名",
  "offers": [
    {{"size": "1 MG", "price": 34000, "inStock": true}},
    {{"size": "5 MG", "price": 130800, "inStock": true}}
  ]
}}

HTMLコンテンツ:
{html_content}
"""
        
        response = model.generate_content(prompt)
        logger.log(f"✓ Gemini API応答受信", "DEBUG")
        
        # レスポンステキストを取得
        response_text = response.text.strip()
        
        # マークダウンコードブロックを除去
        response_text = re.sub(r'^```json\s*', '', response_text)
        response_text = re.sub(r'^```\s*', '', response_text)
        response_text = re.sub(r'\s*```$', '', response_text)
        response_text = response_text.strip()
        
        # JSON解析
        product_info = json.loads(response_text)
        logger.log(f"✓ 製品情報抽出成功", "INFO")
        return product_info
        
    except json.JSONDecodeError as e:
        logger.log(f"JSON解析エラー: {str(e)}", "ERROR")
        logger.log(f"レスポンス: {response_text[:500]}", "DEBUG")
        return None
    except Exception as e:
        logger.log(f"情報抽出エラー: {str(e)}", "ERROR")
        return None

def main():
    st.markdown('<h1 class="main-header">🧪 化学試薬 価格比較システム（本番版）</h1>', unsafe_allow_html=True)
    
    # 対象サイト表示
    st.markdown("### 📊 対象ECサイト（11サイト）")
    cols = st.columns(4)
    for idx, (key, site) in enumerate(TARGET_SITES.items()):
        with cols[idx % 4]:
            st.markdown(f'<span class="site-badge">{site["name"]}</span>', unsafe_allow_html=True)
    
    st.markdown("---")
    
    # 設定セクション
    col1, col2 = st.columns([3, 1])
    
    with col1:
        product_name = st.text_input(
            "🔍 製品名を入力してください",
            value="Y-27632",
            placeholder="例: Y-27632, DMSO, Trizol"
        )
    
    with col2:
        max_urls = st.number_input(
            "最大取得URL数",
            min_value=1,
            max_value=20,
            value=10,
            step=1
        )
    
    # デバッグログ表示モード
    show_debug = st.checkbox("🐛 デバッグログを表示", value=False)
    
    st.markdown("---")
    
    # 検索ボタン
    if st.button("🚀 検索開始", type="primary", use_container_width=True):
        if not product_name:
            st.warning("⚠️ 製品名を入力してください")
            return
        
        # ログコンテナ
        log_container = st.empty()
        logger = RealTimeLogger(log_container, show_debug=show_debug)
        
        # 処理開始
        start_time = time.time()
        logger.log(f"処理開始: {product_name}", "INFO")
        
        # Gemini API設定
        model = setup_gemini()
        if not model:
            st.error("❌ Gemini APIの設定に失敗しました")
            return
        
        # 進行状況
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # URL検索
        status_text.text("🔎 製品URLを検索中...")
        progress_bar.progress(20)
        
        urls = search_product_urls(product_name, TARGET_SITES, logger, max_urls=max_urls)
        
        if not urls:
            st.error("❌ 製品URLが見つかりませんでした")
            logger.log("検索結果なし", "ERROR")
            return
        
        st.success(f"✅ {len(urls)}件のURLを発見しました")
        
        # 製品情報抽出
        status_text.text("📊 製品情報を抽出中...")
        progress_bar.progress(40)
        
        all_products = []
        
        for idx, url_info in enumerate(urls):
            logger.log(f"処理中 ({idx+1}/{len(urls)}): {url_info['site']}", "INFO")
            
            # ページ取得
            html_content = fetch_page_content(url_info['url'], logger)
            
            if not html_content:
                logger.log(f"ページ取得失敗: {url_info['url']}", "WARNING")
                continue
            
            # 情報抽出
            product_info = extract_product_info_with_gemini(
                html_content, 
                product_name, 
                model, 
                logger
            )
            
            if product_info:
                product_info['source_site'] = url_info['site']
                product_info['source_url'] = url_info['url']
                all_products.append(product_info)
                logger.log(f"✓ {url_info['site']}から情報抽出成功", "INFO")
            
            # 進捗更新
            progress = 40 + int((idx + 1) / len(urls) * 50)
            progress_bar.progress(progress)
            
            time.sleep(1)  # レート制限対策
        
        progress_bar.progress(100)
        status_text.text("✅ 処理完了")
        
        # 実行時間
        elapsed_time = time.time() - start_time
        logger.log(f"処理完了: {elapsed_time:.1f}秒", "INFO")
        
        # 結果表示
        st.markdown("---")
        st.markdown("## 📋 検索結果")
        
        if not all_products:
            st.warning("⚠️ 製品情報を抽出できませんでした")
            return
        
        st.success(f"✅ {len(all_products)}件の製品情報を取得しました")
        
        # 製品情報表示
        for idx, product in enumerate(all_products):
            with st.expander(f"📦 {product.get('productName', '不明')} - {product.get('source_site', '不明')}", expanded=True):
                col1, col2 = st.columns([2, 1])
                
                with col1:
                    st.markdown(f"**製品名:** {product.get('productName', 'N/A')}")
                    st.markdown(f"**型番:** {product.get('modelNumber', 'N/A')}")
                    st.markdown(f"**メーカー:** {product.get('manufacturer', 'N/A')}")
                    st.markdown(f"**サイト:** {product.get('source_site', 'N/A')}")
                    st.markdown(f"**URL:** [{product.get('source_url', 'N/A')}]({product.get('source_url', '#')})")
                
                with col2:
                    if 'offers' in product and product['offers']:
                        st.markdown("**価格情報:**")
                        for offer in product['offers']:
                            price_str = f"¥{offer.get('price', 0):,}" if offer.get('price') else 'N/A'
                            stock_str = "✅ 在庫あり" if offer.get('inStock') else "❌ 在庫なし"
                            st.markdown(f"- {offer.get('size', 'N/A')}: {price_str} ({stock_str})")
        
        # CSV出力
        st.markdown("---")
        st.markdown("## 💾 データエクスポート")
        
        # DataFrameに変換
        export_data = []
        for product in all_products:
            base_info = {
                '製品名': product.get('productName', 'N/A'),
                '型番': product.get('modelNumber', 'N/A'),
                'メーカー': product.get('manufacturer', 'N/A'),
                'サイト': product.get('source_site', 'N/A'),
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
        
        # CSV生成
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
        
        # 統計情報
        st.markdown("---")
        st.markdown("## 📈 統計情報")
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("取得サイト数", len(all_products))
        
        with col2:
            total_offers = sum(len(p.get('offers', [])) for p in all_products)
            st.metric("価格情報数", total_offers)
        
        with col3:
            st.metric("処理時間", f"{elapsed_time:.1f}秒")
        
        with col4:
            st.metric("検索URL数", len(urls))

if __name__ == "__main__":
    main()
