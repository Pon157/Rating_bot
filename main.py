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

# Влияние звезд на рейтинг
RATING_MAP = {1: -5, 2: -2, 3: 0, 4: 2, 5: 5}

class ReviewState(StatesGroup):
    waiting_for_text = State()
    waiting_for_rate = State()

# --- MIDDLEWARE (ТОЛЬКО ПРОВЕРКА БАНА) ---
class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user or user.is_bot:
            return await handler(event, data)

        # Проверка бана в таблице banned_users
        res = supabase.table("banned_users").select("user_id").eq("user_id", user.id).execute()
        if res.data:
            return  # Игнорируем забаненного

        return await handler(event, data)

# --- КЛАВИАТУРЫ ---
def main_kb():
    buttons = [[KeyboardButton(text=v)] for v in CATEGORIES.values()]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def project_inline_kb(p_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Оценить", callback_data=f"rev_{p_id}"),
         InlineKeyboardButton(text="❤️ Поддержать", callback_data=f"like_{p_id}")]
    ])

# --- ОБРАБОТЧИК ОШИБОК ---
@dp.error()
async def error_handler(event: ErrorEvent):
    logging.error(traceback.format_exc())
    try:
        await bot.send_message(ADMIN_CHAT_ID, f"⚠️ <b>Ошибка:</b>\n<code>{event.exception}</code>", parse_mode="HTML")
    except:
        pass

# --- АДМИН-КОМАНДЫ (УПРАВЛЕНИЕ) ---

@router.message(Command("add"), F.from_user.id == ADMIN_CHAT_ID)
async def admin_add(message: Message):
    try:
        # Формат: /add категория | Название | Описание
        parts = [p.strip() for p in message.text.replace("/add", "").split("|")]
        cat, name, desc = parts[0], parts[1], parts[2]
        supabase.table("projects").insert({"name": name, "category": cat, "description": desc}).execute()
        await message.answer(f"✅ Проект <b>{name}</b> успешно добавлен в категорию <i>{cat}</i>", parse_mode="HTML")
    except:
        await message.answer("❌ <b>Ошибка формата!</b>\nИспользуй: <code>/add категория | Название | Описание</code>", parse_mode="HTML")

@router.message(Command("del"), F.from_user.id == ADMIN_CHAT_ID)
async def admin_delete(message: Message):
    name = message.text.replace("/del", "").strip()
    if name:
        supabase.table("projects").delete().eq("name", name).execute()
        await message.answer(f"🗑 Проект <b>{name}</b> удален из базы.", parse_mode="HTML")

@router.message(Command("score"), F.from_user.id == ADMIN_CHAT_ID)
async def admin_score(message: Message):
    try:
        parts = [p.strip() for p in message.text.replace("/score", "").split("|")]
        name, val = parts[0], int(parts[1])
        res = supabase.table("projects").select("score").eq("name", name).single().execute().data
        new_score = res['score'] + val
        supabase.table("projects").update({"score": new_score}).eq("name", name).execute()
        await message.answer(f"⚖️ Рейтинг проекта <b>{name}</b> обновлен: <code>{new_score}</code>", parse_mode="HTML")
    except:
        await message.answer("❌ <b>Ошибка!</b>\nПример: <code>/score @название | 10</code>", parse_mode="HTML")

@router.message(Command("ban"), F.from_user.id == ADMIN_CHAT_ID)
async def admin_ban(message: Message):
    try:
        uid = int(message.text.split()[1])
        supabase.table("banned_users").insert({"user_id": uid}).execute()
        await message.answer(f"🚫 Пользователь <code>{uid}</code> заблокирован.", parse_mode="HTML")
    except:
        await message.answer("Формат: <code>/ban ID</code>")

@router.message(Command("unban"), F.from_user.id == ADMIN_CHAT_ID)
async def admin_unban(message: Message):
    try:
        uid = int(message.text.split()[1])
        supabase.table("banned_users").delete().eq("user_id", uid).execute()
        await message.answer(f"✅ Пользователь <code>{uid}</code> разблокирован.", parse_mode="HTML")
    except:
        await message.answer("Формат: <code>/unban ID</code>")

# --- ЛОГИКА ПОЛЬЗОВАТЕЛЯ ---

@router.message(CommandStart())
async def cmd_start(message: Message):
    top = supabase.table("projects").select("*").order("score", desc=True).limit(5).execute().data
    text = "<b>🏆 РЕЙТИНГ ПРОЕКТОВ КМБП</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    if top:
        for i, p in enumerate(top, 1):
            text += f"{i}. <b>{p['name']}</b> — <code>{p['score']}</code>\n"
    else:
        text += "Проектов пока нет.\n"
    text += "\nВыберите нужный раздел в меню ниже 👇"
    await message.answer(text, reply_markup=main_kb(), parse_mode="HTML")

@router.message(F.text.in_(CATEGORIES.values()))
async def show_category_content(message: Message):
    cat_key = [k for k, v in CATEGORIES.items() if v == message.text][0]
    data = supabase.table("projects").select("*").eq("category", cat_key).order("score", desc=True).execute().data
    
    if not data:
        return await message.answer(f"В категории '{message.text}' пока никого нет.")
    
    await message.answer(f"💠 <b>{message.text.upper()}</b>", parse_mode="HTML")
    for p in data:
        card = (
            f"<b>{p['name']}</b>\n\n"
            f"{p['description']}\n"
            f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f"Текущий рейтинг: <b>{p['score']}</b>"
        )
        await message.answer(card, reply_markup=project_inline_kb(p['id']), parse_mode="HTML")

@router.callback_query(F.data.startswith("like_"))
async def handle_like(call: CallbackQuery):
    p_id = call.data.split("_")[1]
    # Проверка: лайкал ли уже этот юзер этот проект
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "like").execute()
    
    if check.data:
        return await call.answer("❌ Вы уже поддерживали этот проект!", show_alert=True)
    
    # Обновляем баллы
    res = supabase.table("projects").select("score").eq("id", p_id).single().execute().data
    supabase.table("projects").update({"score": res['score'] + 1}).eq("id", p_id).execute()
    # Логируем действие
    supabase.table("user_logs").insert({"user_id": call.from_user.id, "project_id": p_id, "action_type": "like"}).execute()
    
    await call.answer("❤️ Голос принят!")
    await call.message.edit_reply_markup(reply_markup=None)

@router.callback_query(F.data.startswith("rev_"))
async def review_start(call: CallbackQuery, state: FSMContext):
    await state.update_data(p_id=call.data.split("_")[1])
    await state.set_state(ReviewState.waiting_for_text)
    await call.message.answer("💬 <b>Напишите ваш отзыв:</b>", parse_mode="HTML")
    await call.answer()

@router.message(ReviewState.waiting_for_text)
async def review_get_text(message: Message, state: FSMContext):
    await state.update_data(txt=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐"*i, callback_data=f"st_{i}")] for i in range(5, 0, -1)
    ])
    await state.set_state(ReviewState.waiting_for_rate)
    await message.answer("🌟 <b>Ваша оценка проекту:</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("st_"), ReviewState.waiting_for_rate)
async def review_finish(call: CallbackQuery, state: FSMContext):
    rate = int(call.data.split("_")[1])
    data = await state.get_data()
    diff = RATING_MAP[rate]
    
    p = supabase.table("projects").select("score", "name").eq("id", data['p_id']).single().execute().data
    new_score = p['score'] + diff
    
    # Сохраняем в логи и обновляем проект
    supabase.table("user_logs").insert({
        "user_id": call.from_user.id, "project_id": data['p_id'],
        "action_type": "review", "review_text": data['txt'], "rating_val": rate
    }).execute()
    supabase.table("projects").update({"score": new_score}).eq("id", data['p_id']).execute()
    
    # Отправка модерации админу
    await bot.send_message(
        ADMIN_CHAT_ID, 
        f"📢 <b>Новый отзыв</b>\nПроект: {p['name']}\nОценка: {rate}/5 ({diff:+})\nТекст: <i>{data['txt']}</i>", 
        parse_mode="HTML"
    )
    
    await call.message.edit_text(f"✅ Отзыв опубликован! Рейтинг: <b>{new_score}</b>", parse_mode="HTML")
    await state.clear()
    await call.answer()

# --- ЗАПУСК ---
async def main():
    logging.basicConfig(level=logging.INFO)
    dp.update.outer_middleware(AccessMiddleware())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
