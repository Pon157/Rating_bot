import asyncio
import os
import time
import logging
import traceback
from typing import Dict, Any

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
# Токен берется из .env файла
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

# Мапа влияния оценки на рейтинг
RATING_MAP = {1: -5, 2: -2, 3: 0, 4: 2, 5: 5}

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

# --- СОСТОЯНИЯ (FSM) ---
class ReviewState(StatesGroup):
    waiting_for_text = State()
    waiting_for_rate = State()

# --- MIDDLEWARE (БЕЗОПАСНОСТЬ) ---
class SecurityMiddleware(BaseMiddleware):
    def __init__(self):
        self.cd = {}

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user or user.is_bot: return await handler(event, data)
        
        # Проверка на бан в Supabase
        res = supabase.table("banned_users").select("user_id").eq("user_id", user.id).execute()
        if res.data: return

        # Антиспам 60 секунд (не касается админ-чата)
        chat = data.get("event_chat")
        if chat and chat.id != ADMIN_CHAT_ID:
            if not (isinstance(event, Message) and event.text == "/start"):
                now = time.time()
                if now - self.cd.get(user.id, 0) < 60:
                    if isinstance(event, CallbackQuery):
                        await event.answer("⏳ Пауза 60 сек.", show_alert=True)
                    return
                self.cd[user.id] = now
        return await handler(event, data)

# --- КЛАВИАТУРЫ ---
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=v)] for v in CATEGORIES.values()],
        resize_keyboard=True
    )

def user_project_kb(p_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Оценить", callback_data=f"rev_{p_id}"),
         InlineKeyboardButton(text="❤️ Поддержать", callback_data=f"like_{p_id}")]
    ])

# --- АДМИН-КОМАНДЫ (УПРАВЛЕНИЕ) ---

@router.message(Command("add"), F.chat.id == ADMIN_CHAT_ID)
async def admin_add(message: Message):
    """Добавление: /add категория | Название | Описание"""
    try:
        content = message.text.replace("/add", "").strip()
        cat, name, desc = [p.strip() for p in content.split("|")]
        supabase.table("projects").insert({"name": name, "category": cat, "description": desc}).execute()
        await message.answer(f"✅ Проект <b>{name}</b> успешно добавлен.", parse_mode="HTML")
    except:
        await message.answer("❌ Ошибка. Формат: <code>/add категория | Название | Описание</code>", parse_mode="HTML")

@router.message(Command("del"), F.chat.id == ADMIN_CHAT_ID)
async def admin_delete(message: Message):
    """Удаление по названию: /del Название"""
    name = message.text.replace("/del", "").strip()
    supabase.table("projects").delete().eq("name", name).execute()
    await message.answer(f"🗑 Проект <b>{name}</b> удален.", parse_mode="HTML")

@router.message(Command("score"), F.chat.id == ADMIN_CHAT_ID)
async def admin_score(message: Message):
    """Изменение баллов: /score Название | число"""
    try:
        content = message.text.replace("/score", "").strip()
        name, val = [p.strip() for p in content.split("|")]
        curr = supabase.table("projects").select("score").eq("name", name).single().execute().data
        new_s = curr['score'] + int(val)
        supabase.table("projects").update({"score": new_s}).eq("name", name).execute()
        await message.answer(f"⚖️ Рейтинг <b>{name}</b> обновлен: <code>{new_s}</code>", parse_mode="HTML")
    except:
        await message.answer("❌ Формат: <code>/score Название | +/-баллы</code>", parse_mode="HTML")

@router.message(Command("ban"), F.chat.id == ADMIN_CHAT_ID)
async def admin_ban(message: Message):
    """Бан юзера по ID: /ban ID"""
    try:
        uid = int(message.text.split()[1])
        supabase.table("banned_users").insert({"user_id": uid}).execute()
        await message.answer(f"🚫 Юзер <code>{uid}</code> заблокирован.", parse_mode="HTML")
    except:
        await message.answer("❌ Формат: <code>/ban ID</code>", parse_mode="HTML")

# --- ПОЛЬЗОВАТЕЛЬСКАЯ ЛОГИКА ---

@router.message(CommandStart())
async def cmd_start(message: Message):
    top = supabase.table("projects").select("*").order("score", desc=True).limit(5).execute().data
    text = "<b>🏆 ЛИДЕРЫ РЕЙТИНГА КМБП</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    if top:
        for i, p in enumerate(top, 1):
            text += f"{i}. <b>{p['name']}</b> — <code>{p['score']}</code>\n"
    else:
        text += "Список проектов пуст.\n"
    text += "\nВыберите категорию для просмотра:"
    await message.answer(text, reply_markup=main_kb(), parse_mode="HTML")

@router.message(F.text.in_(CATEGORIES.values()))
async def list_cat_projects(message: Message):
    cat_key = [k for k, v in CATEGORIES.items() if v == message.text][0]
    data = supabase.table("projects").select("*").eq("category", cat_key).order("score", desc=True).execute().data
    
    if not data:
        return await message.answer(f"В разделе '{message.text}' пока пусто.")

    await message.answer(f"💠 <b>{message.text.upper()}</b>", parse_mode="HTML")
    for p in data:
        card = (
            f"<b>{p['name']}</b>\n\n"
            f"{p['description']}\n"
            f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f"Рейтинг: <b>{p['score']}</b>"
        )
        await message.answer(card, reply_markup=user_project_kb(p['id']), parse_mode="HTML")

@router.callback_query(F.data.startswith("like_"))
async def handle_like(call: CallbackQuery):
    p_id = call.data.split("_")[1]
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "like").execute()
    if check.data: 
        return await call.answer("Вы уже поддержали этот проект!", show_alert=True)
    
    curr = supabase.table("projects").select("score").eq("id", p_id).single().execute().data
    supabase.table("projects").update({"score": curr['score'] + 1}).eq("id", p_id).execute()
    supabase.table("user_logs").insert({"user_id": call.from_user.id, "project_id": p_id, "action_type": "like"}).execute()
    await call.answer("❤️ Голос учтен!")
    await call.message.edit_reply_markup(reply_markup=None)

@router.callback_query(F.data.startswith("rev_"))
async def start_review(call: CallbackQuery, state: FSMContext):
    await state.update_data(p_id=call.data.split("_")[1])
    await state.set_state(ReviewState.waiting_for_text)
    await call.message.answer("✍️ <b>Напишите ваш отзыв:</b>", parse_mode="HTML")
    await call.answer()

@router.message(ReviewState.waiting_for_text)
async def get_rev_text(message: Message, state: FSMContext):
    await state.update_data(txt=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐"*i, callback_data=f"st_{i}")] for i in range(5, 0, -1)
    ])
    await state.set_state(ReviewState.waiting_for_rate)
    await message.answer("🌟 <b>Поставьте оценку:</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("st_"), ReviewState.waiting_for_rate)
async def finish_review(call: CallbackQuery, state: FSMContext):
    rate = int(call.data.split("_")[1])
    data = await state.get_data()
    diff = RATING_MAP[rate]
    
    p = supabase.table("projects").select("score", "name").eq("id", data['p_id']).single().execute().data
    new_s = p['score'] + diff
    
    supabase.table("user_logs").insert({
        "user_id": call.from_user.id, "project_id": data['p_id'],
        "action_type": "review", "review_text": data['txt'], "rating_val": rate
    }).execute()
    supabase.table("projects").update({"score": new_s}).eq("id", data['p_id']).execute()
    
    # Уведомление в админ-чат
    admin_msg = (
        f"📢 <b>НОВЫЙ ОТЗЫВ</b>\n"
        f"Проект: {p['name']}\n"
        f"Оценка: {rate}/5 ({diff:+})\n"
        f"Юзер ID: <code>{call.from_user.id}</code>\n"
        f"Текст: <i>{data['txt']}</i>"
    )
    await bot.send_message(ADMIN_CHAT_ID, admin_msg, parse_mode="HTML")
    
    await call.message.edit_text(f"✅ Отзыв принят! Рейтинг обновлен: <b>{new_s}</b>", parse_mode="HTML")
    await state.clear()
    await call.answer()

# --- СИСТЕМНЫЙ ЗАПУСК ---
async def main():
    logging.basicConfig(level=logging.INFO)
    dp.update.outer_middleware(SecurityMiddleware())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
