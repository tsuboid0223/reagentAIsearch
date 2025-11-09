"""
化学試薬情報収集アプリ v3.5
- 高速化版（550秒 → 200-300秒目標）
- スーパーリンク対応（クリック可能なリンク）
- Bright Data Browser API + SERP API統合
- 製品名類似度フィルタリング + 404/エラー検出
- Gemini 2.5 Pro使用
"""

import streamlit as st
import asyncio
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
import requests
import json
import time
from datetime import datetime
import pandas as pd
from io import StringIO
import re
from difflib import SequenceMatcher
import html
import urllib.parse
import logging

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ========== 設定 ==========
BRIGHT_DATA_CONFIG = {
    "browser_api": {
        "host": "brd.superproxy.io",
        "port": 9515,
        "username": "brd-customer-hl_d0ba4768-zone-scraping_browser1",
        "password": "ohwvpqbxcj3q"
    },
    "serp_api": {
        "url": "https://api.brightdata.com/serp/req",
        "username": "brd-customer-hl_d0ba4768-zone-serp_api1",
        "password": "ohwvpqbxcj3q"
    }
}

GEMINI_API_KEY = "AIzaSyAXVsix-5q5_VZdBH00T9EwGmTK7iCAESI"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent"

EC_SITES = [
    {"name": "コスモバイオ", "domain": "cosmobio.co.jp"},
    {"name": "フナコシ", "domain": "funakoshi.co.jp"},
    {"name": "AXEL", "domain": "axel.as-1.co.jp"},
    {"name": "Selleck", "domain": "selleck.co.jp"},
    {"name": "MCE", "domain": "medchemexpress.com"},
    {"name": "ナカライ", "domain": "nacalai.co.jp"},
    {"name": "富士フイルム和光", "domain": "labchem-wako.fujifilm.com"},
    {"name": "関東化学", "domain": "kanto.co.jp"},
    {"name": "TCI", "domain": "tcichemicals.com"},
    {"name": "Merck", "domain": "sigmaaldrich.com"},
    {"name": "和光純薬", "domain": "wako-chem.co.jp"}
]

# ========== 高速化パラメータ（v3.5） ==========
SPEED_CONFIG = {
    "page_timeout": 30000,        # 60秒 → 30秒
    "wait_time": 2000,            # 5秒 → 2秒
    "retry_count": 2,             # 3回 → 2回
    "serp_timeout": 15,           # 20秒 → 15秒
    "gemini_timeout": 20,         # 30秒 → 20秒
    "min_html_size": 5000         # 404検出
}

SIMILARITY_THRESHOLD = 0.5  # 製品名類似度閾値

# ========== ユーティリティ関数 ==========

def clean_url(url):
    """URLのクリーニング（Unicode/HTMLエンティティデコード）"""
    if not url:
        return url
    url = html.unescape(url)
    url = urllib.parse.unquote(url)
    url = url.strip()
    return url

def calculate_similarity(str1, str2):
    """2つの文字列の類似度を計算（0.0～1.0）"""
    str1_clean = str1.lower().strip()
    str2_clean = str2.lower().strip()
    return SequenceMatcher(None, str1_clean, str2_clean).ratio()

def is_likely_404_page(html_content):
    """404エラーページの可能性を判定"""
    if len(html_content) < SPEED_CONFIG["min_html_size"]:
        return True
    
    error_keywords = [
        "404", "not found", "ページが見つかりません",
        "お探しのページは見つかりませんでした",
        "該当する商品がありません"
    ]
    
    html_lower = html_content.lower()
    count = sum(1 for keyword in error_keywords if keyword in html_lower)
    
    return count >= 2

# ========== SERP API（Google検索） ==========

def search_urls_serp_api(query, site_domain, max_results=3):
    """SERP APIでGoogle検索を実行"""
    search_query = f"{query} site:{site_domain}"
    
    payload = [{
        "url": "https://www.google.com/search",
        "q": search_query,
        "gl": "jp",
        "hl": "ja",
        "num": max_results
    }]
    
    try:
        logger.info(f"🔍 SERP API検索: {site_domain}")
        response = requests.post(
            BRIGHT_DATA_CONFIG["serp_api"]["url"],
            auth=(
                BRIGHT_DATA_CONFIG["serp_api"]["username"],
                BRIGHT_DATA_CONFIG["serp_api"]["password"]
            ),
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=SPEED_CONFIG["serp_timeout"]
        )
        
        if response.status_code != 200:
            logger.warning(f"⚠️ SERP APIエラー: HTTP {response.status_code}")
            return []
        
        results = response.json()
        if not results or len(results) == 0:
            logger.warning(f"⚠️ SERP API結果なし: {site_domain}")
            return []
        
        result_data = results[0]
        organic_results = result_data.get("organic", [])
        
        urls = []
        for item in organic_results[:max_results]:
            url = item.get("link")
            if url:
                cleaned_url = clean_url(url)
                urls.append(cleaned_url)
        
        logger.info(f"✅ URL発見: {len(urls)}件 ({site_domain})")
        return urls
    
    except requests.Timeout:
        logger.error(f"⏱️ SERP APIタイムアウト: {site_domain}")
        return []
    except Exception as e:
        logger.error(f"❌ SERP APIエラー: {str(e)[:100]}")
        return []

# ========== Browser API（ページ取得） ==========

async def fetch_page_with_browser_api(url, retries=SPEED_CONFIG["retry_count"]):
    """Browser APIでページを取得（高速化版）"""
    ws_endpoint = (
        f"wss://{BRIGHT_DATA_CONFIG['browser_api']['username']}:"
        f"{BRIGHT_DATA_CONFIG['browser_api']['password']}@"
        f"{BRIGHT_DATA_CONFIG['browser_api']['host']}:"
        f"{BRIGHT_DATA_CONFIG['browser_api']['port']}"
    )
    
    for attempt in range(retries):
        try:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(ws_endpoint)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await context.new_page()
                
                logger.info(f"🌐 ページ取得中: {url[:60]}...")
                
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=SPEED_CONFIG["page_timeout"]
                )
                
                if response and response.status in [403, 404, 500]:
                    logger.warning(f"⚠️ HTTP {response.status}: {url[:60]}")
                    await browser.close()
                    if attempt < retries - 1:
                        await asyncio.sleep(1)
                        continue
                    return None
                
                await asyncio.sleep(SPEED_CONFIG["wait_time"] / 1000)
                html_content = await page.content()
                await browser.close()
                
                if is_likely_404_page(html_content):
                    logger.warning(f"🚫 404ページ検出: {len(html_content)} chars")
                    return None
                
                logger.info(f"✅ ページ取得成功: {len(html_content)} chars")
                return html_content
        
        except PlaywrightTimeout:
            logger.warning(f"⏱️ タイムアウト (試行 {attempt+1}/{retries})")
            if attempt < retries - 1:
                await asyncio.sleep(1)
                continue
            return None
        except Exception as e:
            logger.error(f"❌ エラー (試行 {attempt+1}/{retries}): {str(e)[:100]}")
            if attempt < retries - 1:
                await asyncio.sleep(1)
                continue
            return None
    
    return None

# ========== Gemini API（構造化抽出） ==========

def extract_with_gemini(html_content, query_product, source_url):
    """Gemini APIで製品情報を抽出（高速化版）"""
    
    # 価格関連キーワードチェック（高速化のため事前フィルタリング）
    price_keywords = ["価格", "円", "¥", "税", "price", "JPY", "送料"]
    html_lower = html_content.lower()
    keyword_count = sum(1 for kw in price_keywords if kw in html_lower)
    
    if keyword_count == 0:
        logger.warning(f"⚠️ 価格キーワード未検出（{len(html_content)} chars）")
    
    # HTMLを25000文字に制限（Gemini APIのトークン削減）
    html_snippet = html_content[:25000]
    
    prompt = f"""
あなたは化学試薬のECサイトから製品情報を抽出する専門家です。

【重要】以下のHTMLから「{query_product}」に関する製品情報を抽出してください。

HTMLコンテンツ:
```html
{html_snippet}
```

抽出ルール:
1. 製品名が「{query_product}」と一致または類似する製品のみ対象
2. 複数の容量/価格がある場合は全て抽出
3. 在庫情報が不明な場合は「不明」
4. メーカー情報が不明な場合は「不明」

必須フォーマット（JSON配列）:
```json
[
  {{
    "product_name": "製品名",
    "catalog_number": "型番またはCAS番号",
    "manufacturer": "メーカー名",
    "link": "{source_url}",
    "capacity": "容量（例: 1mg, 5mg）",
    "price": "価格（例: ¥34,000）",
    "stock_status": "在庫有無（有/無/不明）"
  }}
]
```

【注意】製品情報が見つからない場合は空配列 [] を返してください。
"""
    
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 2048
        }
    }
    
    try:
        logger.info(f"🤖 Gemini API呼び出し中...")
        response = requests.post(
            f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
            headers=headers,
            json=payload,
            timeout=SPEED_CONFIG["gemini_timeout"]
        )
        
        if response.status_code != 200:
            logger.error(f"❌ Gemini APIエラー: HTTP {response.status_code}")
            return []
        
        result = response.json()
        text_response = result["candidates"][0]["content"]["parts"][0]["text"]
        
        json_match = re.search(r'```json\s*([\s\S]*?)\s*```', text_response)
        if json_match:
            json_text = json_match.group(1)
        else:
            json_text = text_response
        
        products = json.loads(json_text)
        
        if not isinstance(products, list):
            logger.warning(f"⚠️ Gemini応答が配列ではありません")
            return []
        
        logger.info(f"✅ Gemini抽出成功: {len(products)}件")
        return products
    
    except requests.Timeout:
        logger.error(f"⏱️ Gemini APIタイムアウト")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"❌ JSON解析エラー: {str(e)[:100]}")
        return []
    except Exception as e:
        logger.error(f"❌ Gemini APIエラー: {str(e)[:100]}")
        return []

# ========== メイン処理 ==========

async def collect_product_info(query_product, site_name, site_domain):
    """1サイトの製品情報収集（高速化版）"""
    logger.info(f"\n{'='*60}")
    logger.info(f"📦 サイト: {site_name} ({site_domain})")
    
    # SERP APIでURL検索
    urls = search_urls_serp_api(query_product, site_domain, max_results=3)
    
    if not urls:
        logger.warning(f"⚠️ URL未発見: {site_name}")
        return []
    
    all_products = []
    filtered_count = 0
    
    for idx, url in enumerate(urls, 1):
        logger.info(f"\n--- URL {idx}/{len(urls)} ---")
        logger.info(f"🔗 {url}")
        
        # Browser APIでページ取得
        html_content = await fetch_page_with_browser_api(url)
        
        if not html_content:
            logger.warning(f"⚠️ ページ取得失敗")
            continue
        
        # Gemini APIで抽出
        products = extract_with_gemini(html_content, query_product, url)
        
        if not products:
            logger.warning(f"⚠️ 製品情報なし")
            continue
        
        # 製品名類似度フィルタリング
        for product in products:
            product_name = product.get("product_name", "")
            similarity = calculate_similarity(query_product, product_name)
            
            if similarity < SIMILARITY_THRESHOLD:
                logger.warning(
                    f"🚫 類似度フィルタリング除外: "
                    f"{product_name[:30]} (類似度: {similarity:.2f})"
                )
                filtered_count += 1
                continue
            
            product["site_name"] = site_name
            all_products.append(product)
            logger.info(f"✅ 製品追加: {product_name[:30]} (類似度: {similarity:.2f})")
    
    if filtered_count > 0:
        logger.info(f"🚫 フィルタリング除外: {filtered_count}件")
    
    logger.info(f"📊 {site_name} 取得完了: {len(all_products)}件")
    return all_products

async def collect_all_sites(query_product, progress_bar, status_text):
    """全サイトから製品情報収集（高速化版）"""
    all_products = []
    total_sites = len(EC_SITES)
    success_count = 0
    total_filtered = 0
    
    start_time = time.time()
    
    for idx, site in enumerate(EC_SITES, 1):
        status_text.text(f"🔍 検索中: {site['name']} ({idx}/{total_sites})")
        progress_bar.progress(idx / total_sites)
        
        products = await collect_product_info(
            query_product,
            site['name'],
            site['domain']
        )
        
        if products:
            all_products.extend(products)
            success_count += 1
        
        logger.info(f"⏭️ 次のサイトへ")
    
    elapsed_time = time.time() - start_time
    
    logger.info(f"\n{'='*60}")
    logger.info(f"🎉 処理完了: {elapsed_time:.1f}秒")
    logger.info(f"📊 取得成功: {success_count}/{total_sites}サイト")
    if total_filtered > 0:
        logger.info(f"🚫 フィルタリング除外: {total_filtered}件（類似度 < {SIMILARITY_THRESHOLD}）")
    
    return all_products

# ========== Streamlit UI ==========

def create_hyperlink_df(df):
    """DataFrameのリンク先列をクリック可能なHTMLリンクに変換"""
    if df.empty or 'リンク先' not in df.columns:
        return df
    
    df_display = df.copy()
    
    # リンク先をHTMLリンクに変換
    df_display['リンク先'] = df_display['リンク先'].apply(
        lambda x: f'<a href="{x}" target="_blank">🔗 製品ページ</a>' if pd.notna(x) else ''
    )
    
    return df_display

def main():
    st.set_page_config(
        page_title="化学試薬情報収集システム v3.5",
        page_icon="🧪",
        layout="wide"
    )
    
    st.title("🧪 化学試薬情報収集システム v3.5")
    st.markdown("**高速化版** | Browser API + SERP API統合 | Gemini 2.5 Pro")
    
    # サイドバー
    with st.sidebar:
        st.header("⚙️ 設定")
        st.write(f"**対象サイト数**: {len(EC_SITES)}サイト")
        st.write(f"**類似度閾値**: {SIMILARITY_THRESHOLD}")
        st.write(f"**タイムアウト**: {SPEED_CONFIG['page_timeout']/1000}秒")
        st.write(f"**待機時間**: {SPEED_CONFIG['wait_time']/1000}秒")
        
        st.markdown("---")
        st.markdown("### 📋 対象ECサイト")
        for site in EC_SITES:
            st.markdown(f"- {site['name']}")
    
    # メインエリア
    query = st.text_input(
        "🔍 検索する試薬名を入力してください",
        placeholder="例: Y-27632, Paclitaxel, DMSO"
    )
    
    col1, col2 = st.columns([1, 4])
    with col1:
        search_button = st.button("🚀 検索開始", type="primary", use_container_width=True)
    
    if search_button:
        if not query:
            st.error("⚠️ 試薬名を入力してください")
            return
        
        st.markdown("---")
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # 非同期処理実行
        products = asyncio.run(collect_all_sites(query, progress_bar, status_text))
        
        progress_bar.empty()
        status_text.empty()
        
        if not products:
            st.warning("⚠️ 製品情報が見つかりませんでした")
            return
        
        # データフレーム作成
        df = pd.DataFrame(products)
        
        # 列名を日本語に変換
        column_mapping = {
            "product_name": "製品名",
            "site_name": "販売元",
            "catalog_number": "型番",
            "manufacturer": "メーカー",
            "link": "リンク先",
            "capacity": "容量",
            "price": "価格",
            "stock_status": "在庫有無"
        }
        df = df.rename(columns=column_mapping)
        
        # 列順序を整理
        column_order = ["製品名", "販売元", "型番", "メーカー", "リンク先", "容量", "価格", "在庫有無"]
        df = df[[col for col in column_order if col in df.columns]]
        
        st.success(f"✅ 検索完了: {len(df)}件の製品情報を取得しました")
        
        # HTMLリンク付きDataFrameを作成
        df_display = create_hyperlink_df(df)
        
        # テーブル表示（HTMLリンク有効化）
        st.markdown("### 📊 検索結果")
        st.write(df_display.to_html(escape=False, index=False), unsafe_allow_html=True)
        
        # CSV出力
        st.markdown("---")
        st.markdown("### 📥 データエクスポート")
        
        timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M")
        csv_filename = f"{timestamp}_export.csv"
        
        csv_buffer = StringIO()
        df.to_csv(csv_buffer, index=True, encoding='utf-8-sig')
        csv_data = csv_buffer.getvalue()
        
        st.download_button(
            label="📥 CSVダウンロード",
            data=csv_data,
            file_name=csv_filename,
            mime="text/csv"
        )
        
        st.info(f"💡 ヒント: Excelで開く場合は UTF-8 BOM 形式で保存されています")

if __name__ == "__main__":
    main()
