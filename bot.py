import os
import asyncio
import time
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

TOKEN = "8802164056:AAHUzN18Lr5a8S3lhKmuIJ4Ix0OP4X5_Jo4"

# ─────────────────────────────────────────
# /start
# ─────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or "Do'st"
    await update.message.reply_text(
        f"Salom, {name}! 👋\n\n"
        f"🎉 Asadbekning botiga xush kelibsiz!\n\n"
        "🎵 Musiqa — qo'shiq yoki qo'shiqchi ismini yozing\n"
        "📥 Video — YouTube/Instagram/TikTok havolasini yozing\n"
    )

# ─────────────────────────────────────────
# URL tekshirish
# ─────────────────────────────────────────
def is_url(text):
    return text.startswith("http://") or text.startswith("https://")

# ─────────────────────────────────────────
# Musiqa qidirish — katalog
# ─────────────────────────────────────────
async def search_music(update: Update, query: str):
    msg = await update.message.reply_text("🔍 Qidirilmoqda...")
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': 'in_playlist',
            'skip_download': True,
            'socket_timeout': 15,
        }

        loop = asyncio.get_running_loop()  # ✅ To'g'ri usul
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(
                None, lambda: ydl.extract_info(f"ytsearch5:{query}", download=False)
            )

        entries = info.get('entries', [])
        if not entries:
            await msg.edit_text("❌ Hech narsa topilmadi!")
            return

        keyboard = []
        for entry in entries[:5]:
            title = (entry.get('title') or 'Nomsiz')[:50]
            duration = entry.get('duration') or 0
            mins = int(duration) // 60
            secs = int(duration) % 60
            vid_id = entry.get('id', '')
            if not vid_id:
                continue
            keyboard.append([InlineKeyboardButton(
                f"🎵 {title} ({mins}:{secs:02d})",
                callback_data=f"dl_{vid_id}"
            )])

        if not keyboard:
            await msg.edit_text("❌ Natijalar topilmadi!")
            return

        await msg.edit_text(
            f"🎵 *{query}* bo'yicha natijalar:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    except Exception as e:
        await msg.edit_text(f"❌ Xato: {e}")

# ─────────────────────────────────────────
# Progress hook — rate limit xatosini oldini olish
# ─────────────────────────────────────────
def make_progress_hook(msg, loop):
    state = {'last_update': 0, 'last_percent': ''}

    def hook(d):
        if d['status'] == 'downloading':
            percent = d.get('_percent_str', '').strip()
            speed = d.get('_speed_str', '').strip()
            now = time.time()
            # ✅ Har 3 sekundda bir marta yangilash (rate limit oldini olish)
            if percent and percent != state['last_percent'] and now - state['last_update'] > 3:
                state['last_percent'] = percent
                state['last_update'] = now
                asyncio.run_coroutine_threadsafe(
                    msg.edit_text(f"⏳ Yuklanmoqda... {percent}\n⚡ Tezlik: {speed}"),
                    loop
                )
    return hook

# ─────────────────────────────────────────
# Callback — tugmadan qo'shiq yuklash
# ─────────────────────────────────────────
async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    video_id = query.data.replace("dl_", "", 1)
    url = f"https://www.youtube.com/watch?v={video_id}"

    msg = await query.edit_message_text("⏳ Yuklanmoqda... 0%")
    loop = asyncio.get_running_loop()  # ✅

    try:
        os.makedirs("downloads", exist_ok=True)
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'concurrent_fragment_downloads': 4,
            'progress_hooks': [make_progress_hook(msg, loop)],
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(
                None, lambda: ydl.extract_info(url, download=True)
            )
            # ✅ Haqiqiy fayl nomini topish
            filename = ydl.prepare_filename(info)
            if not os.path.exists(filename):
                # Kengaytma farq qilishi mumkin, qidirish
                base = os.path.splitext(filename)[0]
                for f in os.listdir("downloads"):
                    if f.startswith(video_id):
                        filename = os.path.join("downloads", f)
                        break

        await msg.delete()
        with open(filename, 'rb') as audio:
            await query.message.reply_audio(
                audio,
                title=info.get('title', "Qo'shiq"),
                performer=info.get('uploader', ''),
            )
        os.remove(filename)

    except Exception as e:
        await msg.edit_text(f"❌ Xato: {e}")

# ─────────────────────────────────────────
# Video yuklash (URL orqali)
# ─────────────────────────────────────────
async def download_video(update: Update, url: str):
    msg = await update.message.reply_text("⏳ Yuklanmoqda... 0%")
    loop = asyncio.get_running_loop()  # ✅

    try:
        os.makedirs("downloads", exist_ok=True)
        ydl_opts = {
            'format': 'best[ext=mp4][filesize<50M]/best[filesize<50M]/best',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'concurrent_fragment_downloads': 4,
            'progress_hooks': [make_progress_hook(msg, loop)],
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15',
                'Accept-Language': 'en-US,en;q=0.5',
            },
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(
                None, lambda: ydl.extract_info(url, download=True)
            )
            filename = ydl.prepare_filename(info)
            # ✅ Fayl mavjudligini tekshirish
            if not os.path.exists(filename):
                vid_id = info.get('id', '')
                for f in os.listdir("downloads"):
                    if f.startswith(vid_id):
                        filename = os.path.join("downloads", f)
                        break

        await msg.delete()
        with open(filename, 'rb') as video:
            await update.message.reply_video(
                video,
                caption=f"✅ {info.get('title', 'Video')}"
            )
        os.remove(filename)

    except Exception as e:
        await msg.edit_text(f"❌ Xato: {e}")

# ─────────────────────────────────────────
# Xabar handler
# ─────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if is_url(text):
        await download_video(update, text)
    else:
        await search_music(update, text)

# ─────────────────────────────────────────
# Botni ishga tushirish
# ─────────────────────────────────────────
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(CallbackQueryHandler(download_callback, pattern="^dl_"))

print("Bot ishlamoqda... ✅")
app.run_polling(poll_interval=0.5)
