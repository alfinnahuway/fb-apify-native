# Facebook Apify + Native Comment Scraper

Hybrid scraper: Apify search by keyword + Native Selenium comment scraping with Facebook cookies.

## Architecture

```
Phase 1: Apify search → find posts by keyword (URL + metadata)
    ↓
Phase 2: Normalize URLs → /account/posts/ID format
    ↓
Phase 3: Native Selenium + cookies + DataImpulse proxy → scrape 100 comments + replies per post
    ↓
JSON output (post metadata + comments + replies)
```

## Cost

- Apify search: $0.006/post
- Comment scraping: $0 (native Selenium + proxy)
- Total for 10 posts × 100 comments: ~$0.06

## Setup

### 1. Environment Variables

```bash
export APIFY_API_KEYS="apify_api_xxx"
export DATAIMPULSE_USER="your_proxy_user"
export DATAIMPULSE_PASS="your_proxy_pass"
```

### 2. Cookies

Place Facebook cookies in `fb_cookies.txt` (Netscape format).

To get cookies:
1. Login Facebook from your laptop browser
2. Install "Get cookies.txt LOCALLY" Chrome extension
3. Export → cookies.txt
4. Save as `fb_cookies.txt` in this folder

### 3. Chrome for Testing

```bash
# Binary: /tmp/cft/chrome-linux64/chrome
# Driver: /tmp/cft/chromedriver-linux64/chromedriver
```

## Usage

```bash
python3 fb_apify_native.py \
  --keyword "indonesia" \
  --max-posts 10 \
  --max-comments 100 \
  --output output.json
```

## Output Format

```json
{
  "metadata": {
    "keyword": "indonesia",
    "total_posts": 10,
    "total_comments": 500,
    "total_replies": 300,
    "total_data_points": 800
  },
  "posts": [
    {
      "post_id": "123456",
      "post_url_normalized": "https://www.facebook.com/account/posts/123456",
      "account": "account_name",
      "author": "Page Name",
      "post_text": "...",
      "likes": 1000,
      "comments_count": 500,
      "shares": 100,
      "timestamp": "2026-08-27",
      "play_count": 0,
      "comments": [
        {
          "commenter": "John Doe",
          "text": "Comment text here",
          "likes": 10,
          "timestamp": "3d"
        }
      ],
      "replies": [
        {
          "replier": "Jane Doe",
          "text": "Reply text here"
        }
      ]
    }
  ]
}
```

## URL Normalization

Apify returns URLs in various formats. All are normalized to `/account/posts/ID`:

| Input URL | Normalized |
|:----------|:-----------|
| `/reel/ID` | `/account/posts/ID` |
| `/videos/ID` | `/account/posts/ID` |
| `/photos/ID` | `/account/posts/ID` |
| bare `/ID` | `/account/posts/ID` |
| `/account/posts/ID` | unchanged |

This is required because Facebook only renders comment sections for `/account/posts/ID` URLs.
