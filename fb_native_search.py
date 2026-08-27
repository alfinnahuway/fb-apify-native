#!/usr/bin/env python3
"""
Facebook Native Keyword Search + Comment Scraper
=================================================
Phase 1: Native keyword search via Selenium + cookies (FREE, $0)
  - Open facebook.com/search/posts/?q=keyword
  - Extract post IDs + text + account names from page source
  - Scroll to load more results
  - Normalize to /account/posts/ID URLs

Phase 2: Native comment scraping per post (FREE, $0)
  - Open each post URL with cookies
  - Click "X comments" button
  - Scroll + extract comments + replies

Cost: $0 (100% native, no Apify needed)
"""

import argparse
import json
import os
import random
import re
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings('ignore')

# ─── CONFIG ───────────────────────────────────────────────────────────────────

CHROME_BINARY = "/tmp/cft/chrome-linux64/chrome"
DRIVER_PATH = "/tmp/cft/chromedriver-linux64/chromedriver"
COOKIE_FILE = str(Path(__file__).parent / "fb_cookies.txt")

# DataImpulse proxy
_proxy_user = os.environ.get("DATAIMPULSE_USER", "3cfb78986c362b6168ff__cr.id")
_proxy_pass = os.environ.get("DATAIMPULSE_PASS", "581917ac7809e35e")
_proxy_host = "74.81.81.81"
PROXY_PORTS = [10000, 10001, 10002, 10003, 10004, 10005, 10006, 10007, 10008, 10009]


# ─── BROWSER SETUP ────────────────────────────────────────────────────────────

def get_browser(proxy_port=None):
    """Create Selenium browser with proxy + anti-detection."""
    from seleniumwire import webdriver
    from selenium.webdriver.chrome.service import Service

    if proxy_port is None:
        proxy_port = random.choice(PROXY_PORTS)
    
    proxy_url = f"http://{_proxy_user}:{_proxy_pass}@{_proxy_host}:{proxy_port}"
    
    chrome_options = webdriver.ChromeOptions()
    for arg in [
        "--disable-notifications",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1920,1080",
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    ]:
        chrome_options.add_argument(arg)
    chrome_options.binary_location = CHROME_BINARY
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(
        service=Service(DRIVER_PATH),
        options=chrome_options,
        seleniumwire_options={
            "proxy": {
                "http": proxy_url,
                "https": proxy_url,
            }
        }
    )
    
    # Anti-detection
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['id', 'en-US', 'en']});
        """
    })
    
    return driver


def load_cookies(driver, cookie_file):
    """Load Netscape format cookies into browser."""
    if not os.path.exists(cookie_file):
        print(f"  ⚠️ Cookie file not found: {cookie_file}")
        return False
    
    count = 0
    with open(cookie_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                try:
                    cookie = {
                        "name": parts[5],
                        "value": parts[6],
                        "domain": parts[0],
                        "path": parts[2],
                        "secure": parts[3] == "TRUE",
                    }
                    if parts[4] != "0":
                        cookie["expiry"] = int(parts[4])
                    driver.add_cookie(cookie)
                    count += 1
                except:
                    pass
    print(f"  ✅ Loaded {count} cookies")
    return True


def verify_login(driver):
    """Verify cookies are working."""
    driver.get("https://www.facebook.com")
    time.sleep(5)
    cookies = driver.get_cookies()
    has_cuser = any(c.get("name") == "c_user" for c in cookies)
    if has_cuser:
        print("  ✅ Logged in via cookies!")
    else:
        print("  ⚠️ Cookies might be invalid")
    return has_cuser


# ─── PHASE 1: NATIVE KEYWORD SEARCH ──────────────────────────────────────────

def native_keyword_search(driver, keyword, max_posts=10, max_scrolls=10, max_retries=3):
    """
    Search Facebook posts by keyword using native Selenium + cookies.
    
    Extracts post IDs from page source using regex patterns.
    Facebook search page uses SPA rendering — post data is embedded in JSON.
    
    Note: Facebook intermittently blocks search page. If blank page detected,
    retry with fresh page load.
    """
    from selenium.webdriver.common.by import By
    
    search_url = f"https://www.facebook.com/search/posts/?q={keyword.replace(' ', '%20')}"
    
    for attempt in range(max_retries):
        print(f"\n  Attempt {attempt+1}/{max_retries}")
        print(f"  Opening: {search_url}")
        driver.get(search_url)
        time.sleep(random.uniform(10, 15))
        
        title = driver.title
        src = driver.page_source
        print(f"  Title: {title}")
        print(f"  Page source: {len(src):,} chars")
        
        if len(src) < 5000:
            print(f"  ⚠️ Blank page ({len(src)} chars) — Facebook blocking search")
            if attempt < max_retries - 1:
                print(f"  Retrying in 5s...")
                time.sleep(5)
                # Reload FB to refresh cookies
                driver.get("https://www.facebook.com")
                time.sleep(5)
            continue
        
        if "login" in driver.current_url.lower():
            print("  ⚠️ Redirected to login")
            continue
        
        # Success — extract posts
        posts = []
        seen_ids = set()
        posts.extend(extract_posts_from_source(src, seen_ids))
        
        print(f"  Initial posts: {len(posts)}")
        
        # Scroll to load more
        for scroll in range(max_scrolls):
            if len(posts) >= max_posts:
                break
                
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(random.uniform(3, 5))
            
            src = driver.page_source
            new_posts = extract_posts_from_source(src, seen_ids)
            
            if new_posts:
                posts.extend(new_posts)
                print(f"  Scroll {scroll+1} | Total: {len(posts)} posts")
            else:
                print(f"  Scroll {scroll+1} | No new posts (total: {len(posts)})")
        
        posts = posts[:max_posts]
        
        print(f"\n  Posts found:")
        for i, post in enumerate(posts):
            text_preview = post.get("text", "")[:80]
            print(f"    [{i+1}] ID: {post['post_id']} | Account: {post.get('account', '?')} | {text_preview}")
        
        return posts
    
    print(f"\n  ❌ All {max_retries} attempts failed — Facebook blocking search page")
    print(f"  This is intermittent — try again later or use --account mode")
    return []


def extract_posts_from_source(src, seen_ids):
    """Extract post data from Facebook search page source using regex."""
    posts = []
    
    # Pattern 1: "post_id":"123456789"
    post_ids = set(re.findall(r'"post_id":"?(\d{10,})"?', src))
    
    for pid in post_ids:
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        
        # Find context around this post_id
        idx = src.find(pid)
        context = src[max(0, idx-2000):idx+2000] if idx > 0 else ""
        
        # Extract account name
        account = ""
        account_match = re.search(r'"username"?\s*:\s*"([^"]+)"', context)
        if account_match:
            account = account_match.group(1)
        
        if not account:
            account_match = re.search(r'facebook\.com/([a-zA-Z0-9_.]+)[/?"]', context)
            if account_match:
                candidate = account_match.group(1)
                if candidate not in ("search", "reel", "videos", "photos", "watch", "groups", "help", "login"):
                    account = candidate
        
        # Extract post text
        text = ""
        text_match = re.search(r'"text"\s*:\s*"([^"]{20,500})"', context)
        if text_match:
            text = text_match.group(1).replace("\\n", " ").replace("\\/", "/")
        
        # Extract page name
        page_name = ""
        page_match = re.search(r'"pageName"?\s*:\s*"([^"]+)"', context)
        if page_match:
            page_name = page_match.group(1)
        
        # Build normalized URL
        if account and pid.isdigit():
            post_url = f"https://www.facebook.com/{account}/posts/{pid}"
        elif pid.isdigit():
            post_url = f"https://www.facebook.com/{pid}"
        else:
            post_url = f"https://www.facebook.com/{pid}"
        
        posts.append({
            "post_id": pid,
            "account": account or page_name,
            "post_url": post_url,
            "text": text,
            "page_name": page_name,
        })
    
    # Pattern 2: pfbid IDs
    pfbids = set(re.findall(r'(pfbid[A-Za-z0-9]{20,})', src))
    
    for pfbid in pfbids:
        if pfbid in seen_ids:
            continue
        seen_ids.add(pfbid)
        
        # Find context
        idx = src.find(pfbid)
        context = src[max(0, idx-2000):idx+2000] if idx > 0 else ""
        
        # Extract account
        account = ""
        account_match = re.search(r'"username"?\s*:\s*"([^"]+)"', context)
        if account_match:
            account = account_match.group(1)
        
        if not account:
            account_match = re.search(r'facebook\.com/([a-zA-Z0-9_.]+)[/?"]', context)
            if account_match:
                candidate = account_match.group(1)
                if candidate not in ("search", "reel", "videos", "photos", "watch", "groups", "help", "login"):
                    account = candidate
        
        # Extract text
        text = ""
        text_match = re.search(r'"text"\s*:\s*"([^"]{20,500})"', context)
        if text_match:
            text = text_match.group(1).replace("\\n", " ").replace("\\/", "/")
        
        # For pfbid, try /account/posts/pfbid format
        if account:
            post_url = f"https://www.facebook.com/{account}/posts/{pfbid}"
        else:
            post_url = f"https://www.facebook.com/{pfbid}"
        
        posts.append({
            "post_id": pfbid,
            "account": account,
            "post_url": post_url,
            "text": text,
        })
    
    return posts


# ─── PHASE 2: NATIVE COMMENT SCRAPING ─────────────────────────────────────────

def scrape_post_comments(driver, post, max_comments=150):
    """Scrape comments from a single Facebook post."""
    from selenium.webdriver.common.by import By
    
    url = post["post_url"]
    print(f"\n{'─'*60}")
    print(f"  Post: {url[:70]}")
    print(f"  Account: {post.get('account', '?')} | Text: {post.get('text', '')[:60]}")
    print(f"{'─'*60}")
    
    driver.get(url)
    time.sleep(random.uniform(5, 8))
    
    # Click "X comments" button
    comment_btn = None
    try:
        elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'comments') or contains(text(), 'komentar')]")
        for elem in elements:
            text = elem.text.strip().lower()
            if re.search(r'\d+\s*(comments|komentar)', text):
                comment_btn = elem
                print(f"  ✅ Found: '{elem.text}'")
                break
    except:
        pass
    
    if comment_btn:
        try:
            driver.execute_script("arguments[0].click();", comment_btn)
            time.sleep(3)
        except:
            pass
    
    # Scroll and collect comments
    comments = []
    replies = []
    no_change_count = 0
    
    for scroll in range(1, 41):  # Up to 40 scrolls
        # Find comment elements
        comment_elements = []
        
        # Selector 1: aria-label
        try:
            comment_elements = driver.find_elements(By.CSS_SELECTOR, "div[aria-label='Komentar' i], div[aria-label='Comment' i]")
        except:
            pass
        
        # Selector 2: data-testid
        if not comment_elements:
            try:
                comment_elements = driver.find_elements(By.CSS_SELECTOR, "div[data-testid='UFI2Comment/root']")
            except:
                pass
        
        # Selector 3: role=article
        if not comment_elements:
            try:
                all_articles = driver.find_elements(By.CSS_SELECTOR, "div[role='article']")
                comment_elements = [a for a in all_articles if len(a.text) > 10]
            except:
                pass
        
        # Parse new comments
        if len(comments) < len(comment_elements):
            for elem in comment_elements[len(comments):]:
                try:
                    text = elem.text.strip()
                    if text and len(text) > 5:
                        lines = text.split('\n')
                        commenter = lines[0].strip() if lines else "Unknown"
                        comment_text = '\n'.join(lines[1:]) if len(lines) > 1 else text
                        comment_text = re.sub(r'\b(suka|like|reply|balas|lihat balasan|view more replies)\b.*$', '', comment_text, flags=re.IGNORECASE).strip()
                        
                        likes = 0
                        likes_match = re.search(r'(\d+)\s*(suka|like)', text.lower())
                        if likes_match:
                            likes = int(likes_match.group(1))
                        
                        ts = ""
                        ts_match = re.search(r'(\d+\s*(jam|menit|detik|hari|minggu|bulan|tahun|h|m|d|w|mo|y|s)\b|(?:Just now|Baru saja|\d+h|\d+m|\d+d))', text)
                        if ts_match:
                            ts = ts_match.group(0)
                        
                        if comment_text and len(comment_text) > 3:
                            comments.append({
                                "commenter": commenter,
                                "text": comment_text[:500],
                                "likes": likes,
                                "timestamp": ts,
                            })
                except:
                    pass
        
        print(f"  Scroll {scroll} | Comments: {len(comments)}")
        
        if len(comments) >= max_comments:
            break
        
        prev_count = len(comments)
        
        # Scroll down
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(random.uniform(2, 4))
        
        # Click reply buttons
        if scroll % 3 == 0:
            try:
                reply_btns = driver.find_elements(By.XPATH, "//span[contains(text(), 'Balas') or contains(text(), 'Reply') or contains(text(), 'Lihat balasan') or contains(text(), 'View')]")
                for btn in reply_btns[:5]:
                    try:
                        driver.execute_script("arguments[0].click();", btn)
                        time.sleep(1)
                    except:
                        pass
            except:
                pass
        
        if len(comments) == prev_count:
            no_change_count += 1
            if no_change_count >= 10:
                break
        else:
            no_change_count = 0
    
    # Collect replies
    try:
        reply_elements = driver.find_elements(By.CSS_SELECTOR, "div[role='article'] div[role='article']")
        for elem in reply_elements:
            try:
                text = elem.text.strip()
                if text and len(text) > 5:
                    lines = text.split('\n')
                    replier = lines[0].strip() if lines else "Unknown"
                    reply_text = '\n'.join(lines[1:]) if len(lines) > 1 else text
                    reply_text = re.sub(r'\b(suka|like|reply|balas)\b.*$', '', reply_text, flags=re.IGNORECASE).strip()
                    
                    if reply_text and len(reply_text) > 3:
                        replies.append({
                            "replier": replier,
                            "text": reply_text[:500],
                        })
            except:
                pass
    except:
        pass
    
    post["comments"] = comments
    post["replies"] = replies
    post["total_comments_scraped"] = len(comments)
    post["total_replies_scraped"] = len(replies)
    
    print(f"  ✅ {len(comments)} comments + {len(replies)} replies")
    return post


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Facebook Native Keyword Search + Comment Scraper")
    parser.add_argument("--keyword", required=True, help="Search keyword")
    parser.add_argument("--max-posts", type=int, default=10, help="Max posts to find")
    parser.add_argument("--max-comments", type=int, default=150, help="Max comments per post")
    parser.add_argument("--max-scrolls", type=int, default=10, help="Max scrolls during search")
    parser.add_argument("--output", default="output.json", help="Output JSON file")
    args = parser.parse_args()
    
    print("=" * 60)
    print("  FACEBOOK NATIVE KEYWORD SEARCH + COMMENT SCRAPER")
    print("  100% Native — No Apify needed — $0 cost")
    print("=" * 60)
    print(f"  Keyword: {args.keyword}")
    print(f"  Max posts: {args.max_posts}")
    print(f"  Max comments: {args.max_comments}")
    print(f"  Output: {args.output}")
    
    # ── PHASE 1: KEYWORD SEARCH ────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  PHASE 1: NATIVE KEYWORD SEARCH")
    print("═" * 60)
    
    driver = get_browser()
    
    print("  Loading Facebook + cookies...")
    driver.get("https://www.facebook.com")
    time.sleep(5)
    load_cookies(driver, COOKIE_FILE)
    
    # Reload with cookies — go to profile page to verify
    driver.get("https://www.facebook.com/PrabowoSubianto")
    time.sleep(8)
    
    cookies = driver.get_cookies()
    has_cuser = any(c.get("name") == "c_user" for c in cookies)
    if has_cuser:
        print("  ✅ Logged in via cookies!")
    else:
        print("  ⚠️ Cookies might be invalid — trying search anyway")
    
    posts = native_keyword_search(driver, args.keyword, args.max_posts, args.max_scrolls)
    
    if not posts:
        print("\n❌ No posts found!")
        driver.quit()
        sys.exit(1)
    
    # ── PHASE 2: COMMENT SCRAPING ──────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  PHASE 2: NATIVE COMMENT SCRAPING")
    print("═" * 60)
    
    for i, post in enumerate(posts):
        print(f"\n  Post {i+1}/{len(posts)}")
        try:
            scrape_post_comments(driver, post, args.max_comments)
        except Exception as e:
            print(f"  ❌ Error: {e}")
            post["comments"] = []
            post["replies"] = []
        
        time.sleep(random.uniform(3, 6))
    
    driver.quit()
    
    # ── SAVE ──────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  SAVING RESULTS")
    print("═" * 60)
    
    total_comments = sum(p.get("total_comments_scraped", 0) for p in posts)
    total_replies = sum(p.get("total_replies_scraped", 0) for p in posts)
    
    output = {
        "metadata": {
            "keyword": args.keyword,
            "method": "native_keyword_search",
            "total_posts": len(posts),
            "total_comments": total_comments,
            "total_replies": total_replies,
            "total_data_points": total_comments + total_replies,
            "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "cost": "$0.00",
        },
        "posts": posts,
    }
    
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n  ✅ Saved to: {args.output}")
    print(f"  Posts: {len(posts)}")
    print(f"  Comments: {total_comments}")
    print(f"  Replies: {total_replies}")
    print(f"  Total data points: {total_comments + total_replies}")
    print(f"  Cost: $0.00")
    print("\nDone!")


if __name__ == "__main__":
    main()
