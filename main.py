import asyncio
import os
import time
import logging
import traceback
from datetime import datetime
from typing import Dict, Any, Callable, Awaitable, Union

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

# --- НАСТРОЙКИ ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", 0))

# Категории (ключ для базы : название для кнопок)
CATEGORIES = {
    "support_bots": "🤖 Боты поддержки",
    "support_admins": "👨‍💻 Админы поддержки",
    "lot_channels": "📦 Каналы лотов",
    "check_channels": "✅ Каналы проверок",
    "kmbp_channels": "🛡 Каналы КМБП"
}

# Очки рейтинга за оценку (1-5 звезд)
RATING_MAP = {1: -5, 2: -2, 3: 0, 4: 2, 5: 5}

# Инициализация клиентов
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

# --- ОТЧЕТ ОБ ОШИБКАХ В ТЕЛЕГРАМ ---
@dp.error()
async def error_handler(event: ErrorEvent):
    error_trace = traceback.format_exc()
    error_msg = (
        f"🚨 **КРИТИЧЕСКАЯ ОШИБКА**\n\n"
        f"Тип: `{type(event.exception).__name__}`\n"
        f"Текст: `{event.exception}`\n\n"
        f"**Stacktrace:**\n`{error_trace[-3500:]}`"
    )
    logging.error(error_trace)
    try:
        await bot.send_message(ADMIN_CHAT_ID, error_msg, parse_mode="Markdown")
    except:
        pass

# --- MIDDLEWARE: БАН И АНТИСПАМ (60 СЕК) ---
class SecurityMiddleware(BaseMiddleware):
    def __init__(self):
        self.cooldowns = {}

    async def __call__(self, handler: Callable, event: TelegramObject, data: Dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if not user or user.is_bot:
            return await handler(event, data)

        # 1. Проверка на бан
        res = supabase.table("banned_users").select("user_id").eq("user_id", user.id).execute()
        if res.data:
            return # Полный игнор

        # 2. Антиспам (кроме админ-чата)
        chat = data.get("event_chat")
        if chat and chat.id != ADMIN_CHAT_ID:
            # Разрешаем команду /start без КД
            is_start = isinstance(event, Message) and event.text and event.text.startswith("/start")
            
            if not is_start:
                now = time.time()
                last = self.cooldowns.get(user.id, 0)
                if now - last < 60:
                    wait = int(60 - (now - last))
                    if isinstance(event, CallbackQuery):
                        await event.answer(f"⏳ Антиспам! Подождите {wait} сек.", show_alert=True)
                    elif isinstance(event, Message):
                        await event.answer(f"⏳ **Охладись!**\nКнопки будут активны через {wait} сек.", parse_mode="Markdown")
                    return
                self.cooldowns[user.id] = now

        return await handler(event, data)

dp.update.outer_middleware(SecurityMiddleware())

# --- FSM СОСТОЯНИЯ ---
class ReviewState(StatesGroup):
    waiting_for_text = State()
    waiting_for_rate = State()

# --- ВСПОМОГАТЕЛЬНЫЕ КЛАВИАТУРЫ ---
def get_main_kb():
    buttons = [[KeyboardButton(text=v)] for v in CATEGORIES.values()]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, input_field_placeholder="Выберите категорию...")

def get_project_inline(p_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👍 Повысить репутацию (+1)", callback_data=f"like_{p_id}")],
        [InlineKeyboardButton(text="✍️ Оставить отзыв", callback_data=f"rev_start_{p_id}")]
    ])

# --- АДМИН-ФУНКЦИИ ---
@router.message(F.chat.id == ADMIN_CHAT_ID)
async def admin_panel(message: Message):
    if not message.text: return
    args = message.text.split()
    cmd = args[0].lower()

    # /add [category] [Name] [Description]
    if cmd == "/add" and len(args) >= 3:
        cat_key, name = args[1], args[2]
        desc = " ".join(args[3:]) if len(args) > 3 else "Описания нет."
        if cat_key not in CATEGORIES:
            return await message.reply(f"❌ Категории: `{', '.join(CATEGORIES.keys())}`")
        
        supabase.table("projects").insert({"name": name, "category": cat_key, "description": desc}).execute()
        await message.reply(f"🚀 **Проект '{name}' добавлен!**", parse_mode="Markdown")

    # /mod [Name/ID] [+/-Score]
    elif cmd == "/mod" and len(args) == 3:
        target = args[1]
        try:
            val = int(args[2])
            # Поиск по имени или ID
            if target.isdigit():
                p_res = supabase.table("projects").select("*").eq("id", int(target)).execute()
            else:
                p_res = supabase.table("projects").select("*").ilike("name", f"%{target}%").execute()
            
            if p_res.data:
                p = p_res.data[0]
                new_s = p['score'] + val
                supabase.table("projects").update({"score": new_s}).eq("id", p['id']).execute()
                await message.reply(f"⚖️ **Обновлено!**\n{p['name']}: `{new_s}` баллов.")
            else: await message.reply("❌ Проект не найден.")
        except: await message.reply("❌ Ошибка. Пример: `/mod Название +10`")

    # /ban [User_ID] [Reason]
    elif cmd == "/ban" and len(args) >= 2:
        try:
            u_id = int(args[1])
            reason = " ".join(args[2:]) if len(args) > 2 else "Нарушение правил."
            supabase.table("banned_users").insert({"user_id": u_id, "reason": reason}).execute()
            await message.reply(f"🚫 Пользователь `{u_id}` забанен.")
        except: await message.reply("❌ Пример: `/ban 123456 Причина`")

    # /del_project [Name/ID]
    elif cmd == "/del_project" and len(args) == 2:
        target = args[1]
        supabase.table("projects").delete().ilike("name", f"%{target}%").execute()
        await message.reply(f"🗑 Проект `{target}` удален.")

# --- ПОЛЬЗОВАТЕЛЬСКАЯ ЛОГИКА ---
@router.message(CommandStart())
async def cmd_start(message: Message):
    top = supabase.table("projects").select("*").order("score", desc=True).limit(10).execute().data
    msg = "🏆 **ТОП-10 РЕЙТИНГА КМБП**\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    if not top:
        msg += "База данных пуста."
    else:
        for i, p in enumerate(top, 1):
            msg += f"{i}. **{p['name']}** — `{p['score']}` баллов\n"
    
    await message.answer(msg, reply_markup=get_main_kb(), parse_mode="Markdown")

@router.message(F.text.in_(CATEGORIES.values()))
async def show_category(message: Message):
    cat_key = [k for k, v in CATEGORIES.items() if v == message.text][0]
    projs = supabase.table("projects").select("*").eq("category", cat_key).order("score", desc=True).execute().data
    
    if not projs:
        return await message.answer(f"📍 В категории **{message.text}** пока нет проектов.")

    await message.answer(f"✨ **{message.text.upper()}**")
    for p in projs:
        await message.answer(
            f"🔹 **{p['name']}**\n\n{p['description']}\n\n🏆 Рейтинг: `{p['score']}`",
            reply_markup=get_project_inline(p['id']), parse_mode="Markdown"
        )

# --- ЛАЙКИ И ОТЗЫВЫ ---
@router.callback_query(F.data.startswith("like_"))
async def handle_like(call: CallbackQuery):
    p_id = int(call.data.split("_")[1])
    # Проверка на дубликат лайка
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "like").execute()
    if check.data:
        return await call.answer("❌ Вы уже ставили лайк этому проекту!", show_alert=True)
    
    # Обновляем счет
    curr = supabase.table("projects").select("score").eq("id", p_id).single().execute()
    new_s = curr.data['score'] + 1
    supabase.table("projects").update({"score": new_s}).eq("id", p_id).execute()
    # Логируем
    supabase.table("user_logs").insert({"user_id": call.from_user.id, "project_id": p_id, "action_type": "like"}).execute()
    
    await call.answer("❤️ Голос принят!")
    await call.message.edit_reply_markup(reply_markup=None)

@router.callback_query(F.data.startswith("rev_start_"))
async def review_flow_start(call: CallbackQuery, state: FSMContext):
    p_id = int(call.data.split("_")[2])
    # Проверка на дубликат отзыва
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "review").execute()
    if check.data:
        return await call.answer("❌ Вы уже оставляли отзыв!", show_alert=True)
    
    await state.update_data(p_id=p_id)
    await state.set_state(ReviewState.waiting_for_text)
    await call.message.answer("💬 **Напишите ваш текст отзыва:**")
    await call.answer()

@router.message(ReviewState.waiting_for_text)
async def review_flow_text(message: Message, state: FSMContext):
    await state.update_data(txt=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐"*i, callback_data=f"rate_{i}")] for i in range(5, 0, -1)
    ])
    await state.set_state(ReviewState.waiting_for_rate)
    await message.answer("🌟 **Оцените проект от 1 до 5 звезд:**", reply_markup=kb)

@router.callback_query(F.data.startswith("rate_"), ReviewState.waiting_for_rate)
async def review_flow_finish(call: CallbackQuery, state: FSMContext):
    rate = int(call.data.split("_")[1])
    data = await state.get_data()
    diff = RATING_MAP[rate]
    
    # Обновляем рейтинг
    curr = supabase.table("projects").select("score", "name").eq("id", data['p_id']).single().execute()
    new_s = curr.data['score'] + diff
    supabase.table("projects").update({"score": new_s}).eq("id", data['p_id']).execute()
    
    # Логируем отзыв
    supabase.table("user_logs").insert({
        "user_id": call.from_user.id, "username": call.from_user.username,
        "project_id": data['p_id'], "action_type": "review",
        "review_text": data['txt'], "rating_val": rate
    }).execute()
    
    # Уведомляем админа
    admin_notif = (
        f"📣 **НОВЫЙ ОТЗЫВ**\n\n"
        f"Проект: `{curr.data['name']}`\n"
        f"Оценка: `{rate}/5` ({diff:+})\n"
        f"От пользователя: @{call.from_user.username or call.from_user.id}\n"
        f"Текст: _{data['txt']}_"
    )
    await bot.send_message(ADMIN_CHAT_ID, admin_notif, parse_mode="Markdown")
    
    await call.message.edit_text(f"✅ **Отзыв опубликован!**\nТекущий рейтинг: `{new_s}`", parse_mode="Markdown")
    await state.clear()

# --- ЗАПУСК ---
async def main():
    logging.basicConfig(level=logging.INFO)
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
