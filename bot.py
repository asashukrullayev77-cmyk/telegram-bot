import os
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

TOKEN = os.environ.get"8802164056:AAExGebMcdZZ0Uwa9BdjBcBWfZwfGLHlLd4"

# ─────────────────────────────────────────
# /start komandasi
# ─────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    first_name = user.first_name or "Do'st"

    await update.message.reply_text(
        f"👋 Salom, {first_name}!\n\n"
        "🎉 Asadbekning botiga xush kelibsiz!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📌 Nima qila olaman:\n\n"
        "🎵 *Musiqa* — qo'shiq yoki qo'shiqchi ismini yozing\n"
        "   Masalan: `Shaxriyor Umarov` yoki `Closer`\n\n"
        "📥 *Video* — Instagram/YouTube havolasini yozing\n"
        "   Masalan: `https://youtube.com/...`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⬇️ Boshlash uchun yuboring!",
        parse_mode="Markdown"
    )

# ─────────────────────────────────────────
# URL tekshirish
# ─────────────────────────────────────────
def is_url(text):
    return text.startswith("http://") or text.startswith("https://")

# ─────────────────────────────────────────
# Video yuklab olish (havola orqali)
# ─────────────────────────────────────────
async def download_video(update: Update, url: str):
    msg = await update.message.reply_text("⏳ Video yuklanmoqda, iltimos kuting...")
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

        await msg.edit_text("📤 Yuborilmoqda...")
        with open(filename, 'rb') as video:
            await update.message.reply_video(
                video,
                caption=f"✅ *{info.get('title', 'Video')}*\n\n🤖 @{(await update.get_bot()).username}",
                parse_mode="Markdown"
            )
        os.remove(filename)
        await msg.delete()

    except Exception as e:
        await msg.edit_text(f"❌ Xato yuz berdi:\n`{e}`", parse_mode="Markdown")

# ─────────────────────────────────────────
# Qo'shiqchi qidirish — katalog chiqarish
# ─────────────────────────────────────────
async def search_artist_catalog(update: Update, query: str):
    msg = await update.message.reply_text(f"🔍 *{query}* bo'yicha qidirilmoqda...", parse_mode="Markdown")
    try:
        os.makedirs("downloads", exist_ok=True)

        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'default_search': f'ytsearch10:{query}',
            'noplaylist': False,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)

        entries = info.get('entries', [])
        if not entries:
            await msg.edit_text("❌ Hech narsa topilmadi.")
            return

        # Katalog tugmalari
        keyboard = []
        for i, entry in enumerate(entries[:10]):
            title = entry.get('title', f'Track {i+1}')
            video_id = entry.get('id', '')
            if video_id:
                keyboard.append([
                    InlineKeyboardButton(
                        f"🎵 {title[:45]}",
                        callback_data=f"dl_audio:{video_id}"
                    )
                ])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await msg.edit_text(
            f"🎤 *{query}* — qo'shiqlar ro'yxati:\n\n"
            "Qaysi birini yuklab olmoqchisiz?",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    except Exception as e:
        await msg.edit_text(f"❌ Xato: `{e}`", parse_mode="Markdown")

# ─────────────────────────────────────────
# Katalogdan tanlangan qo'shiqni yuklash
# ─────────────────────────────────────────
async def handle_catalog_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("dl_audio:"):
        return

    video_id = data.split("dl_audio:")[1]
    url = f"https://www.youtube.com/watch?v={video_id}"

    await query.message.edit_text("⏳ Qo'shiq yuklanmoqda...")
    try:
        os.makedirs("downloads", exist_ok=True)
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': 'downloads/%(title)s.%(ext)s',
            'quiet': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

        await query.message.edit_text("📤 Yuborilmoqda...")
        with open(filename, 'rb') as audio:
            await query.message.reply_audio(
                audio,
                title=info.get('title', 'Audio'),
                caption=f"🎵 *{info.get('title', 'Audio')}*\n\n🤖 @{(await query.get_bot()).username}",
                parse_mode="Markdown"
            )
        os.remove(filename)
        await query.message.delete()

    except Exception as e:
        await query.message.edit_text(f"❌ Xato: `{e}`", parse_mode="Markdown")

# ─────────────────────────────────────────
# Oddiy matn — musiqa qidirish yoki URL
# ─────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if is_url(text):
        await download_video(update, text)
    else:
        await search_artist_catalog(update, text)

# ─────────────────────────────────────────
# Bot ishga tushirish
# ─────────────────────────────────────────
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(handle_catalog_choice, pattern="^dl_audio:"))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

print("✅ Bot ishlamoqda...")
app.run_polling()
