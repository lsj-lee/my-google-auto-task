import json
import os
import time
import re
import asyncio
from playwright.async_api import async_playwright
import datetime

DATA_FILE = "amway_products_full.json"

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
    
    target_cats = ["영양건강", "뷰티", "퍼스널 케어", "홈리빙", "원포원", "웰니스", "플러스 쇼핑", "장바구니 스마트 오더", "스마트 오더"]
    
    for cat_name in target_cats:
        try:
            # 텍스트로 링크 찾기 (exact match or contains)
            link = page.get_by_role("link", name=cat_name, exact=True).first
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

        await asyncio.sleep(0.2)

        # '더보기' 버튼 처리
        try:
            more_btns = await page.query_selector_all("a.btn_more, button.btn_more")
            for btn in more_btns:
                if await btn.is_visible():
                    await btn.click()
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
                pv_match = re.search(r"PV\s*:\s*([\d,]+)", status_text)
                if pv_match: pv = pv_match.group(1).replace(",", "")

            if bv == "0":
                bv_match = re.search(r"BV\s*:\s*([\d,]+)", status_text)
                if bv_match: bv = bv_match.group(1).replace(",", "")
            
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

async def crawl_promotions(page):
    """
    /notifications/promotion 페이지를 크롤링하여 이벤트 목록을 수집합니다.
    """
    print("Crawling Promotions...")
    try:
        await page.goto("https://www.amway.co.kr/notifications/promotion", wait_until="networkidle", timeout=60000)
    except Exception as e:
        print(f"  Error loading promotion page: {e}")
        return {}

    promo_data = {}
    
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
            
            href = await link_el.get_attribute("href")
            is_js_link = not href or href == "#" or "javascript" in href
            
            promo_items.append({
                "text": text.split('\n')[0].strip(),
                "element": link_el,
                "href": href,
                "is_js": is_js_link
            })
        except: continue

    print(f"  총 {len(promo_items)}개의 프로모션 항목을 발견했습니다.")

    count = 0
    for i, item in enumerate(promo_items):
        target_href = item["href"]
        is_js = item["is_js"]
        target_title = item["text"]

        try:
            if not is_js and target_href:
                full_url = target_href
                if full_url.startswith("/"):
                    full_url = "https://www.amway.co.kr" + full_url
                
                print(f"  [{i+1}/{len(promo_items)}] 프로모션 진입 (Direct): {target_title}")
                await page.goto(full_url, wait_until="networkidle", timeout=30000)
            else:
                print(f"  [{i+1}/{len(promo_items)}] 프로모션 진입 (Fallback): {target_title}")
                await page.goto("https://www.amway.co.kr/notifications/promotion", wait_until="networkidle", timeout=30000)
                await asyncio.sleep(1)

                current_candidates = await page.query_selector_all("a")
                valid_links = []
                for l in current_candidates:
                    t = await l.inner_text()
                    t = t.strip()
                    if t and len(t) > 5 and ("기간 :" in t or "프로모션" in t):
                        valid_links.append(l)

                if i < len(valid_links):
                    await valid_links[i].click()
                    await page.wait_for_load_state("networkidle", timeout=30000)
                else:
                    print("    -> 요소 재탐색 실패")
                    continue

            # --- 상세 페이지 도착 ---
            
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
                            
                            if raw_pv:
                                pv = str(int(float(raw_pv)))
                            if raw_bv:
                                bv = str(int(float(raw_bv)))
                        
                        if pv == "0":
                            status_text = await product.inner_text()
                            pv_match = re.search(r"PV\s*:\s*([\d,]+)", status_text)
                            if pv_match: pv = pv_match.group(1).replace(",", "")
                            
                            bv_match = re.search(r"BV\s*:\s*([\d,]+)", status_text)
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
            print(f"    -> 상세 페이지 로드 실패: {e}")
            
    print(f"  Found {len(promo_data)} products in promotions.")
    return promo_data

async def run_full_crawl(data_callback=None):
    print(f"[{datetime.datetime.now()}] Starting Amway Smart Crawler (Async)...")
    
    current_data = {}

    async with async_playwright() as p:
        print("  -> 브라우저를 실행 중입니다... (잠시만 기다려주세요)")
        browser = await p.chromium.launch(headless=True)
        # Create context
        context = await browser.new_context()
        
        # 1. Discover categories using one page
        page = await context.new_page()
        cats = await discover_category_tabs(page)
        
        if not cats:
            print("카테고리를 찾지 못했습니다. 기본 URL로 시도합니다.")
            cats = [{"name": "전체상품", "url": "https://www.amway.co.kr/shop/c/shop"}]

        # We can close this initial page as we will open new ones for each category
        await page.close()

        # 2. 각 카테고리 크롤링 (Concurrent)
        # Limit concurrency to avoid overloading the server or local resource
        semaphore = asyncio.Semaphore(5)

        async def crawl_task(cat):
            async with semaphore:
                # Open a new page for this category
                cat_page = await context.new_page()
                try:
                    result = await crawl_category(cat_page, cat)
                    if data_callback and result:
                        print(f"  >> Sending {len(result)} items to sync ({cat['name']})...")
                        if asyncio.iscoroutinefunction(data_callback):
                            await data_callback(result)
                        else:
                            data_callback(result)
                    return result
                finally:
                    await cat_page.close()

        tasks = [crawl_task(cat) for cat in cats]
        results = await asyncio.gather(*tasks)

        for res in results:
            current_data.update(res)

        # 3. [추가] 프로모션(이벤트) 크롤링
        # Run this separately or also concurrently. Let's run it after categories to be safe or just standalone.
        # It needs a page.
        promo_page = await context.new_page()
        try:
            promo_products = await crawl_promotions(promo_page)
            if promo_products:
                if data_callback:
                    print(f"  >> Sending {len(promo_products)} promotions to sync...")
                    if asyncio.iscoroutinefunction(data_callback):
                        await data_callback(promo_products)
                    else:
                        data_callback(promo_products)
                current_data.update(promo_products)
        finally:
            await promo_page.close()
            
        await browser.close()

    print(f"Total products scraped: {len(current_data)}")
    save_current_state(current_data)
    return current_data

if __name__ == "__main__":
    asyncio.run(run_full_crawl())
