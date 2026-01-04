import asyncio
import os
import time
import logging
from typing import Dict, Any, Callable, Awaitable
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, TelegramObject
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from supabase import create_client, Client

# --- КОНФИГУРАЦИЯ ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN") # Токен из .env
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", 0))

CATEGORIES = {
    "support_bots": "🤖 Рейтинг ботов поддержки",
    "support_admins": "👨‍💻 Рейтинг админов поддержки",
    "lot_channels": "📦 Каналы с лотами",
    "check_channels": "✅ Каналы с проверками",
    "kmbp_channels": "🛡 Каналы КМБП"
}

# Баллы за оценки 1-5
RATING_MAP = {1: -5, 2: -2, 3: 0, 4: 2, 5: 5}

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

# --- MIDDLEWARE (АНТИСПАМ 60 СЕК И БАН) ---
class SecurityMiddleware(BaseMiddleware):
    def __init__(self):
        self.users_history = {} # {user_id: last_action_time}

    async def __call__(self, handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: Dict[str, Any]) -> Any:
        # Проверяем, является ли событие сообщением или колбэком
        user = data.get("event_from_user")
        if not user or user.is_bot:
            return await handler(event, data)

        # Достаем chat_id правильно
        current_chat_id = None
        if data.get("event_chat"):
            current_chat_id = data["event_chat"].id

        # 1. Проверка на БАН
        is_banned = supabase.table("banned_users").select("user_id").eq("user_id", user.id).execute()
        if is_banned.data:
            return

        # 2. АНТИСПАМ
        # Если это админ в своем чате — разрешаем без задержек
        if current_chat_id == ADMIN_CHAT_ID:
            return await handler(event, data)

        now = time.time()
        last_action = self.users_history.get(user.id, 0)

        # Ограничение только для нажатий кнопок и текстовых команд
        if now - last_action < 60:
            remains = int(60 - (now - last_action))
            # Если это нажатие инлайн-кнопки
            if isinstance(event, CallbackQuery):
                await event.answer(f"⏳ Подождите {remains} сек!", show_alert=True)
            # Если это обычное сообщение (кнопка меню)
            elif isinstance(event, Message):
                await event.answer(f"⚠️ Слишком часто! Кнопки будут доступны через {remains} сек.")
            return

        self.users_history[user.id] = now
        return await handler(event, data)
                           
# --- FSM ---
class ReviewState(StatesGroup):
    waiting_for_text = State()
    waiting_for_rate = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def find_project(target: str):
    """Поиск по ID или по Названию"""
    if target.isdigit():
        res = supabase.table("projects").select("*").eq("id", int(target)).execute()
    else:
        res = supabase.table("projects").select("*").ilike("name", target).execute()
    return res.data[0] if res.data else None

def update_score(p_id, amount):
    curr = supabase.table("projects").select("score").eq("id", p_id).single().execute()
    new_score = curr.data['score'] + amount
    supabase.table("projects").update({"score": new_score}).eq("id", p_id).execute()
    return new_score

# --- КЛАВИАТУРЫ ---
def main_kb():
    keys = [[KeyboardButton(text=v)] for v in CATEGORIES.values()]
    keys.append([KeyboardButton(text="⭐ Мои отзывы")])
    return ReplyKeyboardMarkup(keyboard=keys, resize_keyboard=True)

def project_inline(p_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👍 +1 Репутация", callback_data=f"like_{p_id}"),
        InlineKeyboardButton(text="✍️ Написать отзыв", callback_data=f"rev_{p_id}")
    ]])

# --- АДМИН КОМАНДЫ ---
@router.message(F.chat.id == ADMIN_CHAT_ID)
async def admin_panel(message: Message):
    if not message.text: return
    args = message.text.split()
    
    # /add [категория] [Имя] [Описание]
    if args[0] == "/add" and len(args) >= 3:
        cat, name = args[1], args[2]
        desc = " ".join(args[3:]) if len(args) > 3 else "Нет описания"
        res = supabase.table("projects").insert({"name": name, "category": cat, "description": desc}).execute()
        await message.reply(f"✅ Добавлен: {name} (ID: {res.data[0]['id']})")

    # /mod [Имя или ID] [+/-Баллы]
    elif args[0] == "/mod" and len(args) == 3:
        p = find_project(args[1])
        if p:
            new_s = update_score(p['id'], int(args[2]))
            await message.reply(f"⚙️ {p['name']}: {new_s} (изменение {args[2]})")
        else: await message.reply("❌ Проект не найден")

    # /del [Имя или ID]
    elif args[0] == "/del" and len(args) == 2:
        p = find_project(args[1])
        if p:
            supabase.table("projects").delete().eq("id", p['id']).execute()
            await message.reply(f"🗑 Удалено: {p['name']}")

# --- ЛОГИКА ПОЛЬЗОВАТЕЛЕЙ ---
@router.message(CommandStart())
async def start(message: Message):
    top_10 = supabase.table("projects").select("*").order("score", desc=True).limit(10).execute().data
    text = "📊 **ОБЩИЙ ТОП-10 ПРОЕКТОВ**\n\n"
    for i, p in enumerate(top_10, 1):
        text += f"{i}. {p['name']} — `{p['score']}` баллов\n"
    await message.answer(text, reply_markup=main_kb(), parse_mode="Markdown")

@router.message(F.text.in_(CATEGORIES.values()))
async def show_category(message: Message):
    cat_key = [k for k, v in CATEGORIES.items() if v == message.text][0]
    projects = supabase.table("projects").select("*").eq("category", cat_key).order("score", desc=True).execute().data
    
    if not projects:
        return await message.answer("В этой категории пока пусто.")
    
    for p in projects:
        await message.answer(
            f"🔹 **{p['name']}**\n{p['description']}\n🏆 Рейтинг: `{p['score']}`",
            reply_markup=project_inline(p['id']), parse_mode="Markdown"
        )

@router.callback_query(F.data.startswith("like_"))
async def handle_like(call: CallbackQuery):
    p_id = int(call.data.split("_")[1])
    # Проверка на дубликат
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "like").execute()
    if check.data:
        return await call.answer("❌ Вы уже ставили лайк этому проекту!", show_alert=True)
    
    update_score(p_id, 1)
    supabase.table("user_logs").insert({"user_id": call.from_user.id, "project_id": p_id, "action_type": "like"}).execute()
    await call.answer("✅ Репутация повышена!")

@router.callback_query(F.data.startswith("rev_"))
async def review_start(call: CallbackQuery, state: FSMContext):
    p_id = int(call.data.split("_")[1])
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "review").execute()
    if check.data:
        return await call.answer("❌ Вы уже писали отзыв!", show_alert=True)
    
    await state.update_data(p_id=p_id)
    await state.set_state(ReviewState.waiting_for_text)
    await call.message.answer("📝 Напишите ваш отзыв о проекте:")
    await call.answer()

@router.message(ReviewState.waiting_for_text)
async def review_text(message: Message, state: FSMContext):
    await state.update_data(txt=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{i} ⭐", callback_data=f"rate_{i}")] for i in range(1, 6)
    ])
    await state.set_state(ReviewState.waiting_for_rate)
    await message.answer("Выберите оценку (1-5):", reply_markup=kb)

@router.callback_query(F.data.startswith("rate_"))
async def review_finish(call: CallbackQuery, state: FSMContext):
    rate = int(call.data.split("_")[1])
    data = await state.get_data()
    p_id, txt = data['p_id'], data['txt']
    
    score_change = RATING_MAP[rate]
    update_score(p_id, score_change)
    
    supabase.table("user_logs").insert({
        "user_id": call.from_user.id, "project_id": p_id, 
        "action_type": "review", "review_text": txt, "rating_val": rate
    }).execute()
    
    await call.message.edit_text(f"✅ Отзыв принят! Влияние на рейтинг: {score_change:+}")
    await state.clear()

async def main():
    logging.basicConfig(level=logging.INFO)
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
