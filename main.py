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

# --- НАСТРОЙКИ ---
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

# --- MIDDLEWARE (АНТИСПАМ И БАН) ---
class SecurityMiddleware(BaseMiddleware):
    def __init__(self):
        self.cd = {}

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user or user.is_bot:
            return await handler(event, data)

        # Проверка бана
        res = supabase.table("banned_users").select("user_id").eq("user_id", user.id).execute()
        if res.data:
            return

        # Антиспам (60 сек)
        chat = data.get("event_chat")
        if chat and chat.id != ADMIN_CHAT_ID:
            is_start = isinstance(event, Message) and event.text == "/start"
            if not is_start:
                now = time.time()
                if now - self.cd.get(user.id, 0) < 60:
                    wait = int(60 - (now - self.cd.get(user.id, 0)))
                    if isinstance(event, CallbackQuery):
                        await event.answer(f"Повтор через {wait} сек.", show_alert=True)
                    return
                self.cd[user.id] = now
        return await handler(event, data)

# --- КЛАВИАТУРЫ ---
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=v)] for v in CATEGORIES.values()],
        resize_keyboard=True
    )

def project_kb(p_id, is_admin=False):
    buttons = [
        [InlineKeyboardButton(text="Оценить проект", callback_data=f"rev_{p_id}"),
         InlineKeyboardButton(text="Поддержать", callback_data=f"like_{p_id}")]
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton(text="Удалить проект", callback_data=f"del_{p_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- ОБРАБОТКА ОШИБОК ---
@dp.error()
async def error_handler(event: ErrorEvent):
    logging.error(traceback.format_exc())
    try:
        await bot.send_message(ADMIN_CHAT_ID, f"<b>⚠️ Ошибка системы:</b>\n<code>{event.exception}</code>", parse_mode="HTML")
    except:
        pass

# --- АДМИН КОМАНДЫ ---
@router.message(Command("add"))
async def add_project(message: Message):
    if message.chat.id != ADMIN_CHAT_ID:
        return

    try:
        content = message.text.replace("/add", "").strip()
        parts = [p.strip() for p in content.split("|")]
        
        if len(parts) < 3:
            return await message.answer("<b>Формат:</b>\n<code>/add категория | Название | Описание</code>", parse_mode="HTML")
        
        cat, name, desc = parts[0], parts[1], parts[2]
        if cat not in CATEGORIES:
            return await message.answer(f"Категория не найдена. Список: <code>{', '.join(CATEGORIES.keys())}</code>", parse_mode="HTML")

        supabase.table("projects").insert({"name": name, "category": cat, "description": desc}).execute()
        await message.answer(f"✅ Проект <b>{name}</b> успешно добавлен.", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Ошибка при добавлении: {e}")

@router.callback_query(F.data.startswith("del_"))
async def delete_project(call: CallbackQuery):
    if call.message.chat.id != ADMIN_CHAT_ID:
        return await call.answer("Доступ запрещен", show_alert=True)
        
    p_id = call.data.split("_")[1]
    supabase.table("projects").delete().eq("id", p_id).execute()
    await call.message.edit_text("🗑 Проект удален из базы.")
    await call.answer()

# --- ЛОГИКА ПОЛЬЗОВАТЕЛЯ ---
@router.message(CommandStart())
async def cmd_start(message: Message):
    top = supabase.table("projects").select("*").order("score", desc=True).limit(5).execute().data
    text = "<b>РЕЙТИНГ ПРОЕКТОВ КМБП</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    if top:
        for i, p in enumerate(top, 1):
            text += f"{i}. <b>{p['name']}</b> — <code>{p['score']}</code> баллов\n"
    else:
        text += "Список проектов пуст.\n"
    
    text += "\nВыберите нужный раздел в меню:"
    await message.answer(text, reply_markup=main_kb(), parse_mode="HTML")

@router.message(F.text.in_(CATEGORIES.values()))
async def list_projects(message: Message):
    cat_key = [k for k, v in CATEGORIES.items() if v == message.text][0]
    data = supabase.table("projects").select("*").eq("category", cat_key).order("score", desc=True).execute().data
    
    if not data:
        return await message.answer(f"В разделе '{message.text}' пока нет участников.")

    await message.answer(f"<b>РАЗДЕЛ: {message.text.upper()}</b>", parse_mode="HTML")
    for p in data:
        card = (
            f"● <b>{p['name']}</b>\n\n"
            f"{p['description']}\n"
            f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f"Рейтинг: <code>{p['score']}</code>"
        )
        is_adm = (message.chat.id == ADMIN_CHAT_ID)
        await message.answer(card, reply_markup=project_kb(p['id'], is_adm), parse_mode="HTML")

@router.callback_query(F.data.startswith("like_"))
async def action_like(call: CallbackQuery):
    p_id = call.data.split("_")[1]
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "like").execute()
    
    if check.data:
        return await call.answer("Вы уже голосовали за этот проект.", show_alert=True)
        
    curr = supabase.table("projects").select("score").eq("id", p_id).single().execute().data
    new_score = curr['score'] + 1
    supabase.table("projects").update({"score": new_score}).eq("id", p_id).execute()
    supabase.table("user_logs").insert({"user_id": call.from_user.id, "project_id": p_id, "action_type": "like"}).execute()
    
    await call.answer(f"Голос принят! Текущий рейтинг: {new_score}")
    await call.message.edit_reply_markup(reply_markup=None)

@router.callback_query(F.data.startswith("rev_"))
async def action_review(call: CallbackQuery, state: FSMContext):
    await state.update_data(p_id=call.data.split("_")[1])
    await state.set_state(ReviewState.waiting_for_text)
    await call.message.answer("<b>Введите текст вашего отзыва:</b>", parse_mode="HTML")
    await call.answer()

@router.message(ReviewState.waiting_for_text)
async def process_rev_text(message: Message, state: FSMContext):
    await state.update_data(txt=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="★"*i, callback_data=f"st_{i}")] for i in range(5, 0, -1)
    ])
    await state.set_state(ReviewState.waiting_for_rate)
    await message.answer("<b>Оцените проект (1-5):</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("st_"), ReviewState.waiting_for_rate)
async def process_rev_rate(call: CallbackQuery, state: FSMContext):
    rate = int(call.data.split("_")[1])
    data = await state.get_data()
    diff = RATING_MAP[rate]
    
    p = supabase.table("projects").select("score", "name").eq("id", data['p_id']).single().execute().data
    new_total = p['score'] + diff
    
    supabase.table("projects").update({"score": new_total}).eq("id", data['p_id']).execute()
    supabase.table("user_logs").insert({
        "user_id": call.from_user.id, "project_id": data['p_id'],
        "action_type": "review", "review_text": data['txt'], "rating_val": rate
    }).execute()
    
    await bot.send_message(ADMIN_CHAT_ID, f"📢 <b>Новый отзыв!</b>\nПроект: {p['name']}\nОценка: {rate}/5\nТекст: {data['txt']}", parse_mode="HTML")
    
    await call.message.edit_text(f"✅ Отзыв опубликован. Новый рейтинг проекта: <b>{new_total}</b>", parse_mode="HTML")
    await state.clear()
    await call.answer()

# --- ЗАПУСК ---
async def main():
    dp.update.outer_middleware(SecurityMiddleware())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
