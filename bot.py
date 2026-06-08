import os
import yt_dlp
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

TOKEN = os.environ.get("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! 🤖\n\n"
        "🎵 Musiqa — qo'shiq yoki qo'shiqchi ismini yozing\n"
        "📥 Video — Instagram/YouTube havolasini yozing\n"
    )

def is_url(text):
    return text.startswith("http://") or text.startswith("https://")

async def download_video(update: Update, url: str):
    await update.message.reply_text("⏳ Yuklanmoqda...")
    try:
        os.makedirs("downloads", exist_ok=True)
        ydl_opts = {
            'format': 'best[ext=mp4]/best',
            'outtmpl': 'downloads/%(title)s.%(ext)s',
            'quiet': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
        with open(filename, 'rb') as video:
            await update.message.reply_video(video, caption=f"✅ {info.get('title', 'Video')}")
        os.remove(filename)
    except Exception as e:
        await update.message.reply_text(f"❌ Xato: {e}")

async def download_music(update: Update, query: str):
    await update.message.reply_text(f"🔍 Qidirilmoqda...")
    try:
        os.makedirs("downloads", exist_ok=True)
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': 'downloads/%(title)s.%(ext)s',
            'quiet': True,
            'default_search': 'ytsearch1',
            'noplaylist': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=True)
            if 'entries' in info:
                info = info['entries'][0]
            filename = ydl.prepare_filename(info)
        with open(filename, 'rb') as audio:
            await update.message.reply_audio(audio, title=info.get('title', query))
        os.remove(filename)
    except Exception as e:
        await update.message.reply_text(f"❌ Xato: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if is_url(text):
        await download_video(update, text)
    else:
        await download_music(update, text)

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

print("Bot ishlamoqda... ✅")
app.run_polling()
