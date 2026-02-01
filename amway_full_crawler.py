import json
import os
import time
import re
from playwright.sync_api import sync_playwright
import datetime

DATA_FILE = "amway_products_full.json"

def discover_category_tabs(page):
    """
    /shop/c/shop 페이지에서 상단 카테고리 탭(영양건강, 뷰티 등)을 수집합니다.
    """
    print("카테고리 탭 탐색 중...")
    try:
        page.goto("https://www.amway.co.kr/shop/c/shop", wait_until="networkidle", timeout=60000)
    except:
        return []

    # 탭 메뉴 선택자 (추정: .category_list 또는 네비게이션 영역)
    # 실제 사이트 구조에 맞춰 모든 주요 카테고리 링크를 찾습니다.
    # 보통 상단 탭이나 '전체 카테고리' 영역을 찾음.
    
    categories = []
    
    # 주요 카테고리 텍스트로 링크 찾기 (스크린샷 기반)
    # "장바구니 스마트 오더" 또는 "스마트 오더"가 별도 탭으로 존재하는지 확인 필요
    target_cats = ["영양건강", "뷰티", "퍼스널 케어", "홈리빙", "원포원", "웰니스", "플러스 쇼핑", "장바구니 스마트 오더", "스마트 오더"]
    
    # 페이지 내의 모든 링크 중 텍스트가 위 목록에 포함되는 것 찾기
    # 정확도를 위해 특정 컨테이너(.category-wrap 등) 내에서 찾으면 좋으나, 범용적으로 검색
    
    for cat_name in target_cats:
        try:
            # 텍스트로 링크 찾기 (exact match or contains)
            link = page.get_by_role("link", name=cat_name, exact=True).first
            if not link.is_visible():
                # exact fail, try generic
                links = page.query_selector_all(f"a:has-text('{cat_name}')")
                for l in links:
                    if l.is_visible():
                        href = l.get_attribute("href")
                        if href and "/shop/" in href:
                            full_url = "https://www.amway.co.kr" + href if href.startswith("/") else href
                            categories.append({"name": cat_name, "url": full_url})
                            break
            else:
                href = link.get_attribute("href")
                if href:
                    full_url = "https://www.amway.co.kr" + href if href.startswith("/") else href
                    categories.append({"name": cat_name, "url": full_url})
        except:
            continue
            
    print(f"총 {len(categories)}개의 카테고리 탭 발견: {[c['name'] for c in categories]}")
    return categories

def load_previous_state():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_current_state(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def crawl_category(page, category_info):
    cat_name = category_info['name']
    url = category_info['url']
    
    print(f"Crawling Category: {cat_name} ({url})")
    try:
        page.goto(url, wait_until="networkidle", timeout=60000)
    except Exception as e:
        print(f"  Error loading page: {e}")
        return {}

    # Wait for products
    try:
        page.wait_for_selector(".product_item, .box_product", timeout=20000)
    except:
        print("  No products found initially.")
        return {}
    
    # Scroll to load all
    last_height = page.evaluate("document.body.scrollHeight")
    for i in range(15): # Adequate scrolling
        page.mouse.wheel(0, 15000)
        
        # Smart wait for height change
        start_wait = time.time()
        while time.time() - start_wait < 2.0:
            current_height = page.evaluate("document.body.scrollHeight")
            if current_height > last_height:
                break
            time.sleep(0.1)

        # Add a small buffer to ensure content renders fully or for multiple loads
        time.sleep(0.2)

        # '더보기' 버튼 처리
        try:
            more_btns = page.query_selector_all("a.btn_more, button.btn_more")
            for btn in more_btns:
                if btn.is_visible():
                    btn.click()
                    # Optimized wait with fallback
                    try:
                        page.wait_for_load_state("networkidle", timeout=1000)
                    except:
                        time.sleep(1) # Slightly more conservative fallback
        except: pass

        new_height = page.evaluate("document.body.scrollHeight")
        if new_height == last_height and i > 2:
            break
        last_height = new_height

    products = page.query_selector_all(".product_item")
    if not products:
        products = page.query_selector_all(".box_product")
        
    print(f"  Found {len(products)} products in {cat_name}.")
    
    category_data = {}

    for product in products:
        try:
            # 1. Name
            name_el = product.query_selector(".text_product-title")
            if not name_el: name_el = product.query_selector(".product_name")
            name = name_el.inner_text().strip() if name_el else "Unknown Name"

            # 2. Link & ID
            link_el = product.query_selector("a")
            link = link_el.get_attribute("href") if link_el else ""
            if link and link.startswith("/"):
                link = "https://www.amway.co.kr" + link
            
            product_id = link.split('/')[-1] if link else name

            # 3. Price
            price_el = product.query_selector(".text_price-data")
            if not price_el: price_el = product.query_selector(".price")
            price = price_el.inner_text().strip() if price_el else "0"

            # 4. Image
            img_el = product.query_selector("img")
            img_src = img_el.get_attribute("src") if img_el else ""
            if img_src and img_src.startswith("/"):
                img_src = "https://www.amway.co.kr" + img_src

            # 5. Status
            status_text = product.inner_text() # 전체 텍스트에서 상태 및 PV/BV 추출
            status = "판매중"
            if "일시품절" in status_text: status = "일시품절"
            elif "품절" in status_text: status = "품절"
            elif "단종" in status_text: status = "단종"

            # 6. PV / BV (Extract from data attributes)
            pv = "0"
            bv = "0"
            
            # Try to find data in hidden input or buttons
            data_el = product.query_selector("input[name='productTealiumTagInfo']")
            if not data_el:
                data_el = product.query_selector(".js-addtocart-v2")
            
            if data_el:
                raw_pv = data_el.get_attribute("data-product-point-value")
                raw_bv = data_el.get_attribute("data-product-business-volume")
                
                if raw_pv:
                    pv = str(int(float(raw_pv))) # Convert "27080.0" -> 27080
                if raw_bv:
                    bv = str(int(float(raw_bv)))
            
            # If still 0, try regex fallback (though likely unnecessary now)
            if pv == "0":
                pv_match = re.search(r"PV\s*:\s*([\d,]+)", status_text)
                if pv_match: pv = pv_match.group(1).replace(",", "")

            if bv == "0":
                bv_match = re.search(r"BV\s*:\s*([\d,]+)", status_text)
                if bv_match: bv = bv_match.group(1).replace(",", "")
            
            # --- [Custom Logic] 스마트 오더 분류 처리 ---
            final_category = cat_name
            if "스마트 오더" in name or "스마트오더" in name:
                final_category = "스마트 오더"
            # ----------------------------------------

            category_data[product_id] = {
                "id": product_id,
                "name": name,
                "price": price,
                "status": status,
                "link": link,
                "image": img_src,
                "category": final_category, # Korean Category Name (Auto-updated)
                "sub_category": "",   # No sub-category as requested
                "pv": pv,
                "bv": bv
            }
        except:
            continue

    return category_data

def crawl_promotions(page):
    """
    /notifications/promotion 페이지를 크롤링하여 이벤트 목록을 수집합니다.
    """
    print("Crawling Promotions (Optimized)...")
    try:
        page.goto("https://www.amway.co.kr/notifications/promotion", wait_until="networkidle", timeout=60000)
    except Exception as e:
        print(f"  Error loading promotion page: {e}")
        return {}

    promo_data = {}
    
    # 스크롤
    try:
        page.mouse.wheel(0, 5000)
        time.sleep(1)
    except: pass

    # Collect items with data-code or valid href
    promo_items = []
    
    # Query all 'a' tags
    candidates = page.query_selector_all("a")
    for link_el in candidates:
        try:
            text = link_el.inner_text().strip()
            if not text or len(text) < 5: continue
            if "기간 :" not in text and "프로모션" not in text: continue
            
            # Skip tabs
            if "진행중인" in text or "종료된" in text: continue

            href = link_el.get_attribute("href")
            data_code = link_el.get_attribute("data-code")
            notice_type = link_el.get_attribute("data-notice-type")

            # Determine if valid item
            target_url = None
            if href and href != "#" and not href.startswith("javascript"):
                target_url = href
                if target_url.startswith("/"):
                    target_url = "https://www.amway.co.kr" + target_url
            elif data_code and notice_type:
                target_url = f"https://www.amway.co.kr/notifications/promotion/detail?notificationCode={data_code}&noticeType={notice_type}&searchPromotionStatus=progress"
            
            if target_url:
                promo_items.append({
                    "text": text.split('\n')[0].strip(),
                    "url": target_url
                })
        except: continue

    print(f"  총 {len(promo_items)}개의 유효한 프로모션 항목을 발견했습니다.")

    # Create a new page context for details
    detail_page = page.context.new_page()

    for i, item in enumerate(promo_items):
        target_url = item["url"]
        target_title = item["text"]

        print(f"  [{i+1}/{len(promo_items)}] 프로모션 진입: {target_title}")
        try:
            detail_page.goto(target_url, wait_until="networkidle", timeout=30000)
            
            # --- 상세 페이지 도착 ---
            
            # 잠시 스크롤
            detail_page.mouse.wheel(0, 3000)
            time.sleep(1)

            # 상품 카드 (.product_item) 우선 검색 (일반적인 상품 목록 패턴)
            products = detail_page.query_selector_all(".product_item")
            if not products:
                products = detail_page.query_selector_all(".box_product")

            # 상품 카드가 있으면 기존 크롤링 로직 활용
            if products:
                for product in products:
                    try:
                        # Name
                        name_el = product.query_selector(".text_product-title")
                        if not name_el: name_el = product.query_selector(".product_name")
                        name = name_el.inner_text().strip() if name_el else "Unknown Name"

                        # Link
                        link_el = product.query_selector("a")
                        p_href = link_el.get_attribute("href") if link_el else ""
                        if not p_href or "/shop/" not in p_href: continue
                        
                        full_p_url = "https://www.amway.co.kr" + p_href if p_href.startswith("/") else p_href
                        product_id = full_p_url.split('/')[-1]

                        # Price
                        price_el = product.query_selector(".text_price-data")
                        if not price_el: price_el = product.query_selector(".price")
                        price = price_el.inner_text().strip() if price_el else "0"

                        # PV / BV (프로모션 내 상품에서도 추출)
                        pv = "0"
                        bv = "0"
                        
                        # Data attributes 확인
                        data_el = product.query_selector("input[name='productTealiumTagInfo']")
                        if not data_el:
                            data_el = product.query_selector(".js-addtocart-v2")
                        
                        if data_el:
                            raw_pv = data_el.get_attribute("data-product-point-value")
                            raw_bv = data_el.get_attribute("data-product-business-volume")
                            
                            if raw_pv:
                                pv = str(int(float(raw_pv)))
                            if raw_bv:
                                bv = str(int(float(raw_bv)))
                        
                        # 텍스트에서 추출 시도 (Fallback)
                        if pv == "0":
                            status_text = product.inner_text()
                            pv_match = re.search(r"PV\s*:\s*([\d,]+)", status_text)
                            if pv_match: pv = pv_match.group(1).replace(",", "")
                            
                            bv_match = re.search(r"BV\s*:\s*([\d,]+)", status_text)
                            if bv_match: bv = bv_match.group(1).replace(",", "")

                        # Image
                        img_el = product.query_selector("img")
                        img_src = img_el.get_attribute("src") if img_el else ""
                        if img_src and img_src.startswith("/"):
                            img_src = "https://www.amway.co.kr" + img_src
                        
                        # Save (Category = 이벤트)
                        if product_id not in promo_data:
                            promo_data[product_id] = {
                                "id": product_id,
                                "name": name,
                                "price": price,
                                "status": "진행중", # 이벤트 내 상품은 일단 진행중으로 가정
                                "link": full_p_url,
                                "image": img_src,
                                "category": "이벤트",
                                "sub_category": "",
                                "pv": pv,
                                "bv": bv
                            }
                    except: continue
            
            # 상품 카드가 없는 경우 (이미지 통배너에 링크만 걸린 경우)
            else:
                # /shop/ 링크를 모두 찾음
                shop_links = detail_page.query_selector_all("a[href*='/shop/']")
                for sl in shop_links:
                    try:
                        href = sl.get_attribute("href")
                        # 카테고리 링크 제외 (shop/c/...)
                        if "/shop/c/" in href: continue
                        # 순수 상품 링크 추정 (숫자 ID나 p/코드)
                        if "/p/" not in href and not href.split('/')[-1].isdigit(): continue

                        full_p_url = "https://www.amway.co.kr" + href if href.startswith("/") else href
                        product_id = full_p_url.split('/')[-1]
                        
                        # 이름 추출 시도 (링크 내부 텍스트 or 이미지 alt)
                        name = sl.inner_text().strip()
                        if not name:
                            img = sl.query_selector("img")
                            if img: name = img.get_attribute("alt") or "이벤트 상품"
                        if not name: name = "이벤트 상품"

                        # 중복 방지
                        if product_id not in promo_data:
                            promo_data[product_id] = {
                                "id": product_id,
                                "name": name,
                                "price": "0", # 링크만으로는 가격 알 수 없음
                                "status": "진행중",
                                "link": full_p_url,
                                "image": "", # 이미지 찾기 어려움
                                "category": "이벤트",
                                "sub_category": "",
                                "pv": "0",
                                "bv": "0"
                            }
                    except: continue

        except Exception as e:
            print(f"    -> 상세 페이지 로드 실패: {e}")
            # Try to recover detail page in case it crashed
            try:
                detail_page.close()
            except: pass
            
            try:
                detail_page = page.context.new_page()
            except:
                print("    -> Critical: Failed to recreate detail page.")

    try:
        detail_page.close()
    except: pass

    print(f"  Found {len(promo_data)} products in promotions.")
    return promo_data

def run_full_crawl(data_callback=None):
    print(f"[{datetime.datetime.now()}] Starting Amway Smart Crawler...")
    
    current_data = {}

    with sync_playwright() as p:
        print("  -> 브라우저를 실행 중입니다... (잠시만 기다려주세요)")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        # 1. 카테고리 탭 발견
        cats = discover_category_tabs(page)
        
        if not cats:
            print("카테고리를 찾지 못했습니다. 기본 URL로 시도합니다.")
            cats = [{"name": "전체상품", "url": "https://www.amway.co.kr/shop/c/shop"}]

        # 2. 각 카테고리 크롤링
        for cat in cats:
            cat_products = crawl_category(page, cat)
            
            if data_callback and cat_products:
                print(f"  >> Sending {len(cat_products)} items to sync...")
                data_callback(cat_products)

            current_data.update(cat_products)

        # 3. [추가] 프로모션(이벤트) 크롤링
        promo_products = crawl_promotions(page)
        if promo_products:
            if data_callback:
                print(f"  >> Sending {len(promo_products)} promotions to sync...")
                data_callback(promo_products)
            current_data.update(promo_products)
            
        browser.close()

    print(f"Total products scraped: {len(current_data)}")
    save_current_state(current_data)
    return current_data

if __name__ == "__main__":
    run_full_crawl()
