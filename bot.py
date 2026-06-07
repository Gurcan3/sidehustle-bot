import os
import asyncio
import logging
from datetime import datetime
import pytz
import requests
import yfinance as yf
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
CLAUDE_API_KEY  = os.environ["CLAUDE_API_KEY"]
ALLOWED_USER_ID = int(os.environ["ALLOWED_USER_ID"])
TR_TZ = pytz.timezone("Europe/Istanbul")

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

def ask_claude(prompt: str) -> str:
    system = (
        "Sen 'Side Hustle' adlı Türkçe konuşan bir yatırım asistanısın. "
        "Türkiye piyasası, BIST, altın ve döviz konusunda uzmansın. "
        "Kısa, net, aksiyon odaklı yanıtlar ver. "
        "Portföy: %60 savunma (altın/döviz), %40 taarruz (BIST). "
        "Drawdown limiti %10. Kesin getiri garantisi verme."
    )
    headers = {
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
    }
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=body, timeout=60)
        r.raise_for_status()
        data = r.json()
        text = " ".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
        return text.strip() or "Yanıt alınamadı."
    except Exception as e:
        logger.error(f"Claude hatası: {e}")
        return f"Analiz alınamadı: {e}"

def price_table(prices):
    a = prices.get("altin", "—")
    u = prices.get("USDTRY=X", "—")
    e = prices.get("EURTRY=X", "—")
    g = prices.get("GBPTRY=X", "—")
    k = prices.get("KCHOL.IS", "—")
    b = prices.get("XU100.IS", "—")
    return (
        f"📊 *Güncel Fiyatlar*\n"
        f"🥇 Altın: `{a:,.2f} TL/gram`\n"
        f"💵 Dolar: `{u:,.2f} TL`\n"
        f"💶 Euro: `{e:,.2f} TL`\n"
        f"💷 Sterlin: `{g:,.2f} TL`\n"
        f"🏭 Koç Holding: `{k:,.2f} TL`\n"
        f"📈 BIST 100: `{b:,.2f}`"
    )

def auth(update: Update) -> bool:
    return update.effective_user.id == ALLOWED_USER_ID

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    await update.message.reply_text(
        "👋 *Side Hustle Bot aktif!*\n\n"
        "/rapor — Anlık fiyatlar\n"
        "/analiz — Piyasa analizi\n"
        "/haber — Güncel haberler\n"
        "/portfoy — Portföy durumu\n"
        "/yardim — Tüm komutlar\n\n"
        "Ya da direkt yaz: _'Bugün KCHOL almalı mıyım?'_",
        parse_mode="Markdown"
    )

async def cmd_rapor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    await update.message.reply_text("📡 Fiyatlar çekiliyor...")
    prices = get_prices()
    tarih = datetime.now(TR_TZ).strftime("%d/%m/%Y %H:%M")
    await update.message.reply_text(
        f"{price_table(prices)}\n\n_Güncelleme: {tarih}_",
        parse_mode="Markdown"
    )

async def cmd_analiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    await update.message.reply_text("🧠 Analiz hazırlanıyor (15-20 sn)...")
    prices = get_prices()
    prompt = (
        f"Güncel fiyatlar: Altın {prices.get('altin')} TL/gram, "
        f"Dolar {prices.get('USDTRY=X')} TL, Euro {prices.get('EURTRY=X')} TL, "
        f"Sterlin {prices.get('GBPTRY=X')} TL, KCHOL {prices.get('KCHOL.IS')} TL, "
        f"BIST100 {prices.get('XU100.IS')}. "
        "Güncel Türkiye piyasa haberlerini tara. "
        "1) Genel trend, 2) Bu hafta öneri, 3) Risk var mı? Kısa ve net yaz."
    )
    yanit = ask_claude(prompt)
    await update.message.reply_text(f"📊 *Piyasa Analizi*\n\n{yanit}", parse_mode="Markdown")

async def cmd_haber(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    await update.message.reply_text("📰 Haberler taranıyor...")
    prompt = (
        "Türkiye finans piyasaları için bugünün en önemli 5 haberini tara. "
        "BIST, TL kuru, altın, TCMB, Koç Holding odaklı. "
        "Her haberi 1-2 cümle özetle, portföye etkisini belirt."
    )
    yanit = ask_claude(prompt)
    await update.message.reply_text(f"📰 *Güncel Haberler*\n\n{yanit}", parse_mode="Markdown")

async def cmd_portfoy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    prices = get_prices()
    tarih = datetime.now(TR_TZ).strftime("%d/%m/%Y %H:%M")
    await update.message.reply_text(
        f"💼 *Portföy Yapısı* — _{tarih}_\n\n"
        f"*Katman A — Savunma (%60)*\n"
        f"🥇 Altın · 💵 Dolar · 💶 Euro · 💷 Sterlin\n\n"
        f"*Katman B — Taarruz (%40)*\n"
        f"🏭 Koç Holding · 📈 BIST 100\n\n"
        f"{price_table(prices)}\n\n"
        f"_Drawdown limiti: %10_",
        parse_mode="Markdown"
    )

async def cmd_yardim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    await update.message.reply_text(
        "🤖 *Side Hustle Bot — Komutlar*\n\n"
        "/rapor — Anlık fiyat tablosu\n"
        "/analiz — Detaylı piyasa analizi\n"
        "/haber — Haber özeti\n"
        "/portfoy — Portföy yapısı\n"
        "/yardim — Bu menü\n\n"
        "☀️ Sabah raporu her gün *08:00*'de otomatik gelir",
        parse_mode="Markdown"
    )

async def serbest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return
    soru = update.message.text
    await update.message.reply_text("💭 Düşünüyorum...")
    prices = get_prices()
    prompt = (
        f"Kullanıcı sorusu: '{soru}'. "
        f"Güncel fiyatlar: Altın {prices.get('altin')} TL, "
        f"Dolar {prices.get('USDTRY=X')} TL, KCHOL {prices.get('KCHOL.IS')} TL. "
        "Soruyu yanıtla. Gerekirse web'de ara. Kısa Türkçe yanıt."
    )
    yanit = ask_claude(prompt)
    await update.message.reply_text(yanit, parse_mode="Markdown")

async def sabah_raporu(app: Application):
    try:
        prices = get_prices()
        tarih = datetime.now(TR_TZ).strftime("%d %B %Y")
        prompt = (
            f"Bugün {tarih}. Sabah piyasa raporu hazırla. "
            f"Fiyatlar: Altın {prices.get('altin')} TL, Dolar {prices.get('USDTRY=X')} TL, "
            f"KCHOL {prices.get('KCHOL.IS')} TL, BIST100 {prices.get('XU100.IS')}. "
            "1) Bugün öne çıkan 2-3 haber, 2) Portföy için dikkat noktası. Max 200 kelime."
        )
        analiz = ask_claude(prompt)
        mesaj = (
            f"☀️ *Günaydın! Side Hustle Sabah Raporu*\n_{tarih}_\n\n"
            f"{price_table(prices)}\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🧠 *Analiz*\n\n{analiz}"
        )
        await app.bot.send_message(chat_id=ALLOWED_USER_ID, text=mesaj, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Sabah raporu hatası: {e}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("rapor",   cmd_rapor))
    app.add_handler(CommandHandler("analiz",  cmd_analiz))
    app.add_handler(CommandHandler("haber",   cmd_haber))
    app.add_handler(CommandHandler("portfoy", cmd_portfoy))
    app.add_handler(CommandHandler("yardim",  cmd_yardim))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, serbest))

    scheduler = AsyncIOScheduler(timezone=TR_TZ)
    scheduler.add_job(sabah_raporu, "cron", hour=8, minute=0, args=[app])
    scheduler.start()

    logger.info("Side Hustle Bot başladı ✅")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
