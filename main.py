import asyncio
import os
import time
import logging
import traceback
from datetime import datetime
from typing import Dict, Any, Union

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, TelegramObject, ErrorEvent
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from supabase import create_client, Client

# --- CONFIG ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", 0))

CATEGORIES = {
    "support_bots": "🤖 Боты поддержки",
    "support_admins": "👨‍💻 Админы поддержки",
    "lot_channels": "📦 Каналы лотов",
    "check_channels": "✅ Каналы проверок",
    "kmbp_channels": "🛡 Каналы КМБП"
}

RATING_MAP = {1: -5, 2: -2, 3: 0, 4: 2, 5: 5}

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

# --- ERROR LOGGING TO TG ---
@dp.error()
async def error_handler(event: ErrorEvent):
    err_text = f"🚨 **System Error**\n\n`{event.exception}`\n\n**Traceback:**\n`{traceback.format_exc()[-500:]}`"
    await bot.send_message(ADMIN_CHAT_ID, err_text, parse_mode="Markdown")

# --- MIDDLEWARE (FIXED) ---
class SecurityMiddleware(BaseMiddleware):
    def __init__(self):
        self.cd = {}

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user or user.is_bot: return await handler(event, data)

        # Ban check
        res = supabase.table("banned_users").select("user_id").eq("user_id", user.id).execute()
        if res.data: return

        # Anti-spam (60s)
        chat = data.get("event_chat")
        if chat and chat.id != ADMIN_CHAT_ID:
            is_cmd = isinstance(event, Message) and event.text and event.text.startswith("/")
            if not is_cmd:
                now = time.time()
                if now - self.cd.get(user.id, 0) < 60:
                    wait = int(60 - (now - self.cd.get(user.id, 0)))
                    if isinstance(event, CallbackQuery):
                        await event.answer(f"⏳ Кулдаун: {wait} сек.", show_alert=True)
                    else:
                        await event.answer(f"⏳ **Пауза!** Подождите {wait} сек.")
                    return
                self.cd[user.id] = now
        return await handler(event, data)

dp.update.outer_middleware(SecurityMiddleware())

# --- STATES ---
class ReviewState(StatesGroup):
    waiting_for_text = State()
    waiting_for_rate = State()

class AdminState(StatesGroup):
    adding_project = State()

# --- KEYBOARDS ---
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=v)] for v in CATEGORIES.values()],
        resize_keyboard=True, input_field_placeholder="Выберите категорию..."
    )

def project_card_kb(p_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 Лайк (+1)", callback_data=f"l_{p_id}"),
         InlineKeyboardButton(text="💬 Отзыв", callback_data=f"r_{p_id}")],
        [InlineKeyboardButton(text="🛠 Модерация (Админ)", callback_data=f"adm_mod_{p_id}")]
    ])

# --- ADMIN COMMANDS ---
@router.message(Command("add"), F.chat.id == ADMIN_CHAT_ID)
async def admin_add_start(message: Message):
    guide = (
        "➕ **Добавление проекта**\n\n"
        "Отправьте данные в формате:\n"
        "`категория | Название | Описание`\n\n"
        "**Доступные категории:**\n" + "\n".join([f"• `{k}`" for k in CATEGORIES.keys()])
    )
    await message.answer(guide, parse_mode="Markdown")

@router.message(F.chat.id == ADMIN_CHAT_ID, F.text.contains("|"))
async def process_admin_add(message: Message):
    try:
        parts = [p.strip() for p in message.text.split("|")]
        if len(parts) < 3: raise ValueError
        
        cat, name, desc = parts[0], parts[1], parts[2]
        if cat not in CATEGORIES:
            return await message.reply(f"❌ Категория `{cat}` не существует.")
        
        supabase.table("projects").insert({"name": name, "category": cat, "description": desc}).execute()
        await message.answer(f"✅ **Успешно!**\nПроект `{name}` добавлен в базу.", parse_mode="Markdown")
    except:
        await message.reply("❌ Ошибка формата. Используйте: `категория | Имя | Описание`")

@router.callback_query(F.data.startswith("adm_mod_"), F.from_user.id == ADMIN_CHAT_ID)
async def admin_mod_menu(call: CallbackQuery):
    p_id = call.data.split("_")[2]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ 10 баллов", callback_data=f"sc_{p_id}_10"),
         InlineKeyboardButton(text="➖ 10 баллов", callback_data=f"sc_{p_id}_-10")],
        [InlineKeyboardButton(text="🗑 Удалить проект", callback_data=f"del_{p_id}")]
    ])
    await call.message.answer(f"🛠 **Модерация ID: {p_id}**", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("sc_"))
async def admin_change_score(call: CallbackQuery):
    _, p_id, val = call.data.split("_")
    curr = supabase.table("projects").select("score").eq("id", p_id).single().execute()
    new_s = curr.data['score'] + int(val)
    supabase.table("projects").update({"score": new_s}).eq("id", p_id).execute()
    await call.message.edit_text(f"✅ Рейтинг изменен! Новый счет: `{new_s}`", parse_mode="Markdown")

@router.callback_query(F.data.startswith("del_"))
async def admin_delete(call: CallbackQuery):
    p_id = call.data.split("_")[1]
    supabase.table("projects").delete().eq("id", p_id).execute()
    await call.message.edit_text("🗑 Проект удален из базы.")

# --- USER LOGIC ---
@router.message(CommandStart())
async def cmd_start(message: Message):
    top = supabase.table("projects").select("*").order("score", desc=True).limit(5).execute().data
    text = "🏆 **ЛИДЕРЫ РЕЙТИНГА КМБП**\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    if top:
        for i, p in enumerate(top, 1):
            text += f"{i}. **{p['name']}** — `{p['score']}`\n"
    else: text += "База проектов пока пуста."
    
    text += "\nВыберите категорию ниже для просмотра всех участников 👇"
    await message.answer(text, reply_markup=main_kb(), parse_mode="Markdown")

@router.message(F.text.in_(CATEGORIES.values()))
async def show_category(message: Message):
    cat_key = [k for k, v in CATEGORIES.items() if v == message.text][0]
    projs = supabase.table("projects").select("*").eq("category", cat_key).order("score", desc=True).execute().data
    
    if not projs: return await message.answer("📍 В этой категории пока пусто.")

    for p in projs:
        card = (
            f"💠 **ПРОЕКТ: {p['name'].upper()}**\n"
            f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f"📝 **Описание:** _{p['description']}_\n\n"
            f"📊 **Текущий рейтинг:** `{p['score']}` баллов"
        )
        await message.answer(card, reply_markup=project_card_kb(p['id']), parse_mode="Markdown")

@router.callback_query(F.data.startswith("l_"))
async def handle_like(call: CallbackQuery):
    p_id = int(call.data.split("_")[1])
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "like").execute()
    if check.data: return await call.answer("❌ Вы уже голосовали за этот проект!", show_alert=True)
    
    curr = supabase.table("projects").select("score").eq("id", p_id).single().execute()
    new_s = curr.data['score'] + 1
    supabase.table("projects").update({"score": new_s}).eq("id", p_id).execute()
    supabase.table("user_logs").insert({"user_id": call.from_user.id, "project_id": p_id, "action_type": "like"}).execute()
    
    await call.answer(f"❤️ Голос принят! Рейтинг: {new_s}")
    await call.message.edit_reply_markup(reply_markup=None)

@router.callback_query(F.data.startswith("r_"))
async def review_start(call: CallbackQuery, state: FSMContext):
    p_id = int(call.data.split("_")[1])
    await state.update_data(p_id=p_id)
    await state.set_state(ReviewState.waiting_for_text)
    await call.message.answer("✍️ **Напишите ваш отзыв:**\n(Минимум 5 символов)", parse_mode="Markdown")
    await call.answer()

@router.message(ReviewState.waiting_for_text)
async def review_text(message: Message, state: FSMContext):
    if len(message.text) < 5: return await message.answer("⚠️ Слишком короткий отзыв!")
    await state.update_data(txt=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐"*i, callback_data=f"rate_{i}")] for i in range(5, 0, -1)
    ])
    await state.set_state(ReviewState.waiting_for_rate)
    await message.answer("🌟 **Оцените проект:**", reply_markup=kb)

@router.callback_query(F.data.startswith("rate_"), ReviewState.waiting_for_rate)
async def review_end(call: CallbackQuery, state: FSMContext):
    rate = int(call.data.split("_")[1])
    data = await state.get_data()
    diff = RATING_MAP[rate]
    
    curr = supabase.table("projects").select("score", "name").eq("id", data['p_id']).single().execute()
    new_s = curr.data['score'] + diff
    
    supabase.table("projects").update({"score": new_s}).eq("id", data['p_id']).execute()
    supabase.table("user_logs").insert({
        "user_id": call.from_user.id, "project_id": data['p_id'],
        "action_type": "review", "review_text": data['txt'], "rating_val": rate
    }).execute()
    
    # Notify Admin
    admin_msg = (
        f"📣 **НОВЫЙ ОТЗЫВ**\n\n"
        f"Проект: `{curr.data['name']}`\n"
        f"Оценка: `{rate}/5` ({diff:+})\n"
        f"Юзер: @{call.from_user.username or call.from_user.id}\n"
        f"Текст: _{data['txt']}_"
    )
    await bot.send_message(ADMIN_CHAT_ID, admin_msg, parse_mode="Markdown")
    
    await call.message.edit_text(f"✅ **Опубликовано!**\nНовый рейтинг: `{new_s}`", parse_mode="Markdown")
    await state.clear()

async def main():
    logging.basicConfig(level=logging.INFO)
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
