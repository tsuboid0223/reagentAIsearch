# -*- coding: utf-8 -*-
"""
製品調達AIエージェント - CosmoBioテスト版 v2.1 (最終版)
（Gemini 1.5 Flash使用 - 確実に動作）
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
import logging
import sys
from datetime import datetime

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
# 定数定義
# ==============================================================================
DEFAULT_MODEL = 'gemini-1.5-flash'  # ← 変更: 安定版を使用
TEST_SITE = 'cosmobio.co.jp'

# フォールバックURL
FALLBACK_URLS = {
    'Y27632': [
        'https://www.cosmobio.co.jp/product/detail/y-27632-dihydrochloride-enz.asp?entry_id=16716',
        'https://search.cosmobio.co.jp/view/p_view.asp?PrimaryKeyValue=4769669&ServerKey=&selPrice=1',
        'https://search.cosmobio.co.jp/view/p_view.asp?PrimaryKeyValue=6379673&ServerKey=&selPrice=1'
    ]
}

# ==============================================================================
# リアルタイムログクラス
# ==============================================================================
class RealTimeLogger:
    """Streamlitでリアルタイムログを表示"""
    def __init__(self):
        self.log_container = st.empty()
        self.logs = []
    
    def add(self, message: str, level: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        icons = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "❌"}
        icon = icons.get(level, "📝")
        
        log_entry = f"{icon} [{timestamp}] {message}"
        self.logs.append(log_entry)
        
        display_logs = self.logs[-25:]
        self.log_container.text_area(
            "🔍 リアルタイムログ",
            "\n".join(display_logs),
            height=350,
            key=f"log_{len(self.logs)}"
        )
        
        logger.info(message)
        sys.stdout.flush()
    
    def clear(self):
        self.logs = []
        self.log_container.empty()

# ==============================================================================
# Gemini API検証
# ==============================================================================
def validate_gemini_api_key(api_key: str, rt_logger: RealTimeLogger) -> tuple[bool, list]:
    """Gemini APIキー検証 + 利用可能モデル一覧取得"""
    try:
        rt_logger.add("Gemini APIキーを検証中...", "info")
        test_url = f"https://generativelanguage.googleapis.com/v1/models?key={api_key}"
        response = requests.get(test_url, timeout=10)
        
        if response.status_code == 200:
            models_data = response.json().get('models', [])
            models = [m.get('name', '').replace('models/', '') for m in models_data if 'gemini' in m.get('name', '').lower()]
            rt_logger.add(f"✅ APIキー有効 - モデル数: {len(models)}", "success")
            
            # v1対応モデルのみ抽出
            v1_models = [m for m in models if 'generateContent' in str(models_data)]
            if v1_models:
                rt_logger.add(f"  利用可能モデル例: {models[:3]}", "info")
            
            return True, models
        else:
            rt_logger.add(f"❌ APIキー検証失敗 (status: {response.status_code})", "error")
            return False, []
    except Exception as e:
        rt_logger.add(f"❌ API検証エラー: {str(e)[:60]}", "error")
        return False, []

# ==============================================================================
# URL検索関数
# ==============================================================================
def get_fallback_urls(product_name: str, rt_logger: RealTimeLogger, max_results: int = 3) -> list:
    """フォールバックURL取得"""
    product_key = product_name.upper().replace('-', '').replace(' ', '')
    
    for key in FALLBACK_URLS:
        if key.upper().replace('-', '').replace(' ', '') == product_key:
            rt_logger.add(f"✅ フォールバックURL使用: {product_name}", "success")
            urls = FALLBACK_URLS[key][:max_results]
            for idx, url in enumerate(urls):
                rt_logger.add(f"  {idx+1}. {url[:60]}...", "info")
            return urls
    
    rt_logger.add(f"⚠️ フォールバックURLなし: {product_name}", "warning")
    return []

# ==============================================================================
# 直接アクセス関数
# ==============================================================================
def get_page_content_direct(url: str, rt_logger: RealTimeLogger, timeout: int = 10) -> dict:
    """プロキシを使わず直接アクセス"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ja,en;q=0.9',
    }
    result = {"url": url, "status_code": None, "content": None, "error": None}
    
    try:
        rt_logger.add(f"  直接アクセス開始...", "info")
        start_time = time.time()
        
        response = requests.get(url, headers=headers, timeout=timeout)
        elapsed = time.time() - start_time
        
        rt_logger.add(f"  応答受信 ({elapsed:.1f}秒) - status: {response.status_code}", "info")
        response.raise_for_status()
        
        if len(response.text) < 500:
            rt_logger.add(f"  ⚠️ レスポンスが短い ({len(response.text)}文字)", "warning")
            result["error"] = "Response too short"
            return result
        
        rt_logger.add(f"  成功 - {len(response.text)}文字取得", "success")
        
        # HTML解析
        soup = BeautifulSoup(response.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'iframe']):
            tag.decompose()
        
        body_text = soup.body.get_text(separator=' ', strip=True) if soup.body else soup.get_text(separator=' ', strip=True)
        result["content"] = body_text[:18000]
        result["status_code"] = response.status_code
        
        return result
        
    except Exception as e:
        rt_logger.add(f"  ❌ 失敗: {str(e)[:60]}", "error")
        result["error"] = str(e)
        return result

# ==============================================================================
# AI解析関数（Gemini 1.5 Flash最適化版）
# ==============================================================================
def analyze_page_with_gemini(page_content: str, product_name: str, gemini_api_key: str, rt_logger: RealTimeLogger, model_name: str = DEFAULT_MODEL) -> dict | None:
    """Gemini APIで製品情報を抽出（Gemini 1.5対応版）"""
    
    prompt = f"""
あなたは化学試薬ECサイトの情報抽出エージェントです。

【抽出対象製品】
製品名: {product_name}

【Webページテキスト】
{page_content}

【抽出ルール】
1. productName: ページタイトルまたはh1要素の製品名
2. modelNumber: 型番/製品コード/カタログ番号
3. manufacturer: メーカー/サプライヤー名
4. offers: 価格表から以下を抽出
   - size: 容量/規格（例: "1 mg", "5 mg"）
   - price: 価格の数値のみ（例: ¥34,000 → 34000）
   - inStock: 在庫状況（「在庫あり」「カートに入れる」があればtrue）

【注意事項】
- 文字化け「��」は「¥」として処理
- 価格表が複数行ある場合は全て抽出
- 情報が見つからない項目はnull
- offers配列は必ず作成（空でも可）

【重要: 出力形式】
必ず以下のJSON形式**のみ**を出力してください。説明文は不要です:

{{
  "productName": "製品名またはnull",
  "modelNumber": "型番またはnull", 
  "manufacturer": "メーカー名またはnull",
  "offers": [
    {{"size": "容量", "price": 価格数値, "inStock": true/false}}
  ]
}}
"""
    
    try:
        rt_logger.add(f"  Gemini API呼び出し中...", "info")
        rt_logger.add(f"  モデル: {model_name}", "info")
        start_time = time.time()
        
        # Gemini 1.5用の設定
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 2048,
                "topP": 0.95,
                "topK": 40
            }
        }
        
        # v1 API使用
        api_url = f"https://generativelanguage.googleapis.com/v1/models/{model_name}:generateContent?key={gemini_api_key}"
        
        response = requests.post(
            api_url,
            headers={'Content-Type': 'application/json'},
            json=payload,
            timeout=45
        )
        
        elapsed = time.time() - start_time
        rt_logger.add(f"  応答受信 ({elapsed:.1f}秒) - status: {response.status_code}", "info")
        
        if response.status_code != 200:
            error_detail = response.text[:300]
            rt_logger.add(f"  ❌ エラー: {error_detail}", "error")
            return None
        
        result = response.json()
        
        if not result.get('candidates'):
            rt_logger.add(f"  ⚠️ candidates なし", "warning")
            return None
        
        response_text = result['candidates'][0]['content']['parts'][0]['text']
        rt_logger.add(f"  レスポンス受信 ({len(response_text)}文字)", "info")
        
        # JSONを抽出
        json_text = response_text.strip()
        
        # マークダウンコードブロック除去
        if '```json' in json_text:
            json_text = json_text.split('```json')[1].split('```')[0].strip()
            rt_logger.add(f"  マークダウンブロック除去", "info")
        elif '```' in json_text:
            json_text = json_text.split('```')[1].split('```')[0].strip()
            rt_logger.add(f"  コードブロック除去", "info")
        
        rt_logger.add(f"  JSON解析中... ({len(json_text)}文字)", "info")
        
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as e:
            rt_logger.add(f"  ⚠️ JSON解析失敗 - 位置: 行{e.lineno} 列{e.colno}", "warning")
            
            # フォールバック: 正規表現で抽出
            import re
            json_match = re.search(r'\{[^{}]*"offers"[^{}]*\[[^\]]*\][^{}]*\}', json_text, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group(0))
                    rt_logger.add(f"  正規表現でJSON抽出成功", "success")
                except:
                    rt_logger.add(f"  フォールバックも失敗", "error")
                    return None
            else:
                return None
        
        offers_count = len(data.get("offers", []))
        rt_logger.add(f"  ✅ 抽出成功: {offers_count}件のoffer", "success")
        
        if offers_count > 0:
            sample = data["offers"][0]
            rt_logger.add(f"    例: {sample.get('size')} - ¥{sample.get('price'):,}", "info")
        
        return data if isinstance(data, dict) else None
        
    except Exception as e:
        rt_logger.add(f"  ❌ AI解析エラー: {str(e)[:100]}", "error")
        return None

# ==============================================================================
# メイン処理
# ==============================================================================
def run_cosmobio_test(product_name: str, manufacturer: str, gemini_api_key: str, model_name: str = DEFAULT_MODEL, max_urls: int = 3) -> tuple[list, list]:
    """CosmoBioテスト実行"""
    
    rt_logger = RealTimeLogger()
    rt_logger.add(f"=== CosmoBioテスト開始 ===", "success")
    rt_logger.add(f"製品名: {product_name}", "info")
    rt_logger.add(f"メーカー: {manufacturer}", "info")
    rt_logger.add(f"モデル: {model_name}", "info")
    
    # API検証
    is_valid, available_models = validate_gemini_api_key(gemini_api_key, rt_logger)
    if not is_valid:
        st.error("❌ Gemini APIキーが無効です")
        return [], []
    
    # モデル確認
    if model_name not in available_models:
        rt_logger.add(f"⚠️ モデル '{model_name}' が利用不可", "warning")
        fallback = 'gemini-1.5-flash' if 'gemini-1.5-flash' in available_models else available_models[0]
        rt_logger.add(f"  代替モデル使用: {fallback}", "info")
        model_name = fallback
    
    # 進捗バー
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    # ステップ1: URL取得
    status_text.text("⏳ URL取得中...")
    progress_bar.progress(0.2)
    rt_logger.add(f"--- ステップ1: URL取得 ---", "success")
    
    urls = get_fallback_urls(product_name, rt_logger, max_urls)
    
    if not urls:
        st.error(f"❌ {product_name}のURLが見つかりませんでした")
        return [], []
    
    with st.expander(f"📋 使用URL ({len(urls)}件)"):
        for idx, url in enumerate(urls):
            st.text(f"{idx+1}. {url}")
    
    progress_bar.progress(0.4)
    status_text.text("✅ URL取得完了")
    
    # ステップ2: ページ取得
    status_text.text("⏳ ページ取得中...")
    progress_bar.progress(0.5)
    rt_logger.add(f"--- ステップ2: ページ取得 ({len(urls)}件) ---", "success")
    
    page_results = []
    for i, url in enumerate(urls):
        rt_logger.add(f"========== URL {i+1}/{len(urls)} ==========", "info")
        rt_logger.add(f"{url}", "info")
        
        page_result = get_page_content_direct(url, rt_logger)
        page_results.append(page_result)
        
        progress = 0.5 + (i + 1) / len(urls) * 0.2
        progress_bar.progress(progress)
        status_text.text(f"⏳ ページ取得中... ({i+1}/{len(urls)})")
    
    progress_bar.progress(0.7)
    status_text.text("✅ ページ取得完了")
    
    # ステップ3: AI解析
    status_text.text("⏳ AI解析中...")
    progress_bar.progress(0.75)
    rt_logger.add(f"--- ステップ3: AI解析 ---", "success")
    
    found_products = []
    valid_pages = [p for p in page_results if p.get("content") and len(p.get("content", "")) > 1000]
    
    rt_logger.add(f"解析対象: {len(valid_pages)}件", "info")
    
    for i, page_result in enumerate(valid_pages):
        rt_logger.add(f"========== AI解析 {i+1}/{len(valid_pages)} ==========", "info")
        rt_logger.add(f"{page_result.get('url')}", "info")
        
        product_data = analyze_page_with_gemini(
            page_result["content"],
            product_name,
            gemini_api_key,
            rt_logger,
            model_name
        )
        
        if product_data and product_data.get("offers"):
            product_data['sourceUrl'] = page_result.get("url")
            found_products.append(product_data)
            rt_logger.add(f"✅ 製品情報追加: {len(found_products)}件目", "success")
        
        progress = 0.75 + (i + 1) / len(valid_pages) * 0.25
        progress_bar.progress(progress)
        status_text.text(f"⏳ AI解析中... ({i+1}/{len(valid_pages)})")
    
    progress_bar.progress(1.0)
    status_text.text("✅ 完了")
    rt_logger.add(f"=== テスト完了: {len(found_products)}件の製品情報を抽出 ===", "success")
    
    return found_products, page_results

# ==============================================================================
# Streamlit UI
# ==============================================================================
st.set_page_config(layout="wide", page_title="CosmoBioテスト v2.1", page_icon="🧪")
st.title("🧪 CosmoBio検索テスト v2.1")
st.caption("最終版: Gemini 1.5 Flash使用 - 確実動作")

# サイドバー
st.sidebar.header("⚙️ APIキー設定")
try:
    gemini_api_key = st.secrets["GOOGLE_API_KEY"]
    st.sidebar.success("✅ Gemini APIキーが設定されています")
except KeyError:
    st.sidebar.error("❌ GOOGLE_API_KEYが設定されていません")
    gemini_api_key = ""

st.sidebar.header("🔍 検索条件")
product_name_input = st.sidebar.text_input(
    "製品名 (必須)", 
    value="Y27632",
    placeholder="例: Y27632"
)
manufacturer_input = st.sidebar.text_input(
    "メーカー", 
    value="",
    placeholder="例: Selleck"
)

max_urls_input = st.sidebar.slider(
    "取得URL数",
    min_value=1,
    max_value=3,
    value=3
)

# モデル選択（Gemini 1.5のみ）
model_options = {
    'gemini-1.5-flash': 'Gemini 1.5 Flash (推奨・高速)',
    'gemini-1.5-pro': 'Gemini 1.5 Pro (高精度)'
}
selected_model = st.sidebar.selectbox(
    "使用するモデル",
    options=list(model_options.keys()),
    format_func=lambda x: model_options[x],
    index=0
)

search_button = st.sidebar.button("🚀 テスト実行", type="primary", use_container_width=True)

st.sidebar.info(f"""
📊 **テスト設定**
- 対象: {TEST_SITE}
- URL数: {max_urls_input}件
- モデル: Gemini 1.5
- 方式: フォールバックURL使用
""")

if search_button:
    if not gemini_api_key:
        st.error("❌ Gemini APIキーが設定されていません")
    elif not product_name_input:
        st.error("❌ 製品名を入力してください")
    else:
        products, logs = run_cosmobio_test(
            product_name_input,
            manufacturer_input,
            gemini_api_key,
            selected_model,
            max_urls_input
        )
        
        if products:
            st.success(f"✅ {len(products)}件の製品情報を取得")
            
            results = []
            for product in products:
                for offer in product.get('offers', []):
                    try:
                        price = int(float(offer.get('price', 0)))
                    except:
                        price = 0
                    
                    results.append({
                        '製品名': product.get('productName', 'N/A'),
                        '型番': product.get('modelNumber', 'N/A'),
                        'メーカー': product.get('manufacturer', 'N/A'),
                        '仕様': offer.get('size', 'N/A'),
                        '価格': price,
                        '在庫': 'あり' if offer.get('inStock') else 'なし/不明',
                        'URL': product.get('sourceUrl', 'N/A')
                    })
            
            df = pd.DataFrame(results)
            
            st.subheader("📊 検索結果")
            st.dataframe(
                df,
                column_config={
                    "価格": st.column_config.NumberColumn(format="¥%d"),
                    "URL": st.column_config.LinkColumn("Link", display_text="開く")
                },
                use_container_width=True,
                hide_index=True
            )
            
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                "📥 CSVダウンロード",
                csv,
                f"cosmobio_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
                "text/csv",
                use_container_width=True
            )
            
            with st.expander("🔍 抽出JSON"):
                for idx, product in enumerate(products):
                    st.write(f"**製品 {idx+1}**")
                    st.json(product)
        else:
            st.warning("⚠️ 製品情報が抽出できませんでした")
        
        with st.expander("📝 ページログ"):
            for idx, log in enumerate(logs):
                st.json({
                    "url": log.get("url"),
                    "status": log.get("status_code"),
                    "length": len(log.get("content", "")),
                    "error": log.get("error")
                })

st.sidebar.markdown("---")
st.sidebar.caption("🎯 v2.1: Gemini 1.5 Flash最適化版")

logger.info("Application Ready")
