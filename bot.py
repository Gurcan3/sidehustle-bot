import os
import time
import logging
import threading
import requests
from datetime import datetime
import pytz

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
CLAUDE_API_KEY  = os.environ["CLAUDE_API_KEY"]
ALLOWED_USER_ID = int(os.environ["ALLOWED_USER_ID"])
TR_TZ = pytz.timezone("Europe/Istanbul")
TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# ── Fiyat çekme — Finans API ──────────────────────────────────────────────────
def get_price_collecting():
    """BigPara API üzerinden fiyat çeker."""
    prices = {}
    try:
        # Döviz kurları — TCMB
        r = requests.get(
            "https://finans.truncgil.com/v4/today.json",
            headers=HEADERS, timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            prices["usd"] = float(str(data.get("USD", {}).get("Satış", "0")).replace(",", "."))
            prices["eur"] = float(str(data.get("EUR", {}).get("Satış", "0")).replace(",", "."))
            prices["gbp"] = float(str(data.get("GBP", {}).get("Satış", "0")).replace(",", "."))
            prices["altin"] = float(str(data.get("gram-altin", {}).get("Satış", "0")).replace(",", "."))
    except Exception as e:
        logger.error(f"Döviz/altın hatası: {e}")

    # Hisse fiyatları — Yahoo Finance basit sorgu
    hisseler = {"KCHOL.IS": "kchol", "FROTO.IS": "froto", "GARAN.IS": "garan"}
    for sembol, key in hisseler.items():
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sembol}?interval=1d&range=5d"
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                data = r.json()
                closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
                closes = [c for c in closes if c is not None]
                if closes:
                    prices[key] = round(closes[-1], 2)
        except Exception as e:
            logger.error(f"{sembol} hatası: {e}")

    # BIST100
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/XU100.IS?interval=1d&range=5d"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            data = r.json()
            closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            closes = [c for c in closes if c is not None]
            if closes:
                prices["bist"] = round(closes[-1], 0)
    except Exception as e:
        logger.error(f"BIST hatası: {e}")

    return prices

def fmt(v, decimals=2):
    if not v:
        return "Veri yok"
    return f"{v:,.{decimals}f}"

def price_table(prices):
    return (
        f"📊 *Güncel Fiyatlar*\n"
        f"🥇 Altın: `{fmt(prices.get('altin'))} TL/gram`\n"
        f"💵 Dolar: `{fmt(prices.get('usd'))} TL`\n"
        f"💶 Euro: `{fmt(prices.get('eur'))} TL`\n"
        f"💷 Sterlin: `{fmt(prices.get('gbp'))} TL`\n"
        f"━━━━━━━━━━━━━━\n"
        f"🏭 KCHOL: `{fmt(prices.get('kchol'))} TL`\n"
        f"🚗 FROTO: `{fmt(prices.get('froto'))} TL`\n"
        f"🏦 GARAN: `{fmt(prices.get('garan'))} TL`\n"
        f"📈 BIST 100: `{fmt(prices.get('bist'), 0)}`"
    )

# ── Claude API ────────────────────────────────────────────────────────────────
def ask_claude(prompt):
    headers = {
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1024,
        "system": (
            "Sen 'Side Hustle' adlı Türkçe konuşan yatırım asistanısın. "
            "Türkiye piyasası, BIST, altın ve döviz uzmansın. "
            "Kısa, net, aksiyon odaklı yanıt ver. "
            "Takip edilen hisseler: KCHOL (Koç Holding), FROTO (Ford Otosan), GARAN (Garanti BBVA). "
            "Portföy: %30 altın, %15 döviz, %25 KCHOL+SAHOL, %15 FROTO/TUPRS, %15 nakit. "
            "Drawdown limiti %10. Kesin getiri garantisi verme."
        ),
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers, json=body, timeout=60
        )
        r.raise_for_status()
        data = r.json()
        text = " ".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
        return text.strip() or "Yanıt alınamadı."
    except Exception as e:
        logger.error(f"Claude hatası: {e}")
        return f"Analiz alınamadı: {e}"

# ── Telegram ──────────────────────────────────────────────────────────────────
def tg_send(chat_id, text):
    try:
        requests.post(f"{TG_API}/sendMessage", json={
            "chat_id": chat_id, "text": text, "parse_mode": "Markdown"
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

# ── Komutlar ──────────────────────────────────────────────────────────────────
def handle_start(chat_id):
    tg_send(chat_id,
        "👋 *Side Hustle Bot aktif!*\n\n"
        "/rapor — Anlık fiyatlar\n"
        "/analiz — Piyasa analizi\n"
        "/haber — Güncel haberler\n"
        "/portfoy — Portföy durumu\n"
        "/kchol — KCHOL detay analiz\n"
        "/yardim — Tüm komutlar\n\n"
        "Ya da direkt yaz: _'Bugün KCHOL almalı mıyım?'_"
    )

def handle_rapor(chat_id):
    tg_send(chat_id, "📡 Fiyatlar çekiliyor...")
    prices = get_price_collecting()
    tarih = datetime.now(TR_TZ).strftime("%d/%m/%Y %H:%M")
    tg_send(chat_id, f"{price_table(prices)}\n\n_Güncelleme: {tarih}_")

def handle_analiz(chat_id):
    tg_send(chat_id, "🧠 Analiz hazırlanıyor...")
    prices = get_price_collecting()
    prompt = (
        f"Güncel fiyatlar: Altın {prices.get('altin')} TL/gram, "
        f"Dolar {prices.get('usd')} TL, Euro {prices.get('eur')} TL, "
        f"KCHOL {prices.get('kchol')} TL, FROTO {prices.get('froto')} TL, "
        f"GARAN {prices.get('garan')} TL, BIST100 {prices.get('bist')}. "
        f"Bugün {datetime.now(TR_TZ).strftime('%d %B %Y')}. "
        "Türkiye piyasası için bugünkü durumu değerlendir. "
        "1) Genel trend 2) KCHOL için bu hafta görüş 3) Risk var mı? "
        "Net ve kısa yaz, maksimum 150 kelime."
    )
    yanit = ask_claude(prompt)
    tg_send(chat_id, f"📊 *Piyasa Analizi*\n\n{yanit}")

def handle_haber(chat_id):
    tg_send(chat_id, "📰 Haberler hazırlanıyor...")
    prompt = (
        f"Bugün {datetime.now(TR_TZ).strftime('%d %B %Y')}. "
        "Türkiye finans piyasaları için bugünün önemli gelişmelerini özetle. "
        "BIST, TL kuru, altın, TCMB, Koç Holding, Ford Otosan, Garanti BBVA odaklı. "
        "3-5 madde, her biri 1-2 cümle. Portföye etkisini belirt."
    )
    yanit = ask_claude(prompt)
    tg_send(chat_id, f"📰 *Piyasa Haberleri*\n\n{yanit}")

def handle_kchol(chat_id):
    tg_send(chat_id, "🔍 KCHOL analiz ediliyor...")
    prices = get_price_collecting()
    kchol = prices.get('kchol', 'bilinmiyor')
    prompt = (
        f"KCHOL şu an {kchol} TL. "
        "Koç Holding hissesi için detaylı analiz yap: "
        "1) Şu anki fiyat makul mu? "
        "2) Kısa vadeli (1-2 hafta) görünüm nasıl? "
        "3) Giriş için beklemeli mi, şimdi mi alınmalı? "
        "4) Stop-loss seviyesi nerede olmalı? "
        "Net ve kısa yaz."
    )
    yanit = ask_claude(prompt)
    tg_send(chat_id, f"🏭 *KCHOL Detay Analiz*\n\n{yanit}")

def handle_portfoy(chat_id):
    prices = get_price_collecting()
    tarih = datetime.now(TR_TZ).strftime("%d/%m/%Y %H:%M")
    tg_send(chat_id,
        f"💼 *Portföy Yapısı* — _{tarih}_\n\n"
        f"*Katman A — Savunma (%45)*\n"
        f"🥇 Altın %30 · 💵 Döviz %15\n\n"
        f"*Katman B — Taarruz (%40)*\n"
        f"🏭 KCHOL · 🚗 FROTO · 🏦 GARAN\n\n"
        f"*Nakit Tamponu (%15)*\n"
        f"Fırsat bekler\n\n"
        f"{price_table(prices)}\n\n"
        f"_Drawdown limiti: %10 | Stop: KCHOL 179 TL altı_"
    )

def handle_yardim(chat_id):
    tg_send(chat_id,
        "🤖 *Side Hustle Bot — Komutlar*\n\n"
        "/rapor — Anlık fiyat tablosu\n"
        "/analiz — Piyasa analizi\n"
        "/haber — Haber özeti\n"
        "/portfoy — Portföy yapısı\n"
        "/kchol — KCHOL detay analiz\n"
        "/yardim — Bu menü\n\n"
        "☀️ Sabah raporu her gün *08:00*'de otomatik gelir\n"
        "💬 Serbest soru da sorabilirsin"
    )

def handle_serbest(chat_id, text):
    tg_send(chat_id, "💭 Düşünüyorum...")
    prices = get_price_collecting()
    prompt = (
        f"Kullanıcı sorusu: '{text}'. "
        f"Güncel: Altın {prices.get('altin')} TL, "
        f"Dolar {prices.get('usd')} TL, "
        f"KCHOL {prices.get('kchol')} TL, "
        f"FROTO {prices.get('froto')} TL, "
        f"GARAN {prices.get('garan')} TL. "
        "Soruyu yanıtla. Kısa Türkçe yanıt, maksimum 100 kelime."
    )
    yanit = ask_claude(prompt)
    tg_send(chat_id, yanit)

def sabah_raporu():
    prices = get_price_collecting()
    tarih = datetime.now(TR_TZ).strftime("%d %B %Y")
    prompt = (
        f"Bugün {tarih}. Sabah yatırım raporu hazırla. "
        f"Fiyatlar: Altın {prices.get('altin')} TL, Dolar {prices.get('usd')} TL, "
        f"KCHOL {prices.get('kchol')} TL, FROTO {prices.get('froto')} TL, "
        f"GARAN {prices.get('garan')} TL, BIST100 {prices.get('bist')}. "
        "1) Bugün için önemli 2-3 gelişme "
        "2) KCHOL için günlük görüş "
        "3) Portföy için aksiyon var mı? "
        "Maksimum 150 kelime, net yaz."
    )
    analiz = ask_claude(prompt)
    tg_send(ALLOWED_USER_ID,
        f"☀️ *Günaydın! Side Hustle Sabah Raporu*\n_{tarih}_\n\n"
        f"{price_table(prices)}\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🧠 *Analiz*\n\n{analiz}\n\n"
        f"_/kchol /analiz /haber komutlarını kullanabilirsin_"
    )

# ── Zamanlayıcı ───────────────────────────────────────────────────────────────
def scheduler_thread():
    last_day = None
    while True:
        now = datetime.now(TR_TZ)
        if now.hour == 8 and now.minute == 0 and now.day != last_day:
            last_day = now.day
            try:
                sabah_raporu()
            except Exception as e:
                logger.error(f"Sabah raporu hatası: {e}")
        time.sleep(30)

# ── Ana döngü ─────────────────────────────────────────────────────────────────
def main():
    logger.info("Side Hustle Bot başladı ✅")
    tg_send(ALLOWED_USER_ID, "🚀 *Side Hustle Bot güncellendi!* /start yaz.")

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
                if text == "/start":       handle_start(chat_id)
                elif text == "/rapor":     handle_rapor(chat_id)
                elif text == "/analiz":    handle_analiz(chat_id)
                elif text == "/haber":     handle_haber(chat_id)
                elif text == "/portfoy":   handle_portfoy(chat_id)
                elif text == "/kchol":     handle_kchol(chat_id)
                elif text == "/yardim":    handle_yardim(chat_id)
                elif text.startswith("/"): tg_send(chat_id, "Bilinmeyen komut. /yardim yaz.")
                else:                      handle_serbest(chat_id, text)
        except Exception as e:
            logger.error(f"Ana döngü hatası: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
