import asyncio
import os
import time
import logging
import traceback
from typing import Dict, Any, Union

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, TelegramObject, ErrorEvent
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from supabase import create_client, Client

# --- КОНФИГУРАЦИЯ ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", 0))

CATEGORIES = {
    "support_bots": "Боты поддержки",
    "support_admins": "Админы поддержки",
    "lot_channels": "Каналы лотов",
    "check_channels": "Каналы проверок",
    "kmbp_channels": "Каналы КМБП"
}

RATING_MAP = {1: -5, 2: -2, 3: 0, 4: 2, 5: 5}

# Инициализация
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

# --- СОСТОЯНИЯ ---
class ReviewState(StatesGroup):
    waiting_for_text = State()
    waiting_for_rate = State()

# --- MIDDLEWARE (БЕЗОПАСНОСТЬ И АНТИСПАМ) ---
class SecurityMiddleware(BaseMiddleware):
    def __init__(self):
        self.users_history = {}

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user or user.is_bot:
            return await handler(event, data)

        # 1. Проверка бана
        res = supabase.table("banned_users").select("user_id").eq("user_id", user.id).execute()
        if res.data:
            return

        # 2. Антиспам (кроме админа и команды /start)
        chat = data.get("event_chat")
        if chat and chat.id != ADMIN_CHAT_ID:
            is_start = isinstance(event, Message) and event.text == "/start"
            if not is_start:
                now = time.time()
                last_time = self.users_history.get(user.id, 0)
                if now - last_time < 60:
                    wait = int(60 - (now - last_time))
                    if isinstance(event, CallbackQuery):
                        await event.answer(f"Пауза! Еще {wait} сек.", show_alert=True)
                    elif isinstance(event, Message):
                        await event.answer(f"⏳ Режим защиты от спама. Подождите {wait} сек.")
                    return
                self.users_history[user.id] = now

        return await handler(event, data)

# --- КЛАВИАТУРЫ ---
def get_main_kb():
    buttons = [[KeyboardButton(text=v)] for v in CATEGORIES.values()]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_project_kb(p_id, is_admin=False):
    kb = [
        [InlineKeyboardButton(text="👍 Лайк", callback_data=f"like_{p_id}"),
         InlineKeyboardButton(text="✍️ Отзыв", callback_data=f"rev_{p_id}")]
    ]
    if is_admin:
        kb.append([InlineKeyboardButton(text="⚙️ Управление (Админ)", callback_data=f"manage_{p_id}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- ОБРАБОТЧИКИ ОШИБОК ---
@dp.error()
async def error_handler(event: ErrorEvent):
    logging.error(f"Ошибка: {event.exception}\n{traceback.format_exc()}")
    await bot.send_message(ADMIN_CHAT_ID, f"⚠️ **Ошибка системы:**\n`{event.exception}`", parse_mode="Markdown")

# --- АДМИН-КОМАНДЫ ---
@router.message(F.chat.id == ADMIN_CHAT_ID, Command("add"))
async def add_project_cmd(message: Message):
    # Пример: /add support_bots | Название | Описание
    try:
        content = message.text.replace("/add", "").strip()
        parts = [i.strip() for i in content.split("|")]
        
        if len(parts) < 3:
            return await message.answer("ℹ️ **Формат:** `/add категория | Название | Описание`", parse_mode="Markdown")
        
        cat, name, desc = parts[0], parts[1], parts[2]
        if cat not in CATEGORIES:
            return await message.answer(f"❌ Ошибка категории. Список: `{', '.join(CATEGORIES.keys())}`", parse_mode="Markdown")

        supabase.table("projects").insert({"name": name, "category": cat, "description": desc}).execute()
        await message.answer(f"✅ Проект **{name}** успешно добавлен.", parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Ошибка при добавлении: {e}")

@router.callback_query(F.data.startswith("manage_"), F.from_user.id == (lambda: True)) # Упрощенная проверка для теста
async def manage_project(call: CallbackQuery):
    p_id = call.data.split("_")[1]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить проект", callback_data=f"del_{p_id}")],
        [InlineKeyboardButton(text="➕ Начислить 10", callback_data=f"mod_{p_id}_10"),
         InlineKeyboardButton(text="➖ Списать 10", callback_data=f"mod_{p_id}_-10")]
    ])
    await call.message.answer(f"🛠 Управление проектом ID: {p_id}", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("mod_"))
async def mod_score(call: CallbackQuery):
    _, p_id, val = call.data.split("_")
    curr = supabase.table("projects").select("score").eq("id", p_id).single().execute().data
    new_score = curr['score'] + int(val)
    supabase.table("projects").update({"score": new_score}).eq("id", p_id).execute()
    await call.message.edit_text(f"⚖️ Рейтинг обновлен: **{new_score}**", parse_mode="Markdown")

@router.callback_query(F.data.startswith("del_"))
async def delete_project(call: CallbackQuery):
    p_id = call.data.split("_")[1]
    supabase.table("projects").delete().eq("id", p_id).execute()
    await call.message.edit_text("🗑 Проект удален.")

# --- ЛОГИКА ПОЛЬЗОВАТЕЛЯ ---
@router.message(CommandStart())
async def cmd_start(message: Message):
    top = supabase.table("projects").select("*").order("score", desc=True).limit(5).execute().data
    text = "🏆 **ЛИДЕРЫ РЕЙТИНГА КМБП**\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    if top:
        for i, p in enumerate(top, 1):
            text += f"{i}. {p['name']} — `{p['score']}`\n"
    else:
        text += "Список проектов пуст.\n"
    
    text += "\nВыберите категорию в меню ниже:"
    await message.answer(text, reply_markup=get_main_kb(), parse_mode="Markdown")

@router.message(F.text.in_(CATEGORIES.values()))
async def show_category(message: Message):
    cat_key = [k for k, v in CATEGORIES.items() if v == message.text][0]
    projects = supabase.table("projects").select("*").eq("category", cat_key).order("score", desc=True).execute().data
    
    if not projects:
        return await message.answer(f"📍 В категории '{message.text}' пока нет участников.")

    await message.answer(f"📋 **{message.text.upper()}**")
    for p in projects:
        info = (
            f"🔹 **{p['name']}**\n\n"
            f"{p['description']}\n\n"
            f"⭐ Рейтинг: `{p['score']}`"
        )
        is_admin = (message.chat.id == ADMIN_CHAT_ID)
        await message.answer(info, reply_markup=get_project_kb(p['id'], is_admin), parse_mode="Markdown")

@router.callback_query(F.data.startswith("like_"))
async def handle_like(call: CallbackQuery):
    p_id = int(call.data.split("_")[1])
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "like").execute()
    
    if check.data:
        return await call.answer("Вы уже голосовали за этот проект.", show_alert=True)
    
    res = supabase.table("projects").select("score").eq("id", p_id).single().execute().data
    supabase.table("projects").update({"score": res['score'] + 1}).eq("id", p_id).execute()
    supabase.table("user_logs").insert({"user_id": call.from_user.id, "project_id": p_id, "action_type": "like"}).execute()
    
    await call.answer("Голос учтен!")
    await call.message.edit_reply_markup(reply_markup=None)

@router.callback_query(F.data.startswith("rev_"))
async def review_start(call: CallbackQuery, state: FSMContext):
    p_id = call.data.split("_")[1]
    await state.update_data(p_id=p_id)
    await state.set_state(ReviewState.waiting_for_text)
    await call.message.answer("💬 Пожалуйста, напишите ваш отзыв:")
    await call.answer()

@router.message(ReviewState.waiting_for_text)
async def review_text(message: Message, state: FSMContext):
    await state.update_data(txt=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐"*i, callback_data=f"rate_{i}")] for i in range(5, 0, -1)
    ])
    await state.set_state(ReviewState.waiting_for_rate)
    await message.answer("Выберите вашу оценку:", reply_markup=kb)

@router.callback_query(F.data.startswith("rate_"), ReviewState.waiting_for_rate)
async def review_done(call: CallbackQuery, state: FSMContext):
    rate = int(call.data.split("_")[1])
    data = await state.get_data()
    diff = RATING_MAP[rate]
    
    p_data = supabase.table("projects").select("score", "name").eq("id", data['p_id']).single().execute().data
    supabase.table("projects").update({"score": p_data['score'] + diff}).eq("id", data['p_id']).execute()
    
    supabase.table("user_logs").insert({
        "user_id": call.from_user.id, "project_id": data['p_id'],
        "action_type": "review", "review_text": data['txt'], "rating_val": rate
    }).execute()
    
    # Уведомление админу
    await bot.send_message(ADMIN_CHAT_ID, f"📢 **Новый отзыв!**\nПроект: {p_data['name']}\nОценка: {rate}/5\nТекст: {data['txt']}")
    
    await call.message.edit_text(f"✅ Спасибо! Отзыв принят. Текущий рейтинг: {p_data['score'] + diff}")
    await state.clear()

# --- ЗАПУСК ---
async def main():
    logging.basicConfig(level=logging.INFO)
    dp.update.outer_middleware(SecurityMiddleware())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
