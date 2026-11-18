import asyncio
import logging
import os
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, ChatJoinRequest, InlineKeyboardMarkup,
    InlineKeyboardButton, FSInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from dotenv import load_dotenv

from bot.db import Database

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("autojoin")

TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
DB_PATH = os.getenv("DB_PATH", "data/bot.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")
PUBLIC_URL = (os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/telegram-webhook")
PORT = int(os.getenv("PORT", "10000"))
USE_WEBHOOK = os.getenv("USE_WEBHOOK", "false").lower() == "true"

if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required.")
if not ADMIN_IDS:
    raise RuntimeError("Set ADMIN_IDS to your numeric Telegram user ID.")

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

db = Database(DB_PATH)
dp = Dispatcher(storage=MemoryStorage())
bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
BOT_USERNAME = None


class Setup(StatesGroup):
    welcome_text = State()
    button_text = State()
    broadcast_content = State()
    restore_file = State()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📡 My Channels", callback_data="channels"),
         InlineKeyboardButton(text="➕ Add Channel", callback_data="add_channel_help")],
        [InlineKeyboardButton(text="✏️ Welcome Setup", callback_data="welcome_help"),
         InlineKeyboardButton(text="🔘 Button Setup", callback_data="button_help")],
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="broadcast_help"),
         InlineKeyboardButton(text="👥 User Database", callback_data="users")],
        [InlineKeyboardButton(text="📊 Statistics", callback_data="stats"),
         InlineKeyboardButton(text="💾 Backup & Restore", callback_data="backup")],
        [InlineKeyboardButton(text="ℹ️ Help", callback_data="help")]
    ])


def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Main Menu", callback_data="home")]
    ])


def welcome_keyboard(channel_id: int, button_text: str):
    # The start payload records that this user arrived through this channel's welcome button.
    url = f"https://t.me/{BOT_USERNAME}?start=w_{abs(channel_id)}"
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=button_text, url=url)]])


async def admin_guard(message: Message) -> bool:
    if not message.from_user or not is_admin(message.from_user.id):
        await message.answer("⛔ এই কমান্ডটি শুধু Bot Admin ব্যবহার করতে পারবেন।")
        return False
    return True


async def send_panel(message: Message):
    await message.answer(
        "🤖 <b>AutoJoin Welcome Bot</b>\n\nনিচের মেনু থেকে Bot পরিচালনা করুন।",
        reply_markup=main_menu()
    )


@dp.message(CommandStart())
async def start_handler(message: Message):
    global BOT_USERNAME
    user = message.from_user
    if not user:
        return
    payload = ""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1:
        payload = parts[1].strip()

    source_channel = None
    if payload.startswith("w_"):
        try:
            source_channel = int(payload[2:])
        except ValueError:
            source_channel = None

    db.upsert_user(user.id, user.first_name or "", user.username or "", source_channel)
    if is_admin(user.id):
        await send_panel(message)
    else:
        await message.answer(
            "🎉 Welcome!\n\nআপনি সফলভাবে Bot চালু করেছেন। এখন থেকে প্রয়োজনীয় আপডেট পেতে পারবেন।"
        )


@dp.message(Command("panel"))
async def panel_command(message: Message):
    if await admin_guard(message):
        await send_panel(message)


@dp.callback_query(F.data == "home")
async def home_callback(call: CallbackQuery):
    if not call.from_user or not is_admin(call.from_user.id):
        await call.answer("Not allowed", show_alert=True)
        return
    await call.message.edit_text("🤖 <b>AutoJoin Welcome Bot</b>\n\nনিচের মেনু থেকে Bot পরিচালনা করুন।",
                                reply_markup=main_menu())
    await call.answer()


@dp.callback_query(F.data == "channels")
async def channels_callback(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Not allowed", show_alert=True); return
    channels = db.list_channels()
    if not channels:
        text = "📡 <b>My Channels</b>\n\nএখনো কোনো Channel যোগ করা হয়নি।\n\nযোগ করতে:\n<code>/addchannel -1001234567890</code>"
    else:
        lines = ["📡 <b>My Channels</b>"]
        for ch in channels:
            lines.append(f"\n• <b>{ch['title']}</b>\n  ID: <code>{ch['chat_id']}</code>\n  Welcome: {'ON' if ch['enabled'] else 'OFF'}")
        lines.append("\nনতুন Channel: <code>/addchannel -1001234567890</code>")
        text = "\n".join(lines)
    await call.message.edit_text(text, reply_markup=back_menu())
    await call.answer()


@dp.callback_query(F.data == "add_channel_help")
async def add_channel_help(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Not allowed", show_alert=True); return
    await call.message.edit_text(
        "➕ <b>Channel Add</b>\n\n১) Bot-কে Channel Admin করুন।\n২) <b>Invite Users / Add Subscribers</b> permission দিন।\n৩) Channel ID সংগ্রহ করে পাঠান:\n<code>/addchannel -1001234567890</code>\n\nBot-কে Admin না করলে Join Request approve করতে পারবে না।",
        reply_markup=back_menu())
    await call.answer()


@dp.message(Command("addchannel"))
async def add_channel(message: Message):
    if not await admin_guard(message): return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().lstrip("-").isdigit():
        await message.answer("ব্যবহার: <code>/addchannel -1001234567890</code>")
        return
    chat_id = int(parts[1].strip())
    try:
        chat = await bot.get_chat(chat_id)
        member = await bot.get_chat_member(chat_id, (await bot.get_me()).id)
        if member.status not in ("administrator", "creator"):
            await message.answer("⛔ আগে Bot-কে ওই Channel-এর Admin করুন।")
            return
        db.add_channel(chat_id, chat.title or str(chat_id))
        await message.answer(f"✅ <b>{chat.title}</b> যোগ হয়েছে।\nএখন <code>/welcome {chat_id}</code> দিয়ে Welcome Text সেট করুন।")
    except TelegramBadRequest as e:
        await message.answer(f"Channel পাওয়া যায়নি বা Bot-এর permission নেই। Channel ID ঠিক আছে কি না দেখুন।\n\n<code>{e.message}</code>")


@dp.message(Command("welcome"))
async def welcome_command(message: Message, state: FSMContext):
    if not await admin_guard(message): return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("ব্যবহার: <code>/welcome -1001234567890</code>")
        return
    cid = int(parts[1])
    if not db.get_channel(cid):
        await message.answer("এই Channel Bot-এ যোগ করা নেই। আগে /addchannel দিন।"); return
    await state.update_data(channel_id=cid)
    await state.set_state(Setup.welcome_text)
    await message.answer("এখন নতুন Welcome Text পাঠান। HTML formatting ও {first_name}, {username} ব্যবহার করতে পারবেন।")


@dp.message(Setup.welcome_text)
async def save_welcome(message: Message, state: FSMContext):
    if not await admin_guard(message): return
    data = await state.get_data()
    text = message.text or message.caption
    if not text:
        await message.answer("Text হিসেবে Welcome Message পাঠান।"); return
    db.set_channel_field(data["channel_id"], "welcome_text", text)
    await state.clear()
    await message.answer("✅ Welcome Text সংরক্ষণ হয়েছে।")


@dp.message(Command("button"))
async def button_command(message: Message, state: FSMContext):
    if not await admin_guard(message): return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("ব্যবহার: <code>/button -1001234567890</code>"); return
    cid = int(parts[1])
    if not db.get_channel(cid):
        await message.answer("এই Channel Bot-এ যোগ করা নেই।"); return
    await state.update_data(channel_id=cid)
    await state.set_state(Setup.button_text)
    await message.answer("Welcome Message-এর Inline Button-এর নতুন Text পাঠান।")


@dp.message(Setup.button_text)
async def save_button(message: Message, state: FSMContext):
    if not await admin_guard(message): return
    data = await state.get_data()
    label = (message.text or "").strip()
    if not label or len(label) > 64:
        await message.answer("Button text ১-৬৪ অক্ষরের মধ্যে দিন।"); return
    db.set_channel_field(data["channel_id"], "button_text", label)
    await state.clear()
    await message.answer("✅ Button Text সংরক্ষণ হয়েছে। Button-এ চাপলে Bot-এর /start deep link খুলবে।")


@dp.callback_query(F.data == "welcome_help")
async def welcome_help(call: CallbackQuery):
    await call.message.edit_text("✏️ Welcome Text সেট করতে:\n<code>/welcome -1001234567890</code>\n\nতারপর আপনার Welcome Message পাঠান।\n\nPlaceholder: <code>{first_name}</code>, <code>{username}</code>", reply_markup=back_menu())
    await call.answer()


@dp.callback_query(F.data == "button_help")
async def button_help(call: CallbackQuery):
    await call.message.edit_text("🔘 Button Text সেট করতে:\n<code>/button -1001234567890</code>\n\nতারপর Button-এর লেখা পাঠান।\n\nButton URL Bot নিজেই তৈরি করবে এবং /start payload দিয়ে source Channel track করবে।", reply_markup=back_menu())
    await call.answer()


@dp.chat_join_request()
async def join_request_handler(request: ChatJoinRequest):
    cid = request.chat.id
    user = request.from_user
    ch = db.get_channel(cid)
    # Keep a record even if the channel has not yet been configured.
    db.upsert_user(user.id, user.first_name or "", user.username or "", None, joined_channel=cid)
    try:
        await bot.approve_chat_join_request(chat_id=cid, user_id=user.id)
        db.mark_approved(user.id, cid)
    except TelegramBadRequest as e:
        log.warning("Could not approve join request %s: %s", user.id, e)
        return

    # Telegram permits a bot with invite-users rights to contact a join requester
    # through user_chat_id for a short period (up to 5 minutes).
    if ch and ch["enabled"]:
        first = user.first_name or "বন্ধু"
        welcome = ch["welcome_text"].replace("{first_name}", first).replace("{username}", user.username or "")
        keyboard = welcome_keyboard(cid, ch["button_text"])
        try:
            await bot.send_message(chat_id=request.user_chat_id, text=welcome, reply_markup=keyboard)
            db.mark_welcome_sent(user.id, cid)
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            log.info("Welcome DM unavailable for user %s: %s", user.id, e)


@dp.callback_query(F.data == "users")
async def users_callback(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Not allowed", show_alert=True); return
    stats = db.user_counts()
    await call.message.edit_text(
        "👥 <b>User Database</b>\n\n"
        f"All started users: <b>{stats['all']}</b>\n"
        f"Started from welcome button: <b>{stats['clicked']}</b>\n"
        f"Join request users not started: <b>{stats['not_started']}</b>\n\n"
        "CSV export: <code>/exportusers</code> (সব), <code>/exportclicked</code> (Button থেকে Start করা User)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔘 Button Clicked Users List", callback_data="clicked_list")],
            [InlineKeyboardButton(text="⬅️ Main Menu", callback_data="home")]
        ]))
    await call.answer()


@dp.callback_query(F.data == "clicked_list")
async def clicked_list_callback(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Not allowed", show_alert=True); return
    rows = db.list_clicked_users(limit=20)
    if not rows:
        text = "🔘 এখনো কেউ Welcome Button-এর Deep Link দিয়ে Bot Start করেনি।"
    else:
        lines = ["🔘 <b>Button Clicked Users (সর্বশেষ ২০ জন)</b>"]
        for row in rows:
            name = (row["first_name"] or "User").replace("<", "&lt;").replace(">", "&gt;")
            uname = f"@{row['username']}" if row["username"] else "No username"
            lines.append(f"• {name} — {uname} — <code>{row['user_id']}</code>")
        text = "\n".join(lines) + "\n\nসম্পূর্ণ তালিকা: <code>/exportclicked</code>"
    await call.message.edit_text(text, reply_markup=back_menu())
    await call.answer()


@dp.callback_query(F.data == "stats")
async def stats_callback(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Not allowed", show_alert=True); return
    s = db.stats()
    await call.message.edit_text(
        "📊 <b>Statistics</b>\n\n"
        f"Channels: <b>{s['channels']}</b>\n"
        f"Join request records: <b>{s['join_requests']}</b>\n"
        f"Approved requests: <b>{s['approved']}</b>\n"
        f"Started users: <b>{s['users']}</b>\n"
        f"Started from welcome button: <b>{s['clicked']}</b>\n"
        f"Welcome DMs sent: <b>{s['welcomes']}</b>",
        reply_markup=back_menu())
    await call.answer()


@dp.message(Command("exportusers"))
async def export_users(message: Message):
    if not await admin_guard(message): return
    path = db.export_csv()
    await message.answer_document(FSInputFile(path, filename="users.csv"), caption="👥 All users database export")


@dp.message(Command("exportclicked"))
async def export_clicked(message: Message):
    if not await admin_guard(message): return
    path = db.export_csv(clicked_only=True)
    await message.answer_document(FSInputFile(path, filename="button_clicked_users.csv"), caption="🔘 Users who started through the welcome button")


@dp.callback_query(F.data == "broadcast_help")
async def broadcast_help(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Not allowed", show_alert=True); return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 All started users", callback_data="bc:all")],
        [InlineKeyboardButton(text="🔘 Started from welcome button", callback_data="bc:clicked")],
        [InlineKeyboardButton(text="⏳ Join-request users not started", callback_data="bc:not_started")],
        [InlineKeyboardButton(text="⬅️ Main Menu", callback_data="home")]
    ])
    await call.message.edit_text("📢 <b>Broadcast Audience</b>\n\nকাদের কাছে পাঠাতে চান?", reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data.startswith("bc:"))
async def broadcast_audience(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Not allowed", show_alert=True); return
    audience = call.data.split(":", 1)[1]
    if audience == "not_started":
        await call.message.edit_text(
            "এই তালিকার User-রা Bot-এ /start করেনি। Telegram তাদের কাছে proactive broadcast অনুমোদন করে না। তারা Start করলে পরে broadcast পাওয়া যাবে।",
            reply_markup=back_menu())
        await call.answer(); return
    await state.update_data(audience=audience)
    await state.set_state(Setup.broadcast_content)
    await call.message.answer("এখন Broadcast Content পাঠান। Text, Photo, Video বা Document পাঠাতে পারবেন। Cancel করতে /cancel লিখুন।")
    await call.answer()


@dp.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):
    if is_admin(message.from_user.id):
        await state.clear()
        await message.answer("কাজ বাতিল হয়েছে।", reply_markup=main_menu())


@dp.message(Setup.broadcast_content)
async def broadcast_content(message: Message, state: FSMContext):
    if not await admin_guard(message): return
    data = await state.get_data()
    audience = data.get("audience", "all")
    user_ids = db.broadcast_targets(audience)
    await state.clear()
    status = await message.answer(f"📢 Broadcast শুরু হচ্ছে...\nTarget: {len(user_ids)}")
    sent = failed = 0
    for uid in user_ids:
        try:
            await message.copy_to(chat_id=uid)
            sent += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            failed += 1
            db.mark_blocked(uid)
        except Exception as e:
            failed += 1
            log.warning("Broadcast failed for %s: %s", uid, e)
        if (sent + failed) % 25 == 0:
            await asyncio.sleep(1)
    await status.edit_text(
        f"✅ <b>Broadcast Finished</b>\n\nTarget: {len(user_ids)}\nSuccessful: {sent}\nFailed: {failed}"
    )


@dp.callback_query(F.data == "backup")
async def backup_callback(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Not allowed", show_alert=True); return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💾 Create & Download Backup", callback_data="backup_now")],
        [InlineKeyboardButton(text="♻️ Restore from .db file", callback_data="restore_help")],
        [InlineKeyboardButton(text="⬅️ Main Menu", callback_data="home")]
    ])
    await call.message.edit_text("💾 <b>Backup & Restore</b>\n\nDatabase backup তৈরি করে ডাউনলোড করতে পারবেন।", reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "backup_now")
async def backup_now(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Not allowed", show_alert=True); return
    path = db.backup(BACKUP_DIR)
    await call.message.answer_document(FSInputFile(path), caption="✅ Database backup. নিরাপদ জায়গায় রেখে দিন।")
    await call.answer("Backup ready")


@dp.callback_query(F.data == "restore_help")
async def restore_help(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Not allowed", show_alert=True); return
    await state.set_state(Setup.restore_file)
    await call.message.answer("⚠️ Restore করলে বর্তমান Database replace হবে। Restore-এর আগে Safety Backup তৈরি হবে। এখন এই Bot-এর তৈরি করা SQLite .db backup file পাঠান।")
    await call.answer()


@dp.message(Setup.restore_file, F.document)
async def restore_file(message: Message, state: FSMContext):
    if not await admin_guard(message): return
    doc = message.document
    if not doc.file_name.lower().endswith(".db"):
        await message.answer("শুধু .db backup file গ্রহণ করা হবে।"); return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    temp = os.path.join(BACKUP_DIR, "restore_upload.db")
    await bot.download(doc, destination=temp)
    try:
        db.restore(temp, BACKUP_DIR)
        await message.answer("♻️ Restore সম্পন্ন হয়েছে। Bot-এর তৈরি করা backup file ব্যবহার করা হয়েছে।")
    except Exception as e:
        await message.answer(f"❌ Restore করা যায়নি: <code>{str(e)[:300]}</code>")
    finally:
        await state.clear()
        if os.path.exists(temp):
            os.remove(temp)


@dp.callback_query(F.data == "help")
async def help_callback(call: CallbackQuery):
    await call.message.edit_text(
        "ℹ️ <b>Quick Commands</b>\n\n"
        "<code>/panel</code> — Admin Panel\n"
        "<code>/addchannel -100...</code> — Channel যোগ\n"
        "<code>/channels</code> — Channel list (Panel-এও আছে)\n"
        "<code>/welcome -100...</code> — Welcome Text সেট\n"
        "<code>/button -100...</code> — Button Text সেট\n"
        "<code>/exportusers</code> — CSV Export\n"
        "<code>/cancel</code> — চলমান কাজ বাতিল\n\n"
        "Bot-কে Channel Admin করে Invite Users permission দিন।",
        reply_markup=back_menu())
    await call.answer()


async def health(request):
    return web.Response(text="AutoJoin Welcome Bot is running.")


async def run_webhook():
    if not PUBLIC_URL:
        raise RuntimeError("PUBLIC_URL is required when USE_WEBHOOK=true")
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_post(WEBHOOK_PATH, lambda req: handle_webhook(req))
    await bot.set_webhook(PUBLIC_URL + WEBHOOK_PATH, allowed_updates=dp.resolve_used_update_types())
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Webhook server started on port %s", PORT)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


async def handle_webhook(request):
    from aiogram.types import Update
    data = await request.json()
    update = Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return web.Response(text="ok")


async def daily_backup_loop():
    while True:
        try:
            await asyncio.sleep(24 * 60 * 60)
            path = db.backup(BACKUP_DIR)
            log.info("Automatic daily backup created: %s", path)
            # Keep the latest 7 automatic/manual backup files in the backup folder.
            backups = sorted(
                [os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR) if f.endswith(".db") and f.startswith("autojoin_backup_")],
                key=os.path.getmtime, reverse=True
            )
            for old in backups[7:]:
                os.remove(old)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Daily backup failed")


async def main():
    global BOT_USERNAME
    me = await bot.get_me()
    BOT_USERNAME = me.username
    log.info("Starting @%s", BOT_USERNAME)
    asyncio.create_task(daily_backup_loop())
    if USE_WEBHOOK:
        await run_webhook()
    else:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
