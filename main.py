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

# --- MIDDLEWARE (ИСПРАВЛЕННЫЙ) ---
class SecurityMiddleware(BaseMiddleware):
    def __init__(self):
        self.cooldowns = {}

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user or user.is_bot:
            return await handler(event, data)

        # 1. Проверка БАНА
        res = supabase.table("banned_users").select("user_id").eq("user_id", user.id).execute()
        if res.data:
            return 

        # 2. АНТИСПАМ (60 секунд)
        # Пропускаем /start и сообщения от админа
        chat = data.get("event_chat")
        if chat and chat.id == ADMIN_CHAT_ID:
            return await handler(event, data)

        if isinstance(event, (Message, CallbackQuery)):
            if isinstance(event, Message) and event.text == "/start":
                return await handler(event, data)
                
            now = time.time()
            last = self.cooldowns.get(user.id, 0)
            if now - last < 60:
                remains = int(60 - (now - last))
                if isinstance(event, CallbackQuery):
                    await event.answer(f"⏳ Кулдаун! Еще {remains} сек.", show_alert=True)
                else:
                    await event.answer(f"⏳ **Слишком быстро!**\nПопробуйте через {remains} сек.", parse_mode="Markdown")
                return
            self.cooldowns[user.id] = now

        return await handler(event, data)

dp.update.outer_middleware(SecurityMiddleware())

# --- STATES ---
class ReviewState(StatesGroup):
    waiting_for_text = State()
    waiting_for_rate = State()

# --- UTILS ---
def find_project(target: str):
    if target.isdigit():
        res = supabase.table("projects").select("*").eq("id", int(target)).execute()
    else:
        res = supabase.table("projects").select("*").ilike("name", f"%{target}%").execute()
    return res.data[0] if res.data else None

def update_score(p_id, amount):
    curr = supabase.table("projects").select("score").eq("id", p_id).single().execute()
    new_s = curr.data['score'] + amount
    supabase.table("projects").update({"score": new_s}).eq("id", p_id).execute()
    return new_s

# --- KEYBOARDS ---
def main_kb():
    buttons = [[KeyboardButton(text=v)] for v in CATEGORIES.values()]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, input_field_placeholder="Выберите категорию...")

def project_kb(p_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👍 Лайк (+1)", callback_data=f"like_{p_id}")],
        [InlineKeyboardButton(text="✍️ Оставить отзыв", callback_data=f"rev_{p_id}")]
    ])

# --- ADMIN COMMANDS ---
@router.message(F.chat.id == ADMIN_CHAT_ID)
async def admin_handler(message: Message):
    if not message.text: return
    text = message.text
    args = text.split()

    if text.startswith("/add") and len(args) >= 3:
        cat, name = args[1], args[2]
        desc = " ".join(args[3:]) if len(args) > 3 else "Описание отсутствует."
        if cat not in CATEGORIES:
            return await message.reply("❌ Ошибка! Категории: " + ", ".join(CATEGORIES.keys()))
        res = supabase.table("projects").insert({"name": name, "category": cat, "description": desc}).execute()
        await message.reply(f"✅ **Успешно!**\nПроект: `{name}`\nID: `{res.data[0]['id']}`", parse_mode="Markdown")

    elif text.startswith("/mod") and len(args) == 3:
        p = find_project(args[1])
        if p:
            new_s = update_score(p['id'], int(args[2]))
            await message.reply(f"⚖️ **Рейтинг обновлен!**\nПроект: {p['name']}\nНовый счет: `{new_s}`", parse_mode="Markdown")
        else: await message.reply("❌ Проект не найден.")

    elif text.startswith("/del_project") and len(args) == 2:
        p = find_project(args[1])
        if p:
            supabase.table("projects").delete().eq("id", p['id']).execute()
            await message.reply(f"🗑 Проект **{p['name']}** удален навсегда.")

    elif text.startswith("/ban") and len(args) == 2:
        u_id = int(args[1])
        supabase.table("banned_users").insert({"user_id": u_id, "reason": "Нарушение"}).execute()
        await message.reply(f"🚫 Пользователь `{u_id}` заблокирован.", parse_mode="Markdown")

# --- USER HANDLERS ---
@router.message(CommandStart())
async def cmd_start(message: Message):
    top = supabase.table("projects").select("*").order("score", desc=True).limit(10).execute().data
    res_text = "🏆 **ТОП-10 ЛУЧШИХ ПРОЕКТОВ**\n"
    res_text += "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    if not top:
        res_text += "Список пуст..."
    for i, p in enumerate(top, 1):
        res_text += f"{i}. **{p['name']}** — `{p['score']}` б.\n"
    
    await message.answer(res_text, reply_markup=main_kb(), parse_mode="Markdown")

@router.message(F.text.in_(CATEGORIES.values()))
async def show_cat(message: Message):
    cat_key = [k for k, v in CATEGORIES.items() if v == message.text][0]
    projects = supabase.table("projects").select("*").eq("category", cat_key).order("score", desc=True).execute().data
    
    if not projects:
        return await message.answer("📍 В этой категории пока нет проектов.")

    await message.answer(f"📍 **{message.text.upper()}**\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯", parse_mode="Markdown")
    for p in projects:
        await message.answer(
            f"🔹 **{p['name']}**\n\n{p['description']}\n\n🏆 Рейтинг: `{p['score']}`",
            reply_markup=project_kb(p['id']), parse_mode="Markdown"
        )

@router.callback_query(F.data.startswith("like_"))
async def handle_like(call: CallbackQuery):
    p_id = int(call.data.split("_")[1])
    # Проверка повтора
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "like").execute()
    if check.data:
        return await call.answer("❌ Вы уже ставили лайк!", show_alert=True)
    
    new_s = update_score(p_id, 1)
    supabase.table("user_logs").insert({"user_id": call.from_user.id, "project_id": p_id, "action_type": "like"}).execute()
    await call.answer(f"❤️ Спасибо! Рейтинг: {new_s}", show_alert=False)
    await call.message.edit_reply_markup(reply_markup=None) # Убираем кнопки после нажатия

# --- REVIEW SYSTEM ---
@router.callback_query(F.data.startswith("rev_"))
async def rev_init(call: CallbackQuery, state: FSMContext):
    p_id = int(call.data.split("_")[1])
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "review").execute()
    if check.data:
        return await call.answer("❌ Вы уже оставляли отзыв!", show_alert=True)
    
    await state.update_data(p_id=p_id)
    await state.set_state(ReviewState.waiting_for_text)
    await call.message.answer("💬 **Напишите ваш отзыв:**\n(Пожалуйста, будьте конструктивны)", parse_mode="Markdown")
    await call.answer()

@router.message(ReviewState.waiting_for_text)
async def rev_text(message: Message, state: FSMContext):
    await state.update_data(txt=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐"*i, callback_data=f"r_{i}")] for i in range(5, 0, -1)
    ])
    await state.set_state(ReviewState.waiting_for_rate)
    await message.answer("⭐ **Оцените проект:**", reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data.startswith("r_"), ReviewState.waiting_for_rate)
async def rev_done(call: CallbackQuery, state: FSMContext):
    val = int(call.data.split("_")[1])
    data = await state.get_data()
    diff = RATING_MAP[val]
    
    new_s = update_score(data['p_id'], diff)
    supabase.table("user_logs").insert({
        "user_id": call.from_user.id, "project_id": data['p_id'],
        "action_type": "review", "review_text": data['txt'], "rating_val": val
    }).execute()

    # Уведомление админам
    p_name = supabase.table("projects").select("name").eq("id", data['p_id']).single().execute().data['name']
    await bot.send_message(ADMIN_CHAT_ID, 
        f"📣 **Новый отзыв!**\nПроект: `{p_name}`\nОценка: `{val}/5` ({diff:+})\nОтзыв: _{data['txt']}_", 
        parse_mode="Markdown")

    await call.message.edit_text(f"✅ **Готово!**\nВаш отзыв учтен. Текущий рейтинг проекта: `{new_s}`", parse_mode="Markdown")
    await state.clear()

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
