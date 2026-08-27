#!/usr/bin/env python3
"""
Facebook Apify + Native Comment Scraper
=======================================
Phase 1: Apify search by keyword → get post URLs + metadata
Phase 2: Normalize URLs to /account/posts/ID → native Selenium scrape comments + replies

Uses: Apify (search), Selenium + DataImpulse proxy (comments), Facebook cookies (auth)
Cost: $0.006/post (Apify) + $0 (comments)
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
PROXY_PORTS = [10000, 10001, 10002, 10003, 10004, 10005, 10006, 10007]

# Apify
APIFY_KEY = os.environ.get("APIFY_API_KEYS", "")
APIFY_ACTOR = "scrapier/facebook-posts-search-scraper"


# ─── PHASE 1: APIFY SEARCH ────────────────────────────────────────────────────

def apify_search(keyword, max_posts=10):
    """Search Facebook posts by keyword via Apify."""
    from apify_client import ApifyClient

    if not APIFY_KEY:
        print("❌ No APIFY_API_KEYS set")
        return []

    client = ApifyClient(APIFY_KEY)
    print(f"  Running Apify actor: {APIFY_ACTOR}")
    print(f"  Keyword: '{keyword}'")
    print(f"  Max posts: {max_posts}")

    run_input = {
        "searchQueries": [keyword],
        "maxResults": max_posts,
    }

    try:
        run = client.actor(APIFY_ACTOR).call(run_input=run_input, timeout_secs=120)
    except Exception as e:
        print(f"  ❌ Apify error: {e}")
        return []

    if not run or run.get("status") != "SUCCEEDED":
        print(f"  ❌ Apify run failed: {run.get('status') if run else 'no run'}")
        return []

    results = list(client.dataset(run.get("defaultDatasetId")).iterate_items())
    print(f"  ✅ Found {len(results)} posts via Apify")
    return results


def normalize_url(post):
    """
    Aggressive URL normalization.
    Convert ALL URL formats to /account/posts/ID for comment rendering.
    
    Handles: /reel/, /videos/, /photos/, /posts/, bare ID, pfbid
    """
    post_id = str(post.get("postId", post.get("postFacebookId", "")))
    original_url = post.get("url", post.get("link", ""))
    author_url = post.get("facebookUrl", "")
    page_name = post.get("pageName", "")
    username = post.get("username", "")
    
    # Step 1: Extract account name
    account = ""
    
    # Try facebookUrl
    if author_url and "facebook.com/" in author_url:
        account = author_url.split("facebook.com/")[1].rstrip("/")
        if "?" in account:
            account = account.split("?")[0]
        if "profile.php" in account:
            account = ""
    
    # Try pageName
    if not account and page_name:
        account = page_name
    
    # Try username
    if not account and username:
        account = username
    
    # Try to extract from original URL
    if not account:
        url_match = re.search(r'facebook\.com/([^/\?]+)', original_url)
        if url_match:
            candidate = url_match.group(1)
            if candidate not in ("reel", "videos", "photos", "posts", "watch", "groups", "story.php", "permalink.php"):
                account = candidate
    
    # Step 2: Extract post_id from various URL formats
    extracted_id = post_id
    
    if not extracted_id or extracted_id == "None":
        # /reel/ID
        m = re.search(r'/reel/(\d+)', original_url)
        if m:
            extracted_id = m.group(1)
        
        # /videos/ID
        if not extracted_id:
            m = re.search(r'/videos/(\d+)', original_url)
            if m:
                extracted_id = m.group(1)
        
        # /photos/ID
        if not extracted_id:
            m = re.search(r'/photos/(\d+)', original_url)
            if m:
                extracted_id = m.group(1)
        
        # /posts/ID
        if not extracted_id:
            m = re.search(r'/posts/([^/?]+)', original_url)
            if m:
                extracted_id = m.group(1)
        
        # bare /ID
        if not extracted_id:
            m = re.search(r'facebook\.com/(\d+)(?:/|$)', original_url)
            if m:
                extracted_id = m.group(1)
    
    # Step 3: Build normalized URL
    if extracted_id and account:
        return f"https://www.facebook.com/{account}/posts/{extracted_id}", account, extracted_id
    elif extracted_id:
        return f"https://www.facebook.com/{extracted_id}", account, extracted_id
    else:
        return original_url, account, extracted_id


def format_posts(raw_posts):
    """Format Apify results into our standard format."""
    formatted = []
    for post in raw_posts:
        normalized_url, account, post_id = normalize_url(post)
        
        formatted.append({
            "post_id": post_id,
            "post_url_normalized": normalized_url,
            "post_url_original": post.get("url", post.get("link", "")),
            "account": account,
            "author": post.get("pageName", post.get("author", "")),
            "post_text": post.get("text", post.get("message", ""))[:500],
            "likes": post.get("likes", post.get("reactionCount", 0)),
            "comments_count": post.get("comments", post.get("commentsCount", 0)),
            "shares": post.get("shares", post.get("sharesCount", 0)),
            "timestamp": post.get("timestamp", post.get("time", "")),
            "play_count": post.get("playCount", post.get("videoPlayCount", 0)),
            "is_video": post.get("isVideo", False),
            "comments": [],
            "replies": [],
            "total_comments_scraped": 0,
            "total_replies_scraped": 0,
        })
    return formatted


# ─── PHASE 2: NATIVE COMMENT SCRAPING ──────────────────────────────────────────

def load_cookies(driver, cookie_file):
    """Load Netscape format cookies into the browser."""
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


def get_browser(proxy_port=10000):
    """Create a Selenium browser with proxy + anti-detection."""
    from seleniumwire import webdriver
    from selenium.webdriver.chrome.service import Service

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


def scrape_post_comments(driver, post, max_comments=100):
    """Scrape comments from a single Facebook post."""
    from selenium.webdriver.common.by import By

    url = post["post_url_normalized"]
    print(f"\n{'─'*60}")
    print(f"  Post: {url[:70]}")
    print(f"  Account: {post['account']} | Likes: {post['likes']} | Comments: {post['comments_count']}")
    print(f"{'─'*60}")

    # Load post
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
                print(f"  ✅ Clicked '{elem.text}'")
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

    for scroll in range(1, 31):
        # Find comment elements — try multiple selectors
        comment_elements = []
        
        # Selector 1: aria-label
        try:
            comment_elements = driver.find_elements(By.CSS_SELECTOR, "div[aria-label='Komentar' i], div[aria-label='Comment' i]")
        except:
            pass
        
        # Selector 2: role=article with UFI2Comment
        if not comment_elements:
            try:
                comment_elements = driver.find_elements(By.CSS_SELECTOR, "div[data-testid='UFI2Comment/root']")
            except:
                pass
        
        # Selector 3: role=article (broader)
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
                        
                        # Extract commenter name (first line)
                        commenter = lines[0].strip() if lines else "Unknown"
                        
                        # Extract comment text (skip first line)
                        comment_text = '\n'.join(lines[1:]) if len(lines) > 1 else text
                        
                        # Remove "Like" / "Suka" / "Reply" / "Balas" from text
                        comment_text = re.sub(r'\b(suka|like|reply|balas|lihat balasan|view more replies| View more)\b.*$', '', comment_text, flags=re.IGNORECASE).strip()
                        
                        # Extract likes
                        likes = 0
                        likes_match = re.search(r'(\d+)\s*(suka|like)', text.lower())
                        if likes_match:
                            likes = int(likes_match.group(1))
                        
                        # Extract timestamp
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

        # Click reply buttons to expand replies
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

        # Check for no change
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
    parser = argparse.ArgumentParser(description="Facebook Apify + Native Comment Scraper")
    parser.add_argument("--keyword", required=True, help="Search keyword")
    parser.add_argument("--max-posts", type=int, default=10, help="Max posts to find")
    parser.add_argument("--max-comments", type=int, default=100, help="Max comments per post")
    parser.add_argument("--output", default="output.json", help="Output JSON file")
    args = parser.parse_args()

    print("=" * 60)
    print("  FACEBOOK APIFY + NATIVE SCRAPER")
    print("  Apify search → Native comment scraping with cookies")
    print("=" * 60)
    print(f"  Keyword: {args.keyword}")
    print(f"  Max posts: {args.max_posts}")
    print(f"  Max comments: {args.max_comments}")
    print(f"  Output: {args.output}")

    # ── PHASE 1: APIFY SEARCH ──────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  PHASE 1: APIFY SEARCH")
    print("═" * 60)

    raw_posts = apify_search(args.keyword, args.max_posts)
    if not raw_posts:
        print("❌ No posts found. Apify might be down.")
        sys.exit(1)

    posts = format_posts(raw_posts)

    print(f"\n  Posts found:")
    for i, post in enumerate(posts):
        print(f"    [{i+1}] {post['post_id']} | {post['author']} | {post['likes']} likes | {post['comments_count']} comments")
        print(f"        URL: {post['post_url_normalized'][:70]}")

    # ── PHASE 2: NATIVE COMMENT SCRAPING ────────────────────────────────────
    print("\n" + "═" * 60)
    print("  PHASE 2: NATIVE COMMENT SCRAPING (cookies + DataImpulse proxy)")
    print("═" * 60)

    proxy_port = random.choice(PROXY_PORTS)
    print(f"  Proxy: {_proxy_host}:{proxy_port}")
    print(f"  Cookies: {COOKIE_FILE}")

    driver = get_browser(proxy_port)

    # Load Facebook + cookies
    print("\n  Loading Facebook + cookies...")
    driver.get("https://www.facebook.com")
    time.sleep(5)
    load_cookies(driver, COOKIE_FILE)

    # Verify login
    browser_cookies = driver.get_cookies()
    has_cuser = any(c.get("name") == "c_user" for c in browser_cookies)
    if has_cuser:
        print("  ✅ Logged in via cookies!")
    else:
        print("  ⚠️ Cookies might be invalid — continuing anyway")

    # Scrape each post
    for i, post in enumerate(posts):
        print(f"\n  Post {i+1}/{len(posts)}")
        
        # Rotate proxy every 3 posts
        if i > 0 and i % 3 == 0:
            proxy_port = random.choice(PROXY_PORTS)
            print(f"  🔄 Rotating proxy: {_proxy_host}:{proxy_port}")
            # Note: proxy rotation requires browser restart in seleniumwire
            # For now, keep same proxy
        
        try:
            scrape_post_comments(driver, post, args.max_comments)
        except Exception as e:
            print(f"  ❌ Error: {e}")
            post["comments"] = []
            post["replies"] = []

        time.sleep(random.uniform(3, 6))

    driver.quit()

    # ── SAVE ───────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  SAVING RESULTS")
    print("═" * 60)

    total_comments = sum(p["total_comments_scraped"] for p in posts)
    total_replies = sum(p["total_replies_scraped"] for p in posts)

    output = {
        "metadata": {
            "keyword": args.keyword,
            "total_posts": len(posts),
            "total_comments": total_comments,
            "total_replies": total_replies,
            "total_data_points": total_comments + total_replies,
            "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
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
    print("\nDone!")


if __name__ == "__main__":
    main()
