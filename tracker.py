import os
import json
import csv
import random
import time
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from pathlib import Path

# ─── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")
SCRAPER_API_KEY    = os.environ.get("SCRAPER_API_KEY")
PRODUCTS_FILE      = "products.json"
HISTORY_FILE       = "data/price_history.csv"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

# ─── Scraper ────────────────────────────────────────────────────────────────────
def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Connection": "keep-alive",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
    }

def scrape_price(url: str) -> tuple[str | None, str | None]:
    """Returns (price_float, product_title) or (None, None) on failure."""
    try:
        time.sleep(random.uniform(1, 3))

        if SCRAPER_API_KEY:
            scraper_url = (
                f"http://api.scraperapi.com"
                f"?api_key={SCRAPER_API_KEY}"
                f"&url={url}"
                f"&country_code=in"
            )
            print(f"  🌐  Using ScraperAPI...")
            response = requests.get(scraper_url, timeout=60)
        else:
            print(f"  ⚠️  No SCRAPER_API_KEY — trying direct request (may get blocked)")
            response = requests.get(url, headers=get_headers(), timeout=15)

        print(f"  📡  Status code: {response.status_code}")

        if response.status_code != 200:
            print(f"  ❌  Bad response: {response.status_code}")
            return None, None

        if "captcha" in response.text.lower() or "robot" in response.text.lower():
            print(f"  ❌  Amazon returned a CAPTCHA — blocked!")
            return None, None

        soup = BeautifulSoup(response.content, "html.parser")

        title_tag = soup.find(id="productTitle")
        title = title_tag.get_text(strip=True) if title_tag else "Unknown Product"
        print(f"  📦  Title: {title[:60]}")

        price = None
        price_selectors = [
            {"id": "priceblock_ourprice"},
            {"id": "priceblock_dealprice"},
            {"id": "price_inside_buybox"},
            {"class": "a-price-whole"},
            {"class": "priceToPay"},
            {"id": "corePrice_feature_div"},
        ]

        for selector in price_selectors:
            tag = soup.find(attrs=selector)
            if tag:
                raw = tag.get_text(strip=True)
                cleaned = raw.replace(",", "").replace("₹", "").replace("$", "").replace("£", "").replace("€", "")
                match = re.search(r"\d+\.?\d*", cleaned)
                if match:
                    price = float(match.group())
                    print(f"  💰  Price: {price}")
                    break

        if price is None:
            print(f"  ❌  Could not find price — Amazon may have changed their HTML")

        return price, title

    except Exception as e:
        print(f"  ❌  Scrape error: {e}")
        return None, None

# ─── History ────────────────────────────────────────────────────────────────────
def load_history() -> dict:
    history = {}
    path = Path(HISTORY_FILE)
    if not path.exists():
        return history
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            asin = row["asin"]
            history.setdefault(asin, []).append({
                "date": row["date"],
                "price": float(row["price"]),
            })
    return history

def save_price(asin: str, title: str, price: float):
    path = Path(HISTORY_FILE)
    path.parent.mkdir(exist_ok=True)
    write_header = not path.exists()
    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["asin", "title", "date", "price"])
        writer.writerow([asin, title, datetime.utcnow().strftime("%Y-%m-%d %H:%M"), price])

# ─── Telegram ───────────────────────────────────────────────────────────────────
def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  ⚠️  Telegram credentials not set — skipping notification.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        print("  ✅  Telegram notification sent.")
    except Exception as e:
        print(f"  ⚠️  Telegram error: {e}")

def format_alert(title, url, old_price, new_price, target):
    drop_pct = round((old_price - new_price) / old_price * 100, 1)
    return (
        f"🚨 <b>Price Drop Alert!</b>\n\n"
        f"📦 <b>{title[:80]}</b>\n\n"
        f"💰 <b>New Price:</b> {new_price}\n"
        f"📉 <b>Was:</b> {old_price}  (↓ {drop_pct}% drop)\n"
        f"🎯 <b>Your Target:</b> {target}\n\n"
        f"🔗 <a href='{url}'>View on Amazon</a>"
    )

def format_target_hit(title, url, price, target):
    return (
        f"🎯 <b>Target Price Reached!</b>\n\n"
        f"📦 <b>{title[:80]}</b>\n\n"
        f"💰 <b>Current Price:</b> {price}\n"
        f"🎯 <b>Your Target:</b> {target}\n\n"
        f"🔗 <a href='{url}'>Buy Now on Amazon</a>"
    )

def send_startup_message():
    msg = (
        "✅ <b>Amazon Price Tracker is running!</b>\n\n"
        "Your tracker is set up correctly and will check prices every 6 hours.\n"
        "You'll get alerts here when prices drop or hit your target. 🎉"
    )
    send_telegram(msg)

# ─── Main ───────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*55}")
    print(f"  Amazon Price Tracker  —  {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"{'='*55}\n")

    print(f"🔑  ScraperAPI key: {'Yes ✅' if SCRAPER_API_KEY else 'No ❌'}")
    print(f"🔑  Telegram token: {'Yes ✅' if TELEGRAM_BOT_TOKEN else 'No ❌'}")
    print(f"🔑  Telegram chat ID: {'Yes ✅' if TELEGRAM_CHAT_ID else 'No ❌'}\n")

    if not Path(PRODUCTS_FILE).exists():
        print(f"❌  {PRODUCTS_FILE} not found.")
        return

    with open(PRODUCTS_FILE) as f:
        products = json.load(f)

    if not products:
        print("❌  No products in products.json")
        return

    history = load_history()
    first_run = not Path(HISTORY_FILE).exists()

    for product in products:
        asin       = product["asin"]
        url        = product["url"]
        target     = float(product.get("target_price", 0))
        alert_drop = float(product.get("alert_on_drop_percent", 5))

        print(f"🔍  Checking ASIN: {asin}")
        price, title = scrape_price(url)

        if price is None:
            print(f"  ❌  Could not fetch price for {asin}\n")
            continue

        past = history.get(asin, [])
        last_price = past[-1]["price"] if past else None

        save_price(asin, title, price)

        if target > 0 and price <= target:
            print(f"  🎯  TARGET REACHED! {price} <= {target}")
            send_telegram(format_target_hit(title, url, price, target))
        elif last_price and price < last_price:
            drop_pct = (last_price - price) / last_price * 100
            print(f"  📉  Price dropped {drop_pct:.1f}% ({last_price} -> {price})")
            if drop_pct >= alert_drop:
                send_telegram(format_alert(title, url, last_price, price, target))
        else:
            print(f"  ✅  No change (last: {last_price}, now: {price})")

        print()

    if first_run:
        send_startup_message()

    print("✅  All products checked.\n")

if __name__ == "__main__":
    main()
