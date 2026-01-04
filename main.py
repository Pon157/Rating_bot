import asyncio
import os
import logging
import traceback
from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ErrorEvent
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from supabase import create_client, Client
from dotenv import load_dotenv

# --- ИНИЦИАЛИЗАЦИЯ ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN") 
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", 0))

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

CATEGORIES = {
    "support_bots": "Боты поддержки",
    "support_admins": "Админы поддержки",
    "lot_channels": "Каналы лотов",
    "check_channels": "Каналы проверок",
    "kmbp_channels": "Каналы КМБП"
}

RATING_MAP = {1: -5, 2: -2, 3: 0, 4: 2, 5: 5}

class ReviewState(StatesGroup):
    waiting_for_text = State()
    waiting_for_rate = State()

# --- MIDDLEWARE (БАН) ---
class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user or user.is_bot:
            return await handler(event, data)
        if user.id != ADMIN_CHAT_ID:
            res = supabase.table("banned_users").select("user_id").eq("user_id", user.id).execute()
            if res.data: return
        return await handler(event, data)

# --- КЛАВИАТУРЫ ---
def main_kb():
    buttons = [[KeyboardButton(text=v)] for v in CATEGORIES.values()]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def project_inline_kb(p_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Оценить", callback_data=f"rev_{p_id}"),
         InlineKeyboardButton(text="❤️ Поддержать", callback_data=f"like_{p_id}")],
        [InlineKeyboardButton(text="💬 Посмотреть отзывы", callback_data=f"viewrev_{p_id}")]
    ])

# --- АДМИН-КОМАНДЫ (С ПРИОРИТЕТОМ) ---

@router.message(Command("add"))
async def admin_add(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_CHAT_ID: return
    await state.clear() # Сбрасываем любые состояния, чтобы команда сработала
    try:
        raw_text = message.text.split(maxsplit=1)[1]
        parts = [p.strip() for p in raw_text.split("|")]
        cat, name, desc = parts[0], parts[1], parts[2]
        supabase.table("projects").insert({"name": name, "category": cat, "description": desc}).execute()
        await message.answer(f"✅ Проект <b>{name}</b> добавлен.", parse_mode="HTML")
    except:
        await message.answer("❌ Формат: <code>/add категория | Название | Описание</code>", parse_mode="HTML")

@router.message(Command("del"))
async def admin_delete(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_CHAT_ID: return
    await state.clear()
    try:
        name = message.text.split(maxsplit=1)[1].strip()
        supabase.table("projects").delete().eq("name", name).execute()
        await message.answer(f"🗑 Проект <b>{name}</b> удален.", parse_mode="HTML")
    except:
        await message.answer("❌ Формат: <code>/del Название</code>")

@router.message(Command("score"))
async def admin_score(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_CHAT_ID: return
    await state.clear()
    try:
        raw_text = message.text.split(maxsplit=1)[1]
        parts = [p.strip() for p in raw_text.split("|")]
        name, val = parts[0], int(parts[1])
        res = supabase.table("projects").select("score").eq("name", name).single().execute().data
        new_score = res['score'] + val
        supabase.table("projects").update({"score": new_score}).eq("name", name).execute()
        await message.answer(f"⚖️ Рейтинг <b>{name}</b>: <code>{new_score}</code>", parse_mode="HTML")
    except:
        await message.answer("❌ Формат: <code>/score Название | 10</code>")

@router.message(Command("delrev"))
async def admin_del_review(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_CHAT_ID: return
    await state.clear()
    try:
        log_id = int(message.text.split()[1])
        rev = supabase.table("user_logs").select("*").eq("id", log_id).single().execute().data
        if rev:
            diff = RATING_MAP.get(rev['rating_val'], 0)
            p = supabase.table("projects").select("score").eq("id", rev['project_id']).single().execute().data
            supabase.table("projects").update({"score": p['score'] - diff}).eq("id", rev['project_id']).execute()
            supabase.table("user_logs").delete().eq("id", log_id).execute()
            await message.answer(f"🗑 Отзыв №{log_id} удален.", parse_mode="HTML")
    except: await message.answer("❌ Формат: <code>/delrev ID</code>")

@router.message(Command("ban"))
async def admin_ban(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_CHAT_ID: return
    await state.clear()
    try:
        uid = int(message.text.split()[1])
        supabase.table("banned_users").insert({"user_id": uid}).execute()
        await message.answer(f"🚫 Юзер <code>{uid}</code> забанен.", parse_mode="HTML")
    except: await message.answer("Пример: <code>/ban 12345</code>")

@router.message(Command("unban"))
async def admin_unban(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_CHAT_ID: return
    await state.clear()
    try:
        uid = int(message.text.split()[1])
        supabase.table("banned_users").delete().eq("user_id", uid).execute()
        await message.answer(f"✅ Юзер <code>{uid}</code> разблокирован.", parse_mode="HTML")
    except: await message.answer("Пример: <code>/unban 12345</code>")

# --- ЛОГИКА ПОЛЬЗОВАТЕЛЯ ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    top = supabase.table("projects").select("*").order("score", desc=True).limit(5).execute().data
    text = "<b>🏆 ТОП-5 ПРОЕКТОВ КМБП</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    if top:
        for i, p in enumerate(top, 1):
            text += f"{i}. <b>{p['name']}</b> — <code>{p['score']}</code>\n"
    else: text += "Проектов пока нет.\n"
    await message.answer(text, reply_markup=main_kb(), parse_mode="HTML")

@router.message(F.text.in_(CATEGORIES.values()))
async def show_cat(message: Message):
    cat_key = [k for k, v in CATEGORIES.items() if v == message.text][0]
    data = supabase.table("projects").select("*").eq("category", cat_key).order("score", desc=True).execute().data
    if not data: return await message.answer(f"В '{message.text}' пусто.")
    for p in data:
        card = f"<b>{p['name']}</b>\n\n{p['description']}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\nРейтинг: <b>{p['score']}</b>"
        await message.answer(card, reply_markup=project_inline_kb(p['id']), parse_mode="HTML")

@router.callback_query(F.data.startswith("viewrev_"))
async def view_reviews(call: CallbackQuery):
    p_id = call.data.split("_")[1]
    revs = supabase.table("user_logs").select("*").eq("project_id", p_id).eq("action_type", "review").order("created_at", desc=True).limit(5).execute().data
    if not revs: return await call.answer("Отзывов пока нет.", show_alert=True)
    text = "<b>💬 ПОСЛЕДНИЕ ОТЗЫВЫ:</b>\n\n"
    for r in revs:
        stars = "⭐" * r['rating_val']
        text += f"{stars}\n<i>{r['review_text']}</i>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    await call.message.answer(text, parse_mode="HTML"); await call.answer()

@router.callback_query(F.data.startswith("like_"))
async def handle_like(call: CallbackQuery):
    p_id = call.data.split("_")[1]
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "like").execute()
    if check.data: return await call.answer("Уже голосовали!", show_alert=True)
    res = supabase.table("projects").select("score").eq("id", p_id).single().execute().data
    supabase.table("projects").update({"score": res['score'] + 1}).eq("id", p_id).execute()
    supabase.table("user_logs").insert({"user_id": call.from_user.id, "project_id": p_id, "action_type": "like"}).execute()
    await call.answer("❤️ Голос принят!"); await call.message.edit_reply_markup(reply_markup=project_inline_kb(p_id))

@router.callback_query(F.data.startswith("rev_"))
async def rev_start(call: CallbackQuery, state: FSMContext):
    await state.update_data(p_id=call.data.split("_")[1])
    await state.set_state(ReviewState.waiting_for_text)
    await call.message.answer("💬 <b>Напишите текст отзыва:</b>", parse_mode="HTML"); await call.answer()

@router.message(ReviewState.waiting_for_text)
async def rev_text(message: Message, state: FSMContext):
    # Если это админ-команда, игнорируем ввод текста отзыва
    if message.text.startswith("/"): return
    await state.update_data(txt=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⭐"*i, callback_data=f"st_{i}")] for i in range(5, 0, -1)])
    await state.set_state(ReviewState.waiting_for_rate)
    await message.answer("🌟 <b>Ваша оценка:</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("st_"), ReviewState.waiting_for_rate)
async def rev_end(call: CallbackQuery, state: FSMContext):
    rate = int(call.data.split("_")[1])
    data = await state.get_data()
    p_id = data['p_id']
    new_diff = RATING_MAP[rate]
    
    # Ищем старый отзыв
    old_rev = supabase.table("user_logs").select("*").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "review").execute()
    p = supabase.table("projects").select("score", "name").eq("id", p_id).single().execute().data
    
    if old_rev.data:
        # Если отзыв уже был — вычитаем старые баллы
        old_val = old_rev.data[0]['rating_val']
        old_diff = RATING_MAP[old_val]
        current_score = p['score'] - old_diff + new_diff
        supabase.table("user_logs").update({"review_text": data['txt'], "rating_val": rate}).eq("id", old_rev.data[0]['id']).execute()
        msg_type = "обновлен"
    else:
        # Если новый — просто прибавляем
        current_score = p['score'] + new_diff
        log = supabase.table("user_logs").insert({"user_id": call.from_user.id, "project_id": p_id, "action_type": "review", "review_text": data['txt'], "rating_val": rate}).execute()
        msg_type = "опубликован"

    supabase.table("projects").update({"score": current_score}).eq("id", p_id).execute()
    await call.message.edit_text(f"✅ Ваш отзыв {msg_type}!", parse_mode="HTML")
    await bot.send_message(ADMIN_CHAT_ID, f"📢 <b>Отзыв {msg_type}:</b> {p['name']}\nТекст: {data['txt']}\nОценка: {rate}/5")
    await state.clear(); await call.answer()

async def main():
    logging.basicConfig(level=logging.INFO)
    dp.update.outer_middleware(AccessMiddleware())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
