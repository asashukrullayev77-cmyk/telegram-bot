import os
import asyncio
import time
import tempfile
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

TOKEN = "8802164056:AAHUzN18Lr5a8S3lhKmuIJ4Ix0OP4X5_Jo4"

COOKIES_FILE = os.environ.get("COOKIES_FILE", "/root/cookies.txt")

def get_cookies_opt():
    if os.path.exists(COOKIES_FILE):
        return {'cookiefile': COOKIES_FILE}
    return {}

# ══════════════════════════════════════════
# /start
# ══════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or "Do'st"
    await update.message.reply_text(
        f"Salom, {name}! 👋\n\n"
        "🎶 *Music Bot* ga xush kelibsiz!\n\n"
        "📌 *Nima qila olaman:*\n"
        "🔍 Qo'shiq nomi → yuklash\n"
        "🔗 YouTube / Instagram / TikTok havolasi → video yoki audio\n"
        "🎧 Videodagi qo'shiqni topish → video yuboring\n\n"
        "▶️ Boshlash uchun havola yoki qo'shiq nomini yozing!",
        parse_mode='Markdown'
    )

# ══════════════════════════════════════════
# Yordamchi funksiyalar
# ══════════════════════════════════════════
def is_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")

def is_instagram(url: str) -> bool:
    return "instagram.com" in url or "instagr.am" in url

def is_tiktok(url: str) -> bool:
    return "tiktok.com" in url or "vm.tiktok.com" in url

def is_youtube(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url

def make_progress_hook(msg, loop):
    state = {'last_update': 0, 'last_pct': ''}
    def hook(d):
        if d['status'] == 'downloading':
            pct = d.get('_percent_str', '').strip()
            spd = d.get('_speed_str', '').strip()
            now = time.time()
            if pct and pct != state['last_pct'] and now - state['last_update'] > 3:
                state['last_pct'] = pct
                state['last_update'] = now
                asyncio.run_coroutine_threadsafe(
                    msg.edit_text(f"⏳ Yuklanmoqda... {pct}\n⚡ Tezlik: {spd}"),
                    loop
                )
    return hook

def find_file(directory: str, prefix: str) -> str | None:
    for f in os.listdir(directory):
        if f.startswith(prefix):
            return os.path.join(directory, f)
    return None

# ══════════════════════════════════════════
# Shazam orqali musiqa tanish
# ══════════════════════════════════════════
async def recognize_song(audio_path: str) -> dict | None:
    """ShazamIO kutubxonasi orqali qo'shiqni tanish"""
    try:
        from shazamio import Shazam
        shazam = Shazam()
        result = await shazam.recognize(audio_path)
        track = result.get('track', {})
        if not track:
            return None
        return {
            'title': track.get('title', ''),
            'artist': track.get('subtitle', ''),
            'genre': track.get('genres', {}).get('primary', ''),
            'cover': track.get('images', {}).get('coverart', ''),
            'apple_url': track.get('url', ''),
        }
    except ImportError:
        return None
    except Exception:
        return None

async def extract_short_audio(video_path: str, duration: int = 30) -> str | None:
    """Videoning boshidan 30 soniyalik audio kesib olish (Shazam uchun)"""
    out = video_path + "_shazam.mp3"
    cmd = [
        'ffmpeg', '-i', video_path,
        '-t', str(duration),
        '-vn', '-acodec', 'libmp3lame', '-q:a', '4',
        '-y', out
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    return out if os.path.exists(out) else None

# ══════════════════════════════════════════
# Musiqa qidirish (matn bo'yicha)
# ══════════════════════════════════════════
async def search_music(update: Update, query: str):
    msg = await update.message.reply_text("🔍 Qidirilmoqda...")
    try:
        loop = asyncio.get_running_loop()
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'extract_flat': False,
            'socket_timeout': 20,
            'noplaylist': True,
            **get_cookies_opt(),
        }

        def do_search():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(f"ytsearch5:{query}", download=False)

        info = await loop.run_in_executor(None, do_search)
        entries = [e for e in (info.get('entries') or []) if e and e.get('id')]

        if not entries:
            await msg.edit_text("❌ Hech narsa topilmadi!")
            return

        keyboard = []
        for e in entries[:5]:
            title = (e.get('title') or 'Nomsiz')[:48]
            dur = int(e.get('duration') or 0)
            keyboard.append([InlineKeyboardButton(
                f"🎵 {title} ({dur//60}:{dur%60:02d})",
                callback_data=f"dl_{e['id']}"
            )])

        await msg.edit_text(
            f"🎵 *{query}* bo'yicha natijalar:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        await msg.edit_text(f"❌ Xato: {e}")

# ══════════════════════════════════════════
# YouTube qo'shiq yuklash (tugma orqali)
# ══════════════════════════════════════════
async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    video_id = q.data.replace("dl_", "", 1)
    url = f"https://www.youtube.com/watch?v={video_id}"
    msg = await q.edit_message_text("⏳ Yuklanmoqda... 0%")
    loop = asyncio.get_running_loop()
    try:
        os.makedirs("downloads", exist_ok=True)
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'quiet': True, 'no_warnings': True,
            'socket_timeout': 30,
            'concurrent_fragment_downloads': 4,
            'progress_hooks': [make_progress_hook(msg, loop)],
            **get_cookies_opt(),
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            filename = ydl.prepare_filename(info)
        if not os.path.exists(filename):
            filename = find_file("downloads", video_id) or filename

        await msg.delete()
        with open(filename, 'rb') as f:
            await q.message.reply_audio(f, title=info.get('title', "Qo'shiq"), performer=info.get('uploader', ''))
        os.remove(filename)
    except Exception as e:
        await msg.edit_text(f"❌ Xato: {e}")

# ══════════════════════════════════════════
# Havola orqali video yuklash
# ══════════════════════════════════════════
async def download_video(update: Update, url: str, audio_only: bool = False):
    msg = await update.message.reply_text(
        "⏳ Audio yuklanmoqda... 0%" if audio_only else "⏳ Video yuklanmoqda... 0%"
    )
    loop = asyncio.get_running_loop()
    os.makedirs("downloads", exist_ok=True)

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15',
            'Accept-Language': 'en-US,en;q=0.5',
        }

        if audio_only:
            ydl_opts = {
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'outtmpl': 'downloads/%(id)s.%(ext)s',
                'quiet': True, 'no_warnings': True, 'socket_timeout': 30,
                'progress_hooks': [make_progress_hook(msg, loop)],
                'http_headers': headers,
                **get_cookies_opt(),
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
                filename = ydl.prepare_filename(info)
            if not os.path.exists(filename):
                filename = find_file("downloads", info.get('id', '')) or filename
            await msg.delete()
            with open(filename, 'rb') as f:
                await update.message.reply_audio(f, title=info.get('title', "Audio"), performer=info.get('uploader', ''))
            os.remove(filename)
            return

        # ── Video yuklash ──
        ydl_opts = {
            'format': 'best[ext=mp4][filesize<50M]/best[filesize<50M]/best',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'quiet': True, 'no_warnings': True, 'socket_timeout': 30,
            'concurrent_fragment_downloads': 4,
            'progress_hooks': [make_progress_hook(msg, loop)],
            'http_headers': headers,
            **get_cookies_opt(),
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            filename = ydl.prepare_filename(info)
        if not os.path.exists(filename):
            filename = find_file("downloads", info.get('id', '')) or filename

        title = info.get('title', 'Video')

        # ── Tugmalar: Audio + Musiqani topish ──
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎵 Faqat audio", callback_data=f"vaudio_{info.get('id','')}"),
            InlineKeyboardButton("🔍 Qo'shiqni topish", callback_data=f"shazam_{info.get('id','')}"),
        ]])

        # Faylni context ga saqlaymiz (shazam uchun)
        context.bot_data[f"vfile_{info.get('id','')}"] = filename
        context.bot_data[f"vinfo_{info.get('id','')}"] = info

        await msg.delete()
        with open(filename, 'rb') as f:
            await update.message.reply_video(f, caption=f"✅ {title}", reply_markup=keyboard)

    except Exception as e:
        err = str(e)
        if "Private" in err or "Login" in err or "login" in err:
            await msg.edit_text("❌ Bu post shaxsiy (private). Ochiq havola yuboring!")
        elif "Unsupported URL" in err:
            await msg.edit_text("❌ Bu havola qo'llab-quvvatlanmaydi.\nYouTube, Instagram, TikTok havolalarini yuboring.")
        else:
            await msg.edit_text(f"❌ Xato: {err}")

# ══════════════════════════════════════════
# Video → faqat audio callback
# ══════════════════════════════════════════
async def video_audio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    vid_id = q.data.replace("vaudio_", "", 1)
    filename = context.bot_data.get(f"vfile_{vid_id}")
    info = context.bot_data.get(f"vinfo_{vid_id}")

    if not filename or not os.path.exists(filename):
        await q.message.reply_text("❌ Fayl topilmadi. Havolani qayta yuboring.")
        return

    msg = await q.message.reply_text("🎵 Audio ajratilmoqda...")
    audio_out = filename + "_audio.mp3"
    cmd = ['ffmpeg', '-i', filename, '-vn', '-acodec', 'libmp3lame', '-q:a', '2', '-y', audio_out]
    proc = await asyncio.create_subprocess_exec(*cmd,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await proc.wait()

    if os.path.exists(audio_out):
        await msg.delete()
        with open(audio_out, 'rb') as f:
            await q.message.reply_audio(f,
                title=info.get('title', 'Audio') if info else 'Audio',
                performer=info.get('uploader', '') if info else '')
        os.remove(audio_out)
    else:
        await msg.edit_text("❌ Audio ajratib bo'lmadi. ffmpeg o'rnatilganmi?")

# ══════════════════════════════════════════
# Shazam callback — videodagi qo'shiqni topish
# ══════════════════════════════════════════
async def shazam_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    vid_id = q.data.replace("shazam_", "", 1)
    filename = context.bot_data.get(f"vfile_{vid_id}")

    if not filename or not os.path.exists(filename):
        await q.message.reply_text("❌ Fayl topilmadi. Havolani qayta yuboring.")
        return

    msg = await q.message.reply_text("🎧 Qo'shiq tanib olinmoqda...")

    # 30 soniyalik audio kesib olish
    short_audio = await extract_short_audio(filename)
    if not short_audio:
        await msg.edit_text("❌ ffmpeg topilmadi. Serverni tekshiring.")
        return

    result = await recognize_song(short_audio)
    os.remove(short_audio)

    if not result:
        await msg.edit_text(
            "❌ Qo'shiq tanib olinmadi.\n"
            "Sabab: video juda shovqinli yoki shazamio o'rnatilmagan.\n"
            "O'rnatish: `pip install shazamio`",
            parse_mode='Markdown'
        )
        return

    text = (
        f"🎵 *{result['title']}*\n"
        f"👤 {result['artist']}\n"
    )
    if result.get('genre'):
        text += f"🎸 {result['genre']}\n"

    # YouTube dan topish tugmasi
    search_q = f"{result['artist']} {result['title']}"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬇️ Shu qo'shiqni yuklab olish", callback_data=f"search_dl_{search_q[:50]}")
    ]])

    if result.get('cover'):
        try:
            await msg.delete()
            await q.message.reply_photo(result['cover'], caption=text, parse_mode='Markdown', reply_markup=keyboard)
            return
        except Exception:
            pass

    await msg.edit_text(text, parse_mode='Markdown', reply_markup=keyboard)

# ══════════════════════════════════════════
# Shazam topgan qo'shiqni yuklab olish
# ══════════════════════════════════════════
async def search_dl_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    search_q = q.data.replace("search_dl_", "", 1)
    msg = await q.message.reply_text(f"🔍 *{search_q}* qidirilmoqda...", parse_mode='Markdown')

    try:
        loop = asyncio.get_running_loop()
        ydl_opts = {
            'quiet': True, 'no_warnings': True,
            'skip_download': True, 'extract_flat': False,
            'socket_timeout': 20, 'noplaylist': True,
            **get_cookies_opt(),
        }
        def do_search():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(f"ytsearch3:{search_q}", download=False)

        info = await loop.run_in_executor(None, do_search)
        entries = [e for e in (info.get('entries') or []) if e and e.get('id')]

        if not entries:
            await msg.edit_text("❌ Topilmadi!")
            return

        keyboard = []
        for e in entries[:3]:
            title = (e.get('title') or 'Nomsiz')[:48]
            dur = int(e.get('duration') or 0)
            keyboard.append([InlineKeyboardButton(
                f"🎵 {title} ({dur//60}:{dur%60:02d})",
                callback_data=f"dl_{e['id']}"
            )])

        await msg.edit_text(
            f"🎵 *{search_q}* natijalar:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        await msg.edit_text(f"❌ Xato: {e}")

# ══════════════════════════════════════════
# Telegram orqali yuborilgan video/audio → Shazam
# ══════════════════════════════════════════
async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi to'g'ridan-to'g'ri video/audio yuborsa — shazam qilish"""
    msg = await update.message.reply_text("🎧 Qo'shiq tanib olinmoqda...")

    file = update.message.video or update.message.audio or update.message.voice or update.message.document
    if not file:
        await msg.edit_text("❌ Fayl topilmadi.")
        return

    os.makedirs("downloads", exist_ok=True)
    tmp_path = f"downloads/tg_{file.file_id[:10]}"

    try:
        tg_file = await context.bot.get_file(file.file_id)
        await tg_file.download_to_drive(tmp_path)
    except Exception as e:
        await msg.edit_text(f"❌ Fayl yuklab olinmadi: {e}")
        return

    # 30 soniyalik audio kesish
    short_audio = await extract_short_audio(tmp_path)
    try:
        os.remove(tmp_path)
    except Exception:
        pass

    if not short_audio:
        await msg.edit_text("❌ ffmpeg topilmadi. Serverni tekshiring.")
        return

    result = await recognize_song(short_audio)
    try:
        os.remove(short_audio)
    except Exception:
        pass

    if not result:
        await msg.edit_text(
            "❌ Qo'shiq tanib olinmadi.\n"
            "Sabab: audio sifati past yoki qo'shiq bazada yo'q.\n"
            "O'rnatish: `pip install shazamio`",
            parse_mode='Markdown'
        )
        return

    text = f"🎵 *{result['title']}*\n👤 {result['artist']}\n"
    if result.get('genre'):
        text += f"🎸 {result['genre']}\n"

    search_q = f"{result['artist']} {result['title']}"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬇️ Shu qo'shiqni yuklab olish", callback_data=f"search_dl_{search_q[:50]}")
    ]])

    if result.get('cover'):
        try:
            await msg.delete()
            await update.message.reply_photo(result['cover'], caption=text, parse_mode='Markdown', reply_markup=keyboard)
            return
        except Exception:
            pass

    await msg.edit_text(text, parse_mode='Markdown', reply_markup=keyboard)

# ══════════════════════════════════════════
# Matn xabar handler
# ══════════════════════════════════════════
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parts = text.split()

    # "havola audio" format
    if len(parts) >= 2 and is_url(parts[0]) and parts[-1].lower() == 'audio':
        await download_video(update, parts[0], audio_only=True)
        return

    if is_url(text):
        await download_video(update, text)
    else:
        await search_music(update, text)

# ══════════════════════════════════════════
# Ishga tushirish
# ══════════════════════════════════════════
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(MessageHandler(
    filters.VIDEO | filters.AUDIO | filters.VOICE |
    filters.Document.VIDEO | filters.Document.AUDIO,
    handle_media
))
app.add_handler(CallbackQueryHandler(download_callback,    pattern="^dl_"))
app.add_handler(CallbackQueryHandler(video_audio_callback, pattern="^vaudio_"))
app.add_handler(CallbackQueryHandler(shazam_callback,      pattern="^shazam_"))
app.add_handler(CallbackQueryHandler(search_dl_callback,   pattern="^search_dl_"))

print("Bot ishlamoqda... ✅")
app.run_polling(poll_interval=0.5)
