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
    print("Crawling Promotions...")
    try:
        page.goto("https://www.amway.co.kr/notifications/promotion", wait_until="networkidle", timeout=60000)
    except Exception as e:
        print(f"  Error loading promotion page: {e}")
        return {}

    # 이벤트 리스트 컨테이너 (텍스트 분석 결과 기반 추정)
    # 이미지 + 텍스트 구조로 되어 있음. '총 (9)' 같은 텍스트가 있으므로 리스트가 존재함.
    # 각 항목은 링크, 이미지, 제목, 기간 정보를 포함함.
    
    # 일반적인 리스트 아이템 선택자 시도
    # .board-list-item, .promotion-item 등
    promo_data = {}
    
    # 텍스트 기반으로 '기간 :'이 포함된 요소들의 부모를 찾아 처리
    # Playwright의 locator 사용
    
    # 스크롤
    try:
        page.mouse.wheel(0, 5000)
        time.sleep(1)
    except: pass

    # 구체적인 셀렉터가 없으므로 링크가 있는 블록을 찾습니다.
    # 보통 프로모션은 <a> 태그 안에 이미지와 텍스트가 묶여있거나, <div>로 감싸져 있음.
    # 텍스트 분석 결과: [이미지] \n [분류] 제목 \n 기간 : ...
    
    # "기간 :" 텍스트를 포함하는 요소들을 찾아서 그 부모 컨테이너를 잡음
    items = page.locator("div:has-text('기간 :')").all()
    
    # 만약 위의 방식이 너무 광범위하다면, 이미지와 텍스트가 함께 있는 링크를 찾음
    # .list_content, .event_list 등으로 추정되지만, 안전하게 page 내의 주요 컨텐츠 영역 스캔
    
    # 더 확실한 방법: 페이지 내의 모든 'a' 태그 중 href가 있고 이미지를 포함하며 텍스트가 있는 것
    links = page.query_selector_all("a")
    
    # 상세 페이지 크롤링을 위한 링크 리스트 (URL 또는 Element Handle)
    # href가 '#'인 경우 클릭해서 이동해야 하므로, 요소 자체를 식별할 방법 필요
    # 그러나 클릭 후 뒤로가기는 불안정할 수 있으므로, href가 있는 것만 수집하거나
    # onclick 이벤트를 분석하는 것이 좋으나 복잡함.
    # 대안: 목록에 있는 '제목'을 클릭 -> 새 탭에서 열기 시도 -> 안되면 현재 탭 이동 후 뒤로가기
    
    # 전략: 유효한 프로모션 항목(텍스트 기준)을 찾아서 리스트업
    promo_items = []
    
    # "기간 :" 이 포함된 텍스트를 가진 a 태그 또는 그 부모 찾기
    candidates = page.query_selector_all("a")
    for link_el in candidates:
        try:
            text = link_el.inner_text().strip()
            if not text or len(text) < 5: continue
            if "기간 :" not in text and "프로모션" not in text: continue
            
            # href 확인
            href = link_el.get_attribute("href")
            # js로 이동하는 경우 (href='#' or 'javascript:...')
            is_js_link = not href or href == "#" or "javascript" in href
            
            promo_items.append({
                "text": text.split('\n')[0].strip(), # 제목만
                "element": link_el, # 나중에 클릭용 (주의: DOM 변경되면 유효하지 않을 수 있음)
                "href": href,
                "is_js": is_js_link
            })
        except: continue

    print(f"  총 {len(promo_items)}개의 프로모션 항목을 발견했습니다.")

    # 상세 페이지 방문 및 상품 추출
    # DOM이 변경되는 것을 막기 위해, 매번 페이지를 새로고침하거나 하지 않고
    # 새 탭(context.new_page)을 열어서 URL로 가거나, 
    # JS 링크의 경우 클릭 로직을 신중하게 처리해야 함.
    # 하지만 Playwright에서 element handle은 페이지가 바뀌면 끊김.
    
    # 가장 확실한 방법: 메인 루프에서 매번 목록 페이지로 돌아오기
    
    count = 0
    for i in range(len(promo_items)):
        # Stale Element 방지를 위해 목록 페이지 재진입 (안전 제일)
        if i > 0:
            try:
                page.goto("https://www.amway.co.kr/notifications/promotion", wait_until="networkidle", timeout=30000)
                time.sleep(1) # 렌더링 대기
            except: 
                print("  (목록 페이지 재로딩 실패, 건너뜀)")
                continue

        # 다시 요소 찾기 (순서대로)
        try:
            # i번째 유효 항목 다시 찾기
            current_candidates = page.query_selector_all("a")
            valid_links = []
            for l in current_candidates:
                t = l.inner_text().strip()
                if t and len(t) > 5 and ("기간 :" in t or "프로모션" in t):
                    valid_links.append(l)
            
            if i >= len(valid_links): 
                break
                
            target_link = valid_links[i]
            target_title = target_link.inner_text().split('\n')[0].strip()
            target_href = target_link.get_attribute("href")
            
            print(f"  [{i+1}/{len(promo_items)}] 프로모션 진입: {target_title}")

            # 클릭하여 상세 이동
            # 새 탭을 여는 것을 시도 (Ctrl+Click) -> 지원 안될 수 있음
            # 그냥 클릭 후 뒤로가기 전략
            
            # 만약 href가 있고 http로 시작하면 goto가 빠름
            if target_href and target_href.startswith("http"):
                 page.goto(target_href, wait_until="networkidle", timeout=30000)
            elif target_href and target_href.startswith("/"):
                 page.goto("https://www.amway.co.kr" + target_href, wait_until="networkidle", timeout=30000)
            else:
                 # JS 클릭
                 target_link.click()
                 page.wait_for_load_state("networkidle", timeout=30000)

            # --- 상세 페이지 도착 ---
            
            # 1. 페이지 내의 상품 링크 찾기 (/shop/ 으로 시작하는 링크)
            # 프로모션 페이지 내에는 상품 목록이 '관련 제품'이나 본문에 링크로 걸려있음.
            # .product-list, .box_product 등을 우선 찾고, 없으면 일반 링크 탐색
            
            # 잠시 스크롤
            page.mouse.wheel(0, 3000)
            time.sleep(1)

            # 상품 카드 (.product_item) 우선 검색 (일반적인 상품 목록 패턴)
            products = page.query_selector_all(".product_item")
            if not products:
                products = page.query_selector_all(".box_product")

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
                shop_links = page.query_selector_all("a[href*='/shop/']")
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
            
    print(f"  Found {len(promo_data)} products in promotions.")
    return promo_data

def run_full_crawl(data_callback=None):
    print(f"[{datetime.datetime.now()}] Starting Amway Smart Crawler...")
    
    current_data = {}

    with sync_playwright() as p:
        print("  -> 브라우저를 실행 중입니다... (잠시만 기다려주세요)")
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
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
