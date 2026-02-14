import json
import os
import time
import re
import asyncio
from playwright.async_api import async_playwright
import datetime

DATA_FILE = "amway_products_full.json"
PV_REGEX = re.compile(r"PV\s*:\s*([\d,]+)")
BV_REGEX = re.compile(r"BV\s*:\s*([\d,]+)")

async def discover_category_tabs(page):
    """
    /shop/c/shop 페이지에서 상단 카테고리 탭(영양건강, 뷰티 등)을 수집합니다.
    """
    print("카테고리 탭 탐색 중...")
    try:
        await page.goto("https://www.amway.co.kr/shop/c/shop", wait_until="networkidle", timeout=60000)
    except:
        return []

    categories = []
    
    target_cats = ["영양건강", "뷰티", "퍼스널 케어", "홈리빙", "원포원", "웰니스", "플러스 쇼핑"]
    
    for cat_name in target_cats:
        try:
            # 텍스트로 링크 찾기 (exact match or contains)
            link = (page.get_by_role("link", name=cat_name, exact=True)).first
            if not await link.is_visible():
                # exact fail, try generic
                links = await page.query_selector_all(f"a:has-text('{cat_name}')")
                for l in links:
                    if await l.is_visible():
                        href = await l.get_attribute("href")
                        if href and "/shop/" in href:
                            full_url = "https://www.amway.co.kr" + href if href.startswith("/") else href
                            categories.append({"name": cat_name, "url": full_url})
                            break
            else:
                href = await link.get_attribute("href")
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

async def crawl_category(page, category_info):
    cat_name = category_info['name']
    url = category_info['url']
    
    print(f"Crawling Category: {cat_name} ({url})")
    try:
        await page.goto(url, wait_until="networkidle", timeout=60000)
    except Exception as e:
        print(f"  Error loading page: {e}")
        return {}

    # Wait for products
    try:
        await page.wait_for_selector(".product_item, .box_product", timeout=20000)
    except:
        print("  No products found initially.")
        return {}
    
    # Scroll to load all
    last_height = await page.evaluate("document.body.scrollHeight")
    for i in range(15): # Adequate scrolling
        await page.mouse.wheel(0, 15000)
        
        # Smart wait for height change
        start_wait = time.time()
        while time.time() - start_wait < 2.0:
            current_height = await page.evaluate("document.body.scrollHeight")
            if current_height > last_height:
                break
            await asyncio.sleep(0.1)

        # '더보기' 버튼 처리
        try:
            more_btns = await page.query_selector_all("a.btn_more, button.btn_more")
            for btn in more_btns:
                if await btn.is_visible():
                    await btn.click()
                    # Optimized wait with fallback
                    try:
                        await page.wait_for_load_state("networkidle", timeout=1000)
                    except:
                        await asyncio.sleep(1)
        except: pass

        new_height = await page.evaluate("document.body.scrollHeight")
        if new_height == last_height and i > 2:
            break
        last_height = new_height

    products = await page.query_selector_all(".product_item")
    if not products:
        products = await page.query_selector_all(".box_product")
        
    print(f"  Found {len(products)} products in {cat_name}.")
    
    category_data = {}

    for product in products:
        try:
            # 1. Name
            name_el = await product.query_selector(".text_product-title")
            if not name_el: name_el = await product.query_selector(".product_name")
            name = await name_el.inner_text() if name_el else "Unknown Name"
            name = name.strip()

            # 2. Link & ID
            link_el = await product.query_selector("a")
            link = await link_el.get_attribute("href") if link_el else ""
            if link and link.startswith("/"):
                link = "https://www.amway.co.kr" + link
            
            product_id = link.split('/')[-1] if link else name

            # 3. Price
            price_el = await product.query_selector(".text_price-data")
            if not price_el: price_el = await product.query_selector(".price")
            price = await price_el.inner_text() if price_el else "0"
            price = price.strip()

            # 4. Image
            img_el = await product.query_selector("img")
            img_src = await img_el.get_attribute("src") if img_el else ""
            if img_src and img_src.startswith("/"):
                img_src = "https://www.amway.co.kr" + img_src

            # 5. Status
            status_text = await product.inner_text()
            status = "판매중"
            if "일시품절" in status_text: status = "일시품절"
            elif "품절" in status_text: status = "품절"
            elif "단종" in status_text: status = "단종"

            # 6. PV / BV
            pv = "0"
            bv = "0"
            
            data_el = await product.query_selector("input[name='productTealiumTagInfo']")
            if not data_el:
                data_el = await product.query_selector(".js-addtocart-v2")
            
            if data_el:
                raw_pv = await data_el.get_attribute("data-product-point-value")
                raw_bv = await data_el.get_attribute("data-product-business-volume")
                
                if raw_pv:
                    pv = str(int(float(raw_pv)))
                if raw_bv:
                    bv = str(int(float(raw_bv)))
            
            if pv == "0":
                pv_match = PV_REGEX.search(status_text)
                if pv_match: pv = pv_match.group(1).replace(",", "")

            if bv == "0":
                bv_match = BV_REGEX.search(status_text)
                if bv_match: bv = bv_match.group(1).replace(",", "")
            
            # Smart Order Handling
            final_category = cat_name
            if "스마트 오더" in name or "스마트오더" in name:
                final_category = "스마트 오더"

            category_data[product_id] = {
                "id": product_id,
                "name": name,
                "price": price,
                "status": status,
                "link": link,
                "image": img_src,
                "category": final_category,
                "sub_category": "",
                "pv": pv,
                "bv": bv
            }
        except:
            continue

    return category_data

async def process_promotion_item(context, item, sem):
    async with sem:
        target_url = item["url"]
        target_title = item["text"]

        # print(f"  [Promo] Processing: {target_title}") # Too verbose?
        page = await context.new_page()
        promo_data = {}

        try:
            await page.goto(target_url, wait_until="networkidle", timeout=30000)
            
            # Scroll
            await page.mouse.wheel(0, 3000)
            await asyncio.sleep(1)

            products = await page.query_selector_all(".product_item")
            if not products:
                products = await page.query_selector_all(".box_product")

            if products:
                for product in products:
                    try:
                        name_el = await product.query_selector(".text_product-title")
                        if not name_el: name_el = await product.query_selector(".product_name")
                        name = await name_el.inner_text() if name_el else "Unknown Name"
                        name = name.strip()

                        link_el = await product.query_selector("a")
                        p_href = await link_el.get_attribute("href") if link_el else ""
                        if not p_href or "/shop/" not in p_href: continue
                        
                        full_p_url = "https://www.amway.co.kr" + p_href if p_href.startswith("/") else p_href
                        product_id = full_p_url.split('/')[-1]

                        price_el = await product.query_selector(".text_price-data")
                        if not price_el: price_el = await product.query_selector(".price")
                        price = await price_el.inner_text() if price_el else "0"
                        price = price.strip()

                        pv = "0"
                        bv = "0"
                        
                        data_el = await product.query_selector("input[name='productTealiumTagInfo']")
                        if not data_el:
                            data_el = await product.query_selector(".js-addtocart-v2")
                        
                        if data_el:
                            raw_pv = await data_el.get_attribute("data-product-point-value")
                            raw_bv = await data_el.get_attribute("data-product-business-volume")
                            
                            if raw_pv: pv = str(int(float(raw_pv)))
                            if raw_bv: bv = str(int(float(raw_bv)))
                        
                        if pv == "0":
                            status_text = await product.inner_text()
                            pv_match = PV_REGEX.search(status_text)
                            if pv_match: pv = pv_match.group(1).replace(",", "")
                            
                            bv_match = BV_REGEX.search(status_text)
                            if bv_match: bv = bv_match.group(1).replace(",", "")

                        img_el = await product.query_selector("img")
                        img_src = await img_el.get_attribute("src") if img_el else ""
                        if img_src and img_src.startswith("/"):
                            img_src = "https://www.amway.co.kr" + img_src
                        
                        if product_id not in promo_data:
                            promo_data[product_id] = {
                                "id": product_id,
                                "name": name,
                                "price": price,
                                "status": "진행중",
                                "link": full_p_url,
                                "image": img_src,
                                "category": "이벤트",
                                "sub_category": "",
                                "pv": pv,
                                "bv": bv
                            }
                    except: continue
            else:
                # Link-only check
                shop_links = await page.query_selector_all("a[href*='/shop/']")
                for sl in shop_links:
                    try:
                        href = await sl.get_attribute("href")
                        if "/shop/c/" in href: continue
                        if "/p/" not in href and not href.split('/')[-1].isdigit(): continue

                        full_p_url = "https://www.amway.co.kr" + href if href.startswith("/") else href
                        product_id = full_p_url.split('/')[-1]
                        
                        name = await sl.inner_text()
                        name = name.strip()
                        if not name:
                            img = await sl.query_selector("img")
                            if img: name = await img.get_attribute("alt") or "이벤트 상품"
                        if not name: name = "이벤트 상품"

                        if product_id not in promo_data:
                            promo_data[product_id] = {
                                "id": product_id,
                                "name": name,
                                "price": "0",
                                "status": "진행중",
                                "link": full_p_url,
                                "image": "",
                                "category": "이벤트",
                                "sub_category": "",
                                "pv": "0",
                                "bv": "0"
                            }
                    except: continue

        except Exception as e:
            print(f"    -> [Promo Error] {target_title}: {e}")
        finally:
            await page.close()
            
        return promo_data

async def crawl_promotions(page, context):
    """
    /notifications/promotion 페이지를 크롤링하여 이벤트 목록을 수집합니다.
    """
    print("Crawling Promotions (Optimized - Async)...")
    try:
        await page.goto("https://www.amway.co.kr/notifications/promotion", wait_until="networkidle", timeout=60000)
    except Exception as e:
        print(f"  Error loading promotion page: {e}")
        return {}

    # 스크롤
    try:
        await page.mouse.wheel(0, 5000)
        await asyncio.sleep(1)
    except: pass

    promo_items = []
    candidates = await page.query_selector_all("a")

    for link_el in candidates:
        try:
            text = await link_el.inner_text()
            text = text.strip()
            if not text or len(text) < 5: continue
            if "기간 :" not in text and "프로모션" not in text: continue

            if "진행중인" in text or "종료된" in text: continue

            href = await link_el.get_attribute("href")
            data_code = await link_el.get_attribute("data-code")
            notice_type = await link_el.get_attribute("data-notice-type")

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

    print(f"  총 {len(promo_items)}개의 유효한 프로모션 항목을 발견했습니다. (Concurrent Processing Start)")

    # Concurrent Processing
    sem = asyncio.Semaphore(5) # Limit concurrency
    tasks = [process_promotion_item(context, item, sem) for item in promo_items]

    # Gather results
    results = await asyncio.gather(*tasks)

    combined_data = {}
    for r in results:
        combined_data.update(r)

    print(f"  Found {len(combined_data)} products in promotions.")
    return combined_data

async def run_full_crawl(data_callback=None):
    print(f"[{datetime.datetime.now()}] Starting Amway Smart Crawler (Async)...")
    
    current_data = {}
    save_lock = asyncio.Lock()
    loop = asyncio.get_running_loop()

    async with async_playwright() as p:
        print("  -> 브라우저를 실행 중입니다... (잠시만 기다려주세요)")
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        # 1. 카테고리 탭 발견
        cats = await discover_category_tabs(page)
        
        if not cats:
            print("카테고리를 찾지 못했습니다. 기본 URL로 시도합니다.")
            cats = [{"name": "전체상품", "url": "https://www.amway.co.kr/shop/c/shop"}]

        # 2. 각 카테고리 크롤링 (Concurrent Categories)
        cat_sem = asyncio.Semaphore(3) # Max 3 concurrent categories

        async def process_category(cat):
            async with cat_sem:
                # Use a new page for each category to ensure isolation
                cat_page = await context.new_page()
                try:
                    cat_products = await crawl_category(cat_page, cat)
                    if cat_products:
                        if data_callback:
                            async with save_lock:
                                print(f"  >> Sending {len(cat_products)} items to sync ({cat['name']})...")
                                # Run sync callback in executor
                                await loop.run_in_executor(None, data_callback, cat_products)
                        return cat_products
                    return {}
                finally:
                    await cat_page.close()

        cat_tasks = [process_category(cat) for cat in cats]
        cat_results = await asyncio.gather(*cat_tasks)

        for r in cat_results:
            current_data.update(r)

        # 3. 프로모션 크롤링
        # Use existing page (it's idle now) or create new one. We can pass context.
        promo_products = await crawl_promotions(page, context)
        if promo_products:
            current_data.update(promo_products)
            if data_callback:
                async with save_lock:
                    print(f"  >> Sending {len(promo_products)} promotions to sync...")
                    await loop.run_in_executor(None, data_callback, promo_products)
            
        await browser.close()

    print(f"Total products scraped: {len(current_data)}")
    save_current_state(current_data)
    return current_data

if __name__ == "__main__":
    asyncio.run(run_full_crawl())
