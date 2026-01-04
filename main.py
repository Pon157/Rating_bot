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

# --- КОНФИГУРАЦИЯ ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", 0))

# Категории проектов
CATEGORIES = {
    "support_bots": "🤖 Боты поддержки",
    "support_admins": "👨‍💻 Админы поддержки",
    "lot_channels": "📦 Каналы лотов",
    "check_channels": "✅ Каналы проверок",
    "kmbp_channels": "🛡 Каналы КМБП"
}

# Математика рейтинга
RATING_MAP = {1: -5, 2: -2, 3: 0, 4: 2, 5: 5}

# Инициализация Supabase
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"Критическая ошибка подключения к Supabase: {e}")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

# --- МЕХАНИЗМ ЛОГИРОВАНИЯ ОШИБОК В ТЕЛЕГРАМ ---
@dp.error()
async def error_handler(event: ErrorEvent):
    error_text = (
        f"⚠️ **КРИТИЧЕСКАЯ ОШИБКА В РАБОТЕ БОТА**\n\n"
        f"Тип: `{type(event.exception).__name__}`\n"
        f"Сообщение: `{event.exception}`\n\n"
        f"**Стек вызовов:**\n"
        f"```python\n{traceback.format_exc()[-3500:]}\n```"
    )
    logging.error(f"Ошибка: {event.exception}")
    try:
        await bot.send_message(ADMIN_CHAT_ID, error_text, parse_mode="Markdown")
    except Exception as e:
        print(f"Не удалось отправить отчет об ошибке: {e}")

# --- MIDDLEWARE БЕЗОПАСНОСТИ И АНТИСПАМА ---
class SecurityMiddleware(BaseMiddleware):
    def __init__(self):
        self.cooldowns = {}

    async def __call__(
        self, 
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: Union[Message, CallbackQuery], 
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if not user or user.is_bot:
            return await handler(event, data)

        # 1. ПРОВЕРКА БАНА (запрос к Supabase)
        try:
            res = supabase.table("banned_users").select("user_id").eq("user_id", user.id).execute()
            if res.data:
                return # Полный игнор забаненного
        except Exception as e:
            logging.error(f"Ошибка проверки бана: {e}")

        # 2. ОПРЕДЕЛЕНИЕ ЧАТА
        chat = data.get("event_chat")
        is_admin_chat = (chat.id == ADMIN_CHAT_ID) if chat else False

        # 3. АНТИСПАМ (60 секунд)
        # Игнорируем антиспам для команды /start и для админ-чата
        if not is_admin_chat:
            if isinstance(event, Message) and event.text == "/start":
                pass 
            else:
                now = time.time()
                last = self.cooldowns.get(user.id, 0)
                if now - last < 60:
                    remains = int(60 - (now - last))
                    if isinstance(event, CallbackQuery):
                        await event.answer(f"⏳ Антиспам! Подождите {remains} сек.", show_alert=True)
                    else:
                        await event.answer(f"⏳ **Охлади пыл!**\n\nКнопки будут доступны через {remains} сек.", parse_mode="Markdown")
                    return
                self.cooldowns[user.id] = now

        return await handler(event, data)

dp.update.outer_middleware(SecurityMiddleware())

# --- СОСТОЯНИЯ (FSM) ---
class ReviewState(StatesGroup):
    waiting_for_text = State()
    waiting_for_rate = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
async def find_project(target: str):
    """Поиск проекта по ID или по имени (регистронезависимо)"""
    try:
        if target.isdigit():
            res = supabase.table("projects").select("*").eq("id", int(target)).execute()
        else:
            res = supabase.table("projects").select("*").ilike("name", f"%{target}%").execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logging.error(f"Ошибка поиска проекта: {e}")
        return None

async def update_project_score(p_id: int, amount: int):
    """Обновление баллов в БД"""
    curr = supabase.table("projects").select("score").eq("id", p_id).single().execute()
    new_score = curr.data['score'] + amount
    supabase.table("projects").update({"score": new_score}).eq("id", p_id).execute()
    return new_score

# --- КЛАВИАТУРЫ ---
def get_main_keyboard():
    buttons = [[KeyboardButton(text=cat_name)] for cat_name in CATEGORIES.values()]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, input_field_placeholder="Выберите нужный раздел...")

def get_project_inline(p_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👍 Повысить репутацию (+1)", callback_data=f"like_{p_id}")],
        [InlineKeyboardButton(text="✍️ Оставить отзыв (баллы)", callback_data=f"rev_start_{p_id}")]
    ])

# --- ОБРАБОТЧИКИ АДМИНИСТРАТОРА ---
@router.message(F.chat.id == ADMIN_CHAT_ID)
async def admin_commands_handler(message: Message):
    if not message.text: return
    cmd_parts = message.text.split()
    command = cmd_parts[0].lower()

    # /add [category] [Name] [Desc]
    if command == "/add" and len(cmd_parts) >= 3:
        cat_key = cmd_parts[1]
        name = cmd_parts[2]
        desc = " ".join(cmd_parts[3:]) if len(cmd_parts) > 3 else "Описание не заполнено."
        
        if cat_key not in CATEGORIES:
            return await message.reply(f"❌ Категория `{cat_key}` не существует!\nДоступные: `{', '.join(CATEGORIES.keys())}`")
        
        try:
            res = supabase.table("projects").insert({"name": name, "category": cat_key, "description": desc}).execute()
            await message.reply(f"🚀 **Проект успешно добавлен!**\n\n🔹 Имя: `{name}`\n🔹 ID: `{res.data[0]['id']}`", parse_mode="Markdown")
        except Exception as e:
            await message.reply(f"❌ Ошибка БД: {e}")

    # /mod [Name/ID] [+/-Score]
    elif command == "/mod" and len(cmd_parts) == 3:
        target = cmd_parts[1]
        try:
            val = int(cmd_parts[2])
            project = await find_project(target)
            if project:
                new_s = await update_project_score(project['id'], val)
                await message.reply(f"⚖️ **Рейтинг изменен!**\nПроект: `{project['name']}`\nИтог: `{new_s}` баллов.", parse_mode="Markdown")
            else:
                await message.reply("❌ Проект не найден.")
        except ValueError:
            await message.reply("❌ Значение баллов должно быть числом (например: +10 или -5).")

    # /del_project [Name/ID]
    elif command == "/del_project" and len(cmd_parts) == 2:
        project = await find_project(cmd_parts[1])
        if project:
            supabase.table("projects").delete().eq("id", project['id']).execute()
            await message.reply(f"🗑 Проект **{project['name']}** и вся его история удалены.")
        else:
            await message.reply("❌ Не могу найти такой проект.")

    # /ban [User_ID]
    elif command == "/ban" and len(cmd_parts) >= 2:
        try:
            target_id = int(cmd_parts[1])
            reason = " ".join(cmd_parts[2:]) if len(cmd_parts) > 2 else "Нарушение правил сообщества"
            supabase.table("banned_users").insert({"user_id": target_id, "reason": reason}).execute()
            await message.reply(f"🚫 Пользователь `{target_id}` заблокирован.")
        except:
            await message.reply("Использование: `/ban ID Причина`")

# --- ОБРАБОТЧИКИ ПОЛЬЗОВАТЕЛЕЙ ---
@router.message(CommandStart())
async def cmd_start(message: Message):
    # Получаем ТОП-10
    top_data = supabase.table("projects").select("*").order("score", desc=True).limit(10).execute().data
    
    welcome_text = (
        "👋 **Добро пожаловать в систему рейтинга КМБП!**\n\n"
        "Ниже представлен актуальный ТОП-10 лучших проектов:\n"
        "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    )
    
    if not top_data:
        welcome_text += "В базе данных пока нет ни одного проекта."
    else:
        for i, p in enumerate(top_data, 1):
            welcome_text += f"{i}. **{p['name']}** — `{p['score']}` баллов\n"
    
    welcome_text += "\nВыберите интересующую вас категорию в меню ниже, чтобы просмотреть подробности или оставить голос."
    
    await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

@router.message(F.text.in_(CATEGORIES.values()))
async def show_category_projects(message: Message):
    # Находим ключ категории по значению из кнопки
    cat_key = [k for k, v in CATEGORIES.items() if v == message.text][0]
    
    projects = supabase.table("projects").select("*").eq("category", cat_key).order("score", desc=True).execute().data
    
    if not projects:
        return await message.answer(f"📍 В категории **{message.text}** пока пусто.")

    await message.answer(f"📑 **Список проектов: {message.text.upper()}**\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯", parse_mode="Markdown")
    
    for p in projects:
        text = (
            f"🔹 **{p['name']}**\n\n"
            f"📝 {p['description']}\n\n"
            f"🏆 Рейтинг проекта: `{p['score']}`"
        )
        await message.answer(text, reply_markup=get_project_inline(p['id']), parse_mode="Markdown")

@router.callback_query(F.data.startswith("like_"))
async def handle_inline_like(call: CallbackQuery):
    p_id = int(call.data.split("_")[1])
    
    # Проверка на повторный лайк (RLS или логи)
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "like").execute()
    if check.data:
        return await call.answer("❌ Вы уже поддерживали этот проект!", show_alert=True)
    
    new_s = await update_project_score(p_id, 1)
    supabase.table("user_logs").insert({"user_id": call.from_user.id, "project_id": p_id, "action_type": "like"}).execute()
    
    await call.answer("❤️ Голос принят!", show_alert=False)
    # Обновляем сообщение (убираем кнопки)
    await call.message.edit_reply_markup(reply_markup=None)

# --- СЛОЖНАЯ СИСТЕМА ОТЗЫВОВ (FSM) ---
@router.callback_query(F.data.startswith("rev_start_"))
async def start_review_flow(call: CallbackQuery, state: FSMContext):
    p_id = int(call.data.split("_")[2])
    
    # Проверка на существование отзыва
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "review").execute()
    if check.data:
        return await call.answer("❌ Вы уже оставляли отзыв об этом проекте!", show_alert=True)
    
    await state.update_data(p_id=p_id)
    await state.set_state(ReviewState.waiting_for_text)
    
    await call.message.answer("✍️ **Оставьте ваш отзыв:**\nОпишите ваши впечатления от работы с проектом. Это поможет другим пользователям.")
    await call.answer()

@router.message(ReviewState.waiting_for_text)
async def process_review_text(message: Message, state: FSMContext):
    if len(message.text) < 5:
        return await message.answer("⚠️ Слишком короткий отзыв. Напишите подробнее!")
    
    await state.update_data(review_txt=message.text)
    
    ratings_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐"*i, callback_data=f"setrate_{i}")] for i in range(5, 0, -1)
    ])
    
    await state.set_state(ReviewState.waiting_for_rate)
    await message.answer("⭐ **На сколько вы оцениваете проект?**\n(1-5 звезд)", reply_markup=ratings_kb)

@router.callback_query(F.data.startswith("setrate_"), ReviewState.waiting_for_rate)
async def finalize_review(call: CallbackQuery, state: FSMContext):
    rating_val = int(call.data.split("_")[1])
    data = await state.get_data()
    
    p_id = data['p_id']
    review_text = data['review_txt']
    score_change = RATING_MAP[rating_val]
    
    # 1. Обновляем рейтинг проекта
    new_total = await update_project_score(p_id, score_change)
    
    # 2. Логируем отзыв
    supabase.table("user_logs").insert({
        "user_id": call.from_user.id,
        "project_id": p_id,
        "action_type": "review",
        "review_text": review_text,
        "rating_val": rating_val
    }).execute()
    
    # 3. Уведомляем админа
    project_data = supabase.table("projects").select("name").eq("id", p_id).single().execute()
    admin_msg = (
        f"🔔 **НОВЫЙ ОТЗЫВ**\n\n"
        f"👤 От: `@{call.from_user.username or call.from_user.id}`\n"
        f"📂 Проект: `{project_data.data['name']}`\n"
        f"🌟 Оценка: `{rating_val}/5` (Баллы: `{score_change:+}`)\n"
        f"💬 Текст: _{review_text}_"
    )
    await bot.send_message(ADMIN_CHAT_ID, admin_msg, parse_mode="Markdown")
    
    await call.message.edit_text(f"✅ **Отзыв опубликован!**\n\nВлияние на рейтинг: `{score_change:+}`\nТекущий счет проекта: `{new_total}`", parse_mode="Markdown")
    await state.clear()
    await call.answer()

# --- ТОЧКА ВХОДА ---
async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    print(f"[{datetime.now()}] Бот @{ (await bot.get_me()).username } запущен и готов к работе.")
    
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен.")
