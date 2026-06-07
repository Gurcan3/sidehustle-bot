import os
import time
import logging
import threading
import requests
import yfinance as yf
from datetime import datetime
import pytz

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
CLAUDE_API_KEY  = os.environ["CLAUDE_API_KEY"]
ALLOWED_USER_ID = int(os.environ["ALLOWED_USER_ID"])
TR_TZ = pytz.timezone("Europe/Istanbul")
TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ── Telegram helpers ──────────────────────────────────────────────────────────
def tg_send(chat_id: int, text: str):
    try:
        requests.post(f"{TG_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }, timeout=15)
    except Exception as e:
        logger.error(f"Telegram send error: {e}")

def tg_get_updates(offset=None):
    try:
        params = {"timeout": 30, "allowed_updates": ["message"]}
        if offset:
            params["offset"] = offset
        r = requests.get(f"{TG_API}/getUpdates", params=params, timeout=35)
        return r.json().get("result", [])
    except Exception as e:
        logger.error(f"getUpdates error: {e}")
        return []

# ── Fiyat çekme ───────────────────────────────────────────────────────────────
def get_prices():
    prices = {}
    try:
        semboller = ["GC=F", "USDTRY=X", "EURTRY=X", "GBPTRY=X", "KCHOL.IS", "XU100.IS"]
        data = yf.download(semboller, period="2d", interval="1d", progress=False, auto_adjust=True)
        close = data["Close"]
        usd_try = float(close["USDTRY=X"].dropna().iloc[-1])
        gold_oz  = float(close["GC=F"].dropna().iloc[-1])
        prices["altin"] = round((gold_oz / 31.1035) * usd_try, 2)
        for s in ["USDTRY=X", "EURTRY=X", "GBPTRY=X", "KCHOL.IS", "XU100.IS"]:
            try:
                prices[s] = round(float(close[s].dropna().iloc[-1]), 2)
            except:
                prices[s] = None
    except Exception as e:
        logger.error(f"Fiyat hatası: {e}")
    return prices

def price_table(prices):
    def fmt(v):
        return f"{v:,.2f}" if v else "—"
    return (
        f"📊 *Güncel Fiyatlar*\n"
        f"🥇 Altın: `{fmt(prices.get('altin'))} TL/gram`\n"
        f"💵 Dolar: `{fmt(prices.get('USDTRY=X'))} TL`\n"
        f"💶 Euro: `{fmt(prices.get('EURTRY=X'))} TL`\n"
        f"💷 Sterlin: `{fmt(prices.get('GBPTRY=X'))} TL`\n"
        f"🏭 Koç Holding: `{fmt(prices.get('KCHOL.IS'))} TL`\n"
        f"📈 BIST 100: `{fmt(prices.get('XU100.IS'))}`"
    )

# ── Claude API ────────────────────────────────────────────────────────────────
def ask_claude(prompt: str) -> str:
    headers = {
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "system": (
            "Sen 'Side Hustle' adlı Türkçe konuşan yatırım asistanısın. "
            "Türkiye piyasası, BIST, altın ve döviz uzmansın. "
            "Kısa, net, aksiyon odaklı yanıt ver. "
            "Portföy: %60 savunma (altın/döviz), %40 taarruz (BIST). "
            "Kesin getiri garantisi verme."
        ),
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
    }
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
                          headers=headers, json=body, timeout=60)
        r.raise_for_status()
        data = r.json()
        text = " ".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
        return text.strip() or "Yanıt alınamadı."
    except Exception as e:
        logger.error(f"Claude hatası: {e}")
        return f"Analiz alınamadı: {e}"

# ── Komut işleyiciler ─────────────────────────────────────────────────────────
def handle_start(chat_id):
    tg_send(chat_id,
        "👋 *Side Hustle Bot aktif!*\n\n"
        "/rapor — Anlık fiyatlar\n"
        "/analiz — Piyasa analizi\n"
        "/haber — Güncel haberler\n"
        "/portfoy — Portföy durumu\n"
        "/yardim — Tüm komutlar\n\n"
        "Ya da direkt yaz: _'Bugün KCHOL almalı mıyım?'_"
    )

def handle_rapor(chat_id):
    tg_send(chat_id, "📡 Fiyatlar çekiliyor...")
    prices = get_prices()
    tarih = datetime.now(TR_TZ).strftime("%d/%m/%Y %H:%M")
    tg_send(chat_id, f"{price_table(prices)}\n\n_Güncelleme: {tarih}_")

def handle_analiz(chat_id):
    tg_send(chat_id, "🧠 Analiz hazırlanıyor (15-20 sn)...")
    prices = get_prices()
    prompt = (
        f"Güncel fiyatlar: Altın {prices.get('altin')} TL/gram, "
        f"Dolar {prices.get('USDTRY=X')} TL, Euro {prices.get('EURTRY=X')} TL, "
        f"KCHOL {prices.get('KCHOL.IS')} TL, BIST100 {prices.get('XU100.IS')}. "
        "Güncel Türkiye piyasa haberlerini tara. "
        "1) Genel trend 2) Bu hafta öneri 3) Risk var mı? Kısa net yaz."
    )
    yanit = ask_claude(prompt)
    tg_send(chat_id, f"📊 *Piyasa Analizi*\n\n{yanit}")

def handle_haber(chat_id):
    tg_send(chat_id, "📰 Haberler taranıyor...")
    prompt = (
        "Türkiye finans piyasaları için bugünün en önemli 5 haberini tara. "
        "BIST, TL kuru, altın, TCMB, Koç Holding odaklı. "
        "Her haberi 1-2 cümle özetle, portföye etkisini belirt."
    )
    yanit = ask_claude(prompt)
    tg_send(chat_id, f"📰 *Güncel Haberler*\n\n{yanit}")

def handle_portfoy(chat_id):
    prices = get_prices()
    tarih = datetime.now(TR_TZ).strftime("%d/%m/%Y %H:%M")
    tg_send(chat_id,
        f"💼 *Portföy Yapısı* — _{tarih}_\n\n"
        f"*Katman A — Savunma (%60)*\n"
        f"🥇 Altın · 💵 Dolar · 💶 Euro · 💷 Sterlin\n\n"
        f"*Katman B — Taarruz (%40)*\n"
        f"🏭 Koç Holding · 📈 BIST 100\n\n"
        f"{price_table(prices)}\n\n"
        f"_Drawdown limiti: %10_"
    )

def handle_yardim(chat_id):
    tg_send(chat_id,
        "🤖 *Side Hustle Bot — Komutlar*\n\n"
        "/rapor — Anlık fiyat tablosu\n"
        "/analiz — Detaylı piyasa analizi\n"
        "/haber — Haber özeti\n"
        "/portfoy — Portföy yapısı\n"
        "/yardim — Bu menü\n\n"
        "☀️ Sabah raporu her gün *08:00*'de otomatik gelir"
    )

def handle_serbest(chat_id, text):
    tg_send(chat_id, "💭 Düşünüyorum...")
    prices = get_prices()
    prompt = (
        f"Kullanıcı sorusu: '{text}'. "
        f"Güncel: Altın {prices.get('altin')} TL, "
        f"Dolar {prices.get('USDTRY=X')} TL, KCHOL {prices.get('KCHOL.IS')} TL. "
        "Soruyu yanıtla. Gerekirse web'de ara. Kısa Türkçe."
    )
    yanit = ask_claude(prompt)
    tg_send(chat_id, yanit)

def sabah_raporu():
    prices = get_prices()
    tarih = datetime.now(TR_TZ).strftime("%d %B %Y")
    prompt = (
        f"Bugün {tarih}. Sabah piyasa raporu. "
        f"Fiyatlar: Altın {prices.get('altin')} TL, Dolar {prices.get('USDTRY=X')} TL, "
        f"KCHOL {prices.get('KCHOL.IS')} TL, BIST100 {prices.get('XU100.IS')}. "
        "1) 2-3 önemli haber 2) Portföy için dikkat noktası. Max 200 kelime."
    )
    analiz = ask_claude(prompt)
    mesaj = (
        f"☀️ *Günaydın! Side Hustle Sabah Raporu*\n_{tarih}_\n\n"
        f"{price_table(prices)}\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🧠 *Analiz*\n\n{analiz}"
    )
    tg_send(ALLOWED_USER_ID, mesaj)

# ── Zamanlayıcı thread ────────────────────────────────────────────────────────
def scheduler_thread():
    last_report_day = None
    while True:
        now = datetime.now(TR_TZ)
        if now.hour == 8 and now.minute == 0 and now.day != last_report_day:
            last_report_day = now.day
            try:
                sabah_raporu()
            except Exception as e:
                logger.error(f"Sabah raporu hatası: {e}")
        time.sleep(30)

# ── Ana döngü ─────────────────────────────────────────────────────────────────
def main():
    logger.info("Side Hustle Bot başladı ✅")
    tg_send(ALLOWED_USER_ID, "🚀 *Side Hustle Bot online!* /start ile başla.")

    # Zamanlayıcıyı ayrı thread'de başlat
    t = threading.Thread(target=scheduler_thread, daemon=True)
    t.start()

    offset = None
    while True:
        try:
            updates = tg_get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "")

                if not chat_id or chat_id != ALLOWED_USER_ID:
                    continue

                if text == "/start":        handle_start(chat_id)
                elif text == "/rapor":      handle_rapor(chat_id)
                elif text == "/analiz":     handle_analiz(chat_id)
                elif text == "/haber":      handle_haber(chat_id)
                elif text == "/portfoy":    handle_portfoy(chat_id)
                elif text == "/yardim":     handle_yardim(chat_id)
                elif text.startswith("/"):  tg_send(chat_id, "Bilinmeyen komut. /yardim yaz.")
                else:                       handle_serbest(chat_id, text)

        except Exception as e:
            logger.error(f"Ana döngü hatası: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
