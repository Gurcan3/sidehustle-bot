import os
import asyncio
import logging
from datetime import datetime
import pytz
import httpx
import yfinance as yf
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Config (env variables) ────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
CLAUDE_API_KEY   = os.environ["CLAUDE_API_KEY"]
ALLOWED_USER_ID  = int(os.environ["ALLOWED_USER_ID"])   # sadece sen kullanırsın
TR_TZ            = pytz.timezone("Europe/Istanbul")

# ── Portföy yapılandırması ────────────────────────────────────────────────────
PORTFOLIO = {
    "Katman A — Savunma": {
        "altin_gram": {"sembol": "GC=F",  "isim": "Altın (gram TL)", "miktar": 0},
        "USDTRY":     {"sembol": "USDTRY=X", "isim": "Dolar/TL",    "miktar": 0},
        "EURTRY":     {"sembol": "EURTRY=X", "isim": "Euro/TL",     "miktar": 0},
        "GBPTRY":     {"sembol": "GBPTRY=X", "isim": "Sterlin/TL",  "miktar": 0},
    },
    "Katman B — Taarruz": {
        "KCHOL.IS":   {"sembol": "KCHOL.IS", "isim": "Koç Holding", "miktar": 0},
        "XU100.IS":   {"sembol": "XU100.IS", "isim": "BIST 100 ETF","miktar": 0},
    }
}

# ── Fiyat çekme ───────────────────────────────────────────────────────────────
def get_prices() -> dict:
    """Tüm enstrümanların güncel fiyatlarını çeker."""
    prices = {}
    semboller = []
    for katman in PORTFOLIO.values():
        for key, val in katman.items():
            semboller.append(val["sembol"])

    try:
        data = yf.download(semboller, period="2d", interval="1d", progress=False, auto_adjust=True)
        close = data["Close"] if "Close" in data.columns else data

        # Altın özel hesap: ons fiyatı → gram TL
        usd_try = float(close["USDTRY=X"].dropna().iloc[-1])
        gold_usd_oz = float(close["GC=F"].dropna().iloc[-1])
        gold_gram_try = round((gold_usd_oz / 31.1035) * usd_try, 2)
        prices["altin_gram"] = gold_gram_try

        for sembol in semboller:
            if sembol == "GC=F":
                continue
            try:
                val = float(close[sembol].dropna().iloc[-1])
                prices[sembol] = round(val, 4)
            except Exception:
                prices[sembol] = None

    except Exception as e:
        logger.error(f"Fiyat çekme hatası: {e}")

    return prices


def format_price_table(prices: dict) -> str:
    """Fiyatları güzel tablo formatına çevirir."""
    lines = ["📊 *Güncel Fiyatlar*\n"]
    mapping = {
        "altin_gram": "🥇 Altın (gram)",
        "USDTRY=X":   "💵 Dolar/TL",
        "EURTRY=X":   "💶 Euro/TL",
        "GBPTRY=X":   "💷 Sterlin/TL",
        "KCHOL.IS":   "🏭 Koç Holding",
        "XU100.IS":   "📈 BIST 100",
    }
    for key, label in mapping.items():
        val = prices.get(key)
        if val:
            lines.append(f"{label}: `{val:,.2f} TL`")
        else:
            lines.append(f"{label}: `—`")
    return "\n".join(lines)


# ── Claude API çağrısı ────────────────────────────────────────────────────────
async def ask_claude(prompt: str, system: str = None) -> str:
    """Claude API'ye istek gönderir ve yanıt döner."""
    if system is None:
        system = (
            "Sen 'Side Hustle' adlı bir finansal asistansın. "
            "Türkiye piyasasını takip eden, BIST, altın, döviz konusunda uzman, "
            "Türkçe konuşan bir yatırım danışmanısın. "
            "Kısa, net, aksiyon odaklı cevaplar ver. "
            "Kesin getiri garantisi verme, her zaman risk uyarısı ekle. "
            "Kullanıcının portföyü: %60 savunma (altın/döviz), %40 taarruz (BIST hisse/ETF). "
            "Drawdown limiti %10. Yıllık hedef: faizi geç, bileşik büyü."
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
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
        # Tüm text bloklarını birleştir
        text = " ".join(
            block["text"] for block in data.get("content", [])
            if block.get("type") == "text"
        )
        return text.strip() or "Yanıt alınamadı."


# ── Sabah raporu ──────────────────────────────────────────────────────────────
async def sabah_raporu(context: ContextTypes.DEFAULT_TYPE):
    """Her sabah 08:00'de otomatik çalışır."""
    try:
        prices = get_prices()
        fiyat_tablosu = format_price_table(prices)

        altin  = prices.get("altin_gram", "—")
        usd    = prices.get("USDTRY=X", "—")
        eur    = prices.get("EURTRY=X", "—")
        gbp    = prices.get("GBPTRY=X", "—")
        kchol  = prices.get("KCHOL.IS", "—")
        bist   = prices.get("XU100.IS", "—")
        tarih  = datetime.now(TR_TZ).strftime("%d %B %Y, %A")

        prompt = f"""
Bugün {tarih}. Portföy sahibine sabah raporu hazırla.

Güncel fiyatlar:
- Altın (gram TL): {altin}
- Dolar/TL: {usd}
- Euro/TL: {eur}
- Sterlin/TL: {gbp}
- Koç Holding (KCHOL): {kchol} TL
- BIST 100: {bist}

Şunları yap:
1. Bu sabah Türkiye ve global piyasalarda öne çıkan 2-3 haberi özetle
2. Portföy için bugün dikkat edilmesi gereken 1-2 nokta belirt
3. Varsa risk uyarısı ver
4. Kısa ve net olsun, maksimum 250 kelime
"""
        analiz = await ask_claude(prompt)

        mesaj = (
            f"☀️ *Günaydın! Side Hustle Sabah Raporu*\n"
            f"_{tarih}_\n\n"
            f"{fiyat_tablosu}\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🧠 *Piyasa Analizi*\n\n"
            f"{analiz}\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💬 Soru için /analiz veya direkt yaz"
        )
        await context.bot.send_message(
            chat_id=ALLOWED_USER_ID,
            text=mesaj,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Sabah raporu hatası: {e}")
        await context.bot.send_message(
            chat_id=ALLOWED_USER_ID,
            text=f"⚠️ Sabah raporu oluşturulamadı: {e}"
        )


# ── Komutlar ──────────────────────────────────────────────────────────────────
def yetki_kontrol(update: Update) -> bool:
    return update.effective_user.id == ALLOWED_USER_ID


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not yetki_kontrol(update): return
    await update.message.reply_text(
        "👋 *Side Hustle Bot aktif!*\n\n"
        "Komutlar:\n"
        "/rapor — anlık fiyat raporu\n"
        "/analiz — piyasa analizi\n"
        "/haber — güncel haberler\n"
        "/drawdown — risk kontrolü\n"
        "/portfoy — portföy durumu\n"
        "/yardim — tüm komutlar\n\n"
        "Ya da direkt soru sor:\n"
        "_'Bugün KCHOL almalı mıyım?'_",
        parse_mode="Markdown"
    )


async def cmd_rapor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not yetki_kontrol(update): return
    await update.message.reply_text("📡 Fiyatlar çekiliyor...")
    prices = get_prices()
    tablo = format_price_table(prices)
    tarih = datetime.now(TR_TZ).strftime("%d/%m/%Y %H:%M")
    await update.message.reply_text(
        f"{tablo}\n\n_Güncelleme: {tarih}_",
        parse_mode="Markdown"
    )


async def cmd_analiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not yetki_kontrol(update): return
    await update.message.reply_text("🧠 Analiz hazırlanıyor, 15-20 saniye...")
    prices = get_prices()
    prompt = f"""
Bugünkü fiyatlar: Altın {prices.get('altin_gram')} TL/gram, 
Dolar {prices.get('USDTRY=X')} TL, Euro {prices.get('EURTRY=X')} TL,
Sterlin {prices.get('GBPTRY=X')} TL, KCHOL {prices.get('KCHOL.IS')} TL,
BIST100 {prices.get('XU100.IS')}.

Güncel piyasa haberlerini tara ve şunları değerlendir:
1. Piyasada genel trend ne yönde?
2. Portföy için bu hafta önerilen hareket nedir?
3. Herhangi bir risk var mı?
Net, kısa, aksiyon odaklı yaz.
"""
    yanit = await ask_claude(prompt)
    await update.message.reply_text(f"📊 *Piyasa Analizi*\n\n{yanit}", parse_mode="Markdown")


async def cmd_haber(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not yetki_kontrol(update): return
    await update.message.reply_text("📰 Haberler taranıyor...")
    prompt = """
Türkiye finans piyasaları için bugünün en önemli 5 haberini tara.
Şunlara odaklan: BIST, TL kuru, altın, merkez bankası, enflasyon, 
büyük Türk şirketleri (özellikle Koç Holding).
Her haberi 1-2 cümleyle özetle. Portföye etkisini belirt.
"""
    yanit = await ask_claude(prompt)
    await update.message.reply_text(f"📰 *Güncel Haberler*\n\n{yanit}", parse_mode="Markdown")


async def cmd_drawdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not yetki_kontrol(update): return
    await update.message.reply_text(
        "🛡️ *Drawdown Kontrolü*\n\n"
        "Portföy değerlerini girmem için şu formatta yaz:\n\n"
        "`drawdown 6000 5500 4000 3800`\n\n"
        "_(Katman A başlangıç, güncel — Katman B başlangıç, güncel)_",
        parse_mode="Markdown"
    )


async def cmd_portfoy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not yetki_kontrol(update): return
    prices = get_prices()
    tarih = datetime.now(TR_TZ).strftime("%d/%m/%Y %H:%M")
    mesaj = (
        f"💼 *Portföy Yapısı*\n_{tarih}_\n\n"
        f"*Katman A — Savunma (%60)*\n"
        f"🥇 Altın: `{prices.get('altin_gram', '—'):,.2f} TL/gram`\n"
        f"💵 Dolar: `{prices.get('USDTRY=X', '—'):,.4f} TL`\n"
        f"💶 Euro: `{prices.get('EURTRY=X', '—'):,.4f} TL`\n"
        f"💷 Sterlin: `{prices.get('GBPTRY=X', '—'):,.4f} TL`\n\n"
        f"*Katman B — Taarruz (%40)*\n"
        f"🏭 Koç Holding: `{prices.get('KCHOL.IS', '—'):,.2f} TL`\n"
        f"📈 BIST 100: `{prices.get('XU100.IS', '—'):,.2f}`\n\n"
        f"_Drawdown limiti: %10_\n"
        f"_Detaylı analiz için /analiz_"
    )
    await update.message.reply_text(mesaj, parse_mode="Markdown")


async def cmd_yardim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not yetki_kontrol(update): return
    await update.message.reply_text(
        "🤖 *Side Hustle Bot — Komutlar*\n\n"
        "/rapor — Anlık fiyat tablosu\n"
        "/analiz — Detaylı piyasa analizi + haberler\n"
        "/haber — Sadece haber özeti\n"
        "/drawdown — Risk & drawdown kontrolü\n"
        "/portfoy — Portföy yapısı\n"
        "/yardim — Bu menü\n\n"
        "*Serbest soru:*\n"
        "Komut olmadan da yazabilirsin:\n"
        "• _'KCHOL bugün alınır mı?'_\n"
        "• _'Altın düşer mi bu hafta?'_\n"
        "• _'Dolar için ne düşünüyorsun?'_\n\n"
        "☀️ Sabah raporu her gün 08:00'de gelir",
        parse_mode="Markdown"
    )


async def serbest_mesaj(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Komut olmadan yazılan mesajları Claude'a iletir."""
    if not yetki_kontrol(update): return
    soru = update.message.text
    await update.message.reply_text("💭 Düşünüyorum...")
    prices = get_prices()
    prompt = f"""
Kullanıcının sorusu: "{soru}"

Bağlam - güncel fiyatlar:
Altın {prices.get('altin_gram')} TL/gram, Dolar {prices.get('USDTRY=X')} TL,
Euro {prices.get('EURTRY=X')} TL, KCHOL {prices.get('KCHOL.IS')} TL

Soruyu yanıtla. Güncel haber veya veri gerekiyorsa web'de ara.
Kısa, net, Türkçe yanıt ver.
"""
    yanit = await ask_claude(prompt)
    await update.message.reply_text(yanit, parse_mode="Markdown")


# ── Ana uygulama ──────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Komutları kaydet
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("rapor",    cmd_rapor))
    app.add_handler(CommandHandler("analiz",   cmd_analiz))
    app.add_handler(CommandHandler("haber",    cmd_haber))
    app.add_handler(CommandHandler("drawdown", cmd_drawdown))
    app.add_handler(CommandHandler("portfoy",  cmd_portfoy))
    app.add_handler(CommandHandler("yardim",   cmd_yardim))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, serbest_mesaj))

    # Sabah 08:00 zamanlayıcısı (TR saati)
    scheduler = AsyncIOScheduler(timezone=TR_TZ)
    scheduler.add_job(
        sabah_raporu,
        trigger="cron",
        hour=8,
        minute=0,
        args=[app]
    )
    scheduler.start()
    logger.info("Side Hustle Bot başlatıldı — sabah 08:00 raporu aktif")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
