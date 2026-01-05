import asyncio
import os
import logging
from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from supabase import create_client, Client
from dotenv import load_dotenv

# --- НАСТРОЙКИ ТОПИКОВ (Замени цифры на ID из ссылок) ---
TOPIC_LOGS_ALL = 0  # Общий топик для ВСЕХ логов/отзывов

TOPICS_BY_CATEGORY = {
    "support_bots": 38,    # Топик для Ботов поддержки
    "support_admins": 41,  # Топик для Админов поддержки
    "lot_channels": 39,    # Топик для Каналов лотов
    "check_channels": 42,  # Топик для Каналов проверок
    "kmbp_channels": 40    # Топик для Каналов КМБП
}

# --- ИНИЦИАЛИЗАЦИЯ ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN") 
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_GROUP_ID = int(os.getenv("ADMIN_CHAT_ID", 0))

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

class AdminScoreState(StatesGroup):
    waiting_for_reason = State()

# --- ПРОВЕРКА ПРАВ (ПО ЧАТУ) ---
async def is_user_admin(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=ADMIN_GROUP_ID, user_id=user_id)
        return member.status in ["creator", "administrator", "member"]
    except Exception as e:
        logging.error(f"Ошибка проверки админки: {e}")
        return False

# --- MIDDLEWARE (БАН) ---
class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user or user.is_bot: return await handler(event, data)
        if await is_user_admin(user.id): return await handler(event, data)
        res = supabase.table("banned_users").select("user_id").eq("user_id", user.id).execute()
        if res.data: return
        return await handler(event, data)

# --- КЛАВИАТУРЫ ---
def main_kb():
    buttons = [[KeyboardButton(text=v)] for v in CATEGORIES.values()]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def project_card_kb(p_id):
    """Чистая карточка проекта без кнопок"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔘 Открыть панель", callback_data=f"panel_{p_id}")]
    ])

def project_panel_kb(p_id):
    """Полная панель действий"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⭐ Оценить", callback_data=f"rev_{p_id}"),
            InlineKeyboardButton(text="❤️ Поддержать", callback_data=f"like_{p_id}")
        ],
        [
            InlineKeyboardButton(text="💬 Отзывы", callback_data=f"viewrev_{p_id}"),
            InlineKeyboardButton(text="📊 История", callback_data=f"history_{p_id}")
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"back_{p_id}")]
    ])

def back_to_panel_kb(p_id):
    """Кнопка назад к панели"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к панели", callback_data=f"panel_{p_id}")]
    ])

# --- ФУНКЦИЯ ОТПРАВКИ ЛОГОВ ---
async def send_log_to_topics(admin_text: str, category: str = None):
    """Отправляет лог во все нужные топики"""
    try:
        # 1. Шлем в общий топик логов
        if TOPIC_LOGS_ALL:
            await bot.send_message(
                ADMIN_GROUP_ID, 
                admin_text, 
                message_thread_id=TOPIC_LOGS_ALL, 
                parse_mode="HTML"
            )
            logging.info(f"Лог отправлен в общий топик {TOPIC_LOGS_ALL}")
        
        # 2. Шлем в топик конкретной категории
        if category:
            cat_topic = TOPICS_BY_CATEGORY.get(category)
            if cat_topic:
                await bot.send_message(
                    ADMIN_GROUP_ID, 
                    admin_text, 
                    message_thread_id=cat_topic, 
                    parse_mode="HTML"
                )
                logging.info(f"Лог отправлен в топик категории {category}: {cat_topic}")
        
        # 3. Если общий топик не указан, отправляем в основной чат
        elif not TOPIC_LOGS_ALL and ADMIN_GROUP_ID:
            await bot.send_message(ADMIN_GROUP_ID, admin_text, parse_mode="HTML")
            logging.info("Лог отправлен в основной админ-чат")
            
    except Exception as e:
        logging.error(f"Ошибка отправки лога: {e}")

# --- АДМИН-КОМАНДЫ ---

@router.message(Command("add"))
async def admin_add(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id): 
        return
        
    await state.clear()
    
    try:
        if len(message.text.split()) < 2:
            await message.reply(
                "❌ Неверный формат. Используйте:\n"
                "<code>/add категория | Название | Описание</code>\n\n"
                "Пример: <code>/add support_bots | Бот Помощи | Отвечает на вопросы</code>",
                parse_mode="HTML"
            )
            return
        
        raw = message.text.split(maxsplit=1)[1]
        parts = raw.split("|")
        
        if len(parts) < 3:
            await message.reply(
                "❌ Неверный формат. Нужно три параметра через '|':\n"
                "1. Категория\n"
                "2. Название\n"
                "3. Описание",
                parse_mode="HTML"
            )
            return
        
        cat, name, desc = [p.strip() for p in parts[:3]]
        
        if cat not in CATEGORIES:
            categories_list = "\n".join([f"- <code>{k}</code> ({v})" for k, v in CATEGORIES.items()])
            await message.reply(
                f"❌ Неверная категория. Доступные:\n{categories_list}",
                parse_mode="HTML"
            )
            return
        
        existing = supabase.table("projects").select("*").eq("name", name).execute()
        if existing.data:
            await message.reply(
                f"⚠️ Проект <b>{name}</b> уже существует!",
                parse_mode="HTML"
            )
            return
        
        result = supabase.table("projects").insert({
            "name": name, 
            "category": cat, 
            "description": desc,
            "score": 0
        }).execute()
        
        if result.data:
            # Добавляем запись в историю
            supabase.table("rating_history").insert({
                "project_id": result.data[0]['id'],
                "admin_id": message.from_user.id,
                "admin_username": message.from_user.username,
                "change_type": "create",
                "score_before": 0,
                "score_after": 0,
                "change_amount": 0,
                "reason": "Создание проекта",
                "is_admin_action": True
            }).execute()
            
            # Отправляем лог
            log_text = (f"📋 <b>Добавлен новый проект:</b>\n\n"
                       f"🏷 Название: <b>{name}</b>\n"
                       f"📂 Категория: <code>{cat}</code>\n"
                       f"📝 Описание: {desc}\n"
                       f"👤 Админ: @{message.from_user.username or message.from_user.id}")
            
            await send_log_to_topics(log_text, cat)
            
            await message.reply(
                f"✅ Проект <b>{name}</b> успешно добавлен!",
                parse_mode="HTML"
            )
        else:
            await message.reply(
                "❌ Ошибка при добавлении проекта.",
            )
            
    except Exception as e:
        logging.error(f"Ошибка в /add: {e}")
        await message.reply(
            "❌ Ошибка при обработке команды.",
        )

@router.message(Command("del"))
async def admin_delete(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id): 
        return
        
    await state.clear()
    
    try:
        if len(message.text.split()) < 2:
            await message.reply(
                "❌ Укажите название проекта для удаления."
            )
            return
        
        name = message.text.split(maxsplit=1)[1].strip()
        
        existing = supabase.table("projects").select("*").eq("name", name).execute()
        if not existing.data:
            await message.reply(
                f"❌ Проект <b>{name}</b> не найден!",
                parse_mode="HTML"
            )
            return
        
        project = existing.data[0]
        project_id = project['id']
        category = project['category']
        score = project['score']
        
        # Считаем сколько отзывов удаляем
        reviews_count = supabase.table("user_logs").select("*").eq("project_id", project_id).execute()
        reviews_num = len(reviews_count.data) if reviews_count.data else 0
        
        # Добавляем запись в историю
        supabase.table("rating_history").insert({
            "project_id": project_id,
            "admin_id": message.from_user.id,
            "admin_username": message.from_user.username,
            "change_type": "delete",
            "score_before": score,
            "score_after": 0,
            "change_amount": -score,
            "reason": "Удаление проекта",
            "is_admin_action": True
        }).execute()
        
        # Удаление проекта и связанных отзывов
        supabase.table("projects").delete().eq("id", project_id).execute()
        supabase.table("user_logs").delete().eq("project_id", project_id).execute()
        supabase.table("rating_history").delete().eq("project_id", project_id).execute()
        
        # Отправляем лог
        log_text = (f"🗑 <b>Проект удален:</b>\n\n"
                   f"🏷 Название: <b>{name}</b>\n"
                   f"📂 Категория: <code>{category}</code>\n"
                   f"📊 Удалено отзывов: {reviews_num}\n"
                   f"🔢 Финальный рейтинг: {score}\n"
                   f"👤 Админ: @{message.from_user.username or message.from_user.id}")
        
        await send_log_to_topics(log_text, category)
        
        await message.reply(
            f"🗑 Проект <b>{name}</b> удален!\n"
            f"📊 Удалено отзывов: {reviews_num}\n"
            f"🔢 Финальный рейтинг: {score}",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logging.error(f"Ошибка в /del: {e}")
        await message.reply(
            "❌ Ошибка при удалении проекта."
        )

@router.message(Command("score"))
async def admin_score(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id): 
        return
        
    try:
        if len(message.text.split()) < 2:
            await message.reply(
                "❌ Неверный формат. Используйте:\n"
                "<code>/score Название | число</code>\n\n"
                "Пример: <code>/score Бот Помощи | 10</code>",
                parse_mode="HTML"
            )
            return
        
        raw = message.text.split(maxsplit=1)[1]
        parts = raw.split("|")
        
        if len(parts) < 2:
            await message.reply(
                "❌ Неверный формат. Нужно два параметра."
            )
            return
        
        name, val_str = [p.strip() for p in parts[:2]]
        
        try:
            val = int(val_str)
        except ValueError:
            await message.reply(
                f"❌ <b>{val_str}</b> не является числом!",
                parse_mode="HTML"
            )
            return
        
        existing = supabase.table("projects").select("*").eq("name", name).execute()
        if not existing.data:
            await message.reply(
                f"❌ Проект <b>{name}</b> не найден!",
                parse_mode="HTML"
            )
            return
        
        project = existing.data[0]
        await state.update_data(
            project_id=project['id'],
            project_name=name,
            category=project['category'],
            old_score=project['score'],
            change_amount=val
        )
        
        await state.set_state(AdminScoreState.waiting_for_reason)
        await message.reply(
            f"📝 <b>Укажите причину изменения рейтинга для проекта <i>{name}</i>:</b>\n\n"
            f"🔢 Текущий рейтинг: <b>{project['score']}</b>\n"
            f"📊 Изменение: <code>{val:+d}</code>\n"
            f"🔢 Новый рейтинг будет: <b>{project['score'] + val}</b>",
            parse_mode="HTML"
        )
            
    except Exception as e:
        logging.error(f"Ошибка в /score: {e}")
        await message.reply(
            "❌ Ошибка при обработке команды."
        )

@router.message(AdminScoreState.waiting_for_reason)
async def admin_score_reason(message: Message, state: FSMContext):
    """Обработка причины изменения рейтинга"""
    if message.text.startswith("/"):
        await state.clear()
        return
    
    data = await state.get_data()
    reason = message.text.strip()
    
    if not reason:
        await message.reply(
            "❌ Причина не может быть пустой. Пожалуйста, укажите причину изменения."
        )
        return
    
    try:
        project_id = data['project_id']
        project_name = data['project_name']
        category = data['category']
        old_score = data['old_score']
        change_amount = data['change_amount']
        new_score = old_score + change_amount
        
        # Обновляем рейтинг проекта
        supabase.table("projects").update({"score": new_score}).eq("id", project_id).execute()
        
        # Добавляем запись в историю
        supabase.table("rating_history").insert({
            "project_id": project_id,
            "admin_id": message.from_user.id,
            "admin_username": message.from_user.username,
            "change_type": "admin_change",
            "score_before": old_score,
            "score_after": new_score,
            "change_amount": change_amount,
            "reason": reason,
            "is_admin_action": True
        }).execute()
        
        # Отправляем лог
        log_text = (f"⚖️ <b>Изменен рейтинг проекта:</b>\n\n"
                   f"🏷 Название: <b>{project_name}</b>\n"
                   f"📂 Категория: <code>{category}</code>\n"
                   f"🔢 Было: <b>{old_score}</b>\n"
                   f"🔢 Стало: <b>{new_score}</b>\n"
                   f"📊 Изменение: <code>{change_amount:+d}</code>\n"
                   f"📝 Причина: <i>{reason}</i>\n"
                   f"👤 Админ: @{message.from_user.username or message.from_user.id}")
        
        await send_log_to_topics(log_text, category)
        
        change_symbol = "📈" if change_amount > 0 else "📉" if change_amount < 0 else "➡️"
        await message.reply(
            f"{change_symbol} <b>Рейтинг проекта изменен!</b>\n\n"
            f"🏷 Проект: <b>{project_name}</b>\n"
            f"🔢 {old_score} → <b>{new_score}</b> ({change_amount:+d})\n"
            f"📝 Причина: <i>{reason}</i>",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logging.error(f"Ошибка в обработке причины: {e}")
        await message.reply(
            "❌ Ошибка при сохранении изменений."
        )
    
    await state.clear()

@router.message(Command("delrev"))
async def admin_delrev(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id): 
        return
        
    await state.clear()
    
    try:
        if len(message.text.split()) < 2:
            await message.reply(
                "❌ Укажите ID отзыва для удаления."
            )
            return
        
        log_id_str = message.text.split()[1]
        
        try:
            log_id = int(log_id_str)
        except ValueError:
            await message.reply(
                f"❌ <b>{log_id_str}</b> не является числовым ID!",
                parse_mode="HTML"
            )
            return
        
        rev_result = supabase.table("user_logs").select("*").eq("id", log_id).execute()
        if not rev_result.data:
            await message.reply(
                f"❌ Отзыв <b>#{log_id}</b> не найден!",
                parse_mode="HTML"
            )
            return
        
        rev = rev_result.data[0]
        
        project_result = supabase.table("projects").select("*").eq("id", rev['project_id']).execute()
        if not project_result.data:
            await message.reply(
                f"❌ Проект отзыва #{log_id} не найден!"
            )
            return
        
        project = project_result.data[0]
        old_score = project['score']
        rating_change = RATING_MAP.get(rev['rating_val'], 0)
        new_score = old_score - rating_change
        
        # Добавляем запись в историю об удалении отзыва
        supabase.table("rating_history").insert({
            "project_id": rev['project_id'],
            "admin_id": message.from_user.id,
            "admin_username": message.from_user.username,
            "change_type": "delete_review",
            "score_before": old_score,
            "score_after": new_score,
            "change_amount": -rating_change,
            "reason": f"Удаление отзыва #{log_id} (оценка: {rev['rating_val']}/5)",
            "is_admin_action": True,
            "related_review_id": log_id
        }).execute()
        
        # Обновляем рейтинг проекта
        supabase.table("projects").update({"score": new_score}).eq("id", rev['project_id']).execute()
        
        # Удаляем отзыв
        supabase.table("user_logs").delete().eq("id", log_id).execute()
        
        # Отправляем лог
        log_text = (f"🗑 <b>Удален отзыв:</b>\n\n"
                   f"🏷 Проект: <b>{project['name']}</b>\n"
                   f"📂 Категория: <code>{project['category']}</code>\n"
                   f"🆔 ID отзыва: <code>{log_id}</code>\n"
                   f"⭐ Оценка: {rev['rating_val']}/5\n"
                   f"📊 Изменение рейтинга: {rating_change:+d}\n"
                   f"🔢 Новый рейтинг: {new_score}\n"
                   f"📝 Текст отзыва: <i>{rev['review_text'][:100]}...</i>\n"
                   f"👤 Удалил: @{message.from_user.username or message.from_user.id}")
        
        await send_log_to_topics(log_text, project['category'])
        
        await message.reply(
            f"🗑 Отзыв <b>#{log_id}</b> удален!\n"
            f"📁 Проект: <b>{project['name']}</b>\n"
            f"📊 Рейтинг: {old_score} → {new_score} ({rating_change:+d})",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logging.error(f"Ошибка в /delrev: {e}")
        await message.reply(
            "❌ Ошибка при удалении отзыва."
        )

# --- ЛОГИКА ПОЛЬЗОВАТЕЛЯ ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    top = supabase.table("projects").select("*").order("score", desc=True).limit(5).execute().data
    text = "<b>🏆 ТОП-5 ПРОЕКТОВ КМБП</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    if top:
        for i, p in enumerate(top, 1):
            text += f"{i}. <b>{p['name']}</b> — <code>{p['score']}</code>\n"
    else: 
        text += "Список пуст.\n"
    
    text += "\n📊 <i>Нажмите на категорию ниже, чтобы увидеть все проекты</i>"
    
    await message.answer(text, reply_markup=main_kb(), parse_mode="HTML")

@router.message(F.text.in_(CATEGORIES.values()))
async def show_cat(message: Message):
    cat_key = [k for k, v in CATEGORIES.items() if v == message.text][0]
    data = supabase.table("projects").select("*").eq("category", cat_key).order("score", desc=True).execute().data
    if not data: 
        await message.answer(f"В разделе '{message.text}' пусто.")
        return
    
    for p in data:
        # Чистая карточка проекта
        card = f"<b>{p['name']}</b>\n\n{p['description']}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        card += f"📊 Текущий рейтинг: <b>{p['score']}</b>\n\n"
        card += f"<i>Нажмите кнопку ниже для управления проектом</i>"
        
        await message.answer(card, reply_markup=project_card_kb(p['id']), parse_mode="HTML")

@router.callback_query(F.data.startswith("panel_"))
async def open_panel(call: CallbackQuery):
    """Открывает панель управления проектом"""
    p_id = call.data.split("_")[1]
    
    # Получаем информацию о проекте
    project = supabase.table("projects").select("*").eq("id", p_id).single().execute().data
    if not project:
        await call.answer("Проект не найден.", show_alert=True)
        return
    
    # Получаем последние изменения
    recent_changes = supabase.table("rating_history").select("*")\
        .eq("project_id", p_id)\
        .order("created_at", desc=True)\
        .limit(2)\
        .execute().data
    
    text = f"<b>🔘 ПАНЕЛЬ УПРАВЛЕНИЯ</b>\n\n"
    text += f"<b>{project['name']}</b>\n"
    text += f"{project['description']}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    text += f"📊 Текущий рейтинг: <b>{project['score']}</b>\n\n"
    
    if recent_changes:
        text += f"<b>📈 Последние изменения:</b>\n"
        for change in recent_changes:
            date = change['created_at'][:10] if change['created_at'] else ""
            symbol = "📈" if change['change_amount'] > 0 else "📉" if change['change_amount'] < 0 else "➡️"
            text += f"{symbol} <code>{change['change_amount']:+d}</code> — {change['reason'][:50]}... ({date})\n"
        text += f"\n"
    
    text += f"<i>Выберите действие:</i>"
    
    await call.message.edit_text(text, reply_markup=project_panel_kb(p_id), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("back_"))
async def back_to_panel(call: CallbackQuery):
    """Возврат к панели из других разделов"""
    p_id = call.data.split("_")[1]
    await open_panel(call)

@router.callback_query(F.data.startswith("rev_"))
async def rev_start(call: CallbackQuery, state: FSMContext):
    p_id = call.data.split("_")[1]
    check = supabase.table("user_logs").select("*").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "review").execute()
    await state.update_data(p_id=p_id)
    await state.set_state(ReviewState.waiting_for_text)
    
    project = supabase.table("projects").select("name").eq("id", p_id).single().execute().data
    project_name = project['name'] if project else "Проект"
    
    txt = f"📝 <b>Изменение отзыва для проекта {project_name}</b>\n\nВведите новый текст отзыва:"
    if not check.data:
        txt = f"💬 <b>Новый отзыв для проекта {project_name}</b>\n\nВведите текст отзыва:"
    
    await call.message.edit_text(txt, parse_mode="HTML", reply_markup=back_to_panel_kb(p_id))
    await call.answer()

@router.message(ReviewState.waiting_for_text)
async def rev_text(message: Message, state: FSMContext):
    if message.text and message.text.startswith("/"): 
        return 
    
    await state.update_data(txt=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐"*i, callback_data=f"st_{i}")] for i in range(5, 0, -1)
    ])
    
    await state.set_state(ReviewState.waiting_for_rate)
    await message.answer("🌟 <b>Выберите оценку:</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("st_"), ReviewState.waiting_for_rate)
async def rev_end(call: CallbackQuery, state: FSMContext):
    rate = int(call.data.split("_")[1]); data = await state.get_data(); p_id = data['p_id']
    old_rev = supabase.table("user_logs").select("*").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "review").execute()
    p = supabase.table("projects").select("*").eq("id", p_id).single().execute().data
    
    old_score = p['score']
    rating_change = RATING_MAP[rate]
    
    if old_rev.data:
        # Учитываем старую оценку при пересчете
        old_rating_change = RATING_MAP[old_rev.data[0]['rating_val']]
        rating_change = RATING_MAP[rate] - old_rating_change
        new_score = old_score + rating_change
        supabase.table("user_logs").update({"review_text": data['txt'], "rating_val": rate}).eq("id", old_rev.data[0]['id']).execute()
        res_txt = "обновлен"; log_id = old_rev.data[0]['id']
        reason = f"Изменение отзыва: {old_rev.data[0]['rating_val']}/5 → {rate}/5"
    else:
        new_score = old_score + rating_change
        log = supabase.table("user_logs").insert({
            "user_id": call.from_user.id, 
            "project_id": p_id, 
            "action_type": "review", 
            "review_text": data['txt'], 
            "rating_val": rate
        }).execute()
        res_txt = "добавлен"; log_id = log.data[0]['id']
        reason = f"Новый отзыв: {rate}/5"

    supabase.table("projects").update({"score": new_score}).eq("id", p_id).execute()
    
    # Добавляем запись в историю
    supabase.table("rating_history").insert({
        "project_id": p_id,
        "user_id": call.from_user.id,
        "username": call.from_user.username,
        "change_type": "user_review",
        "score_before": old_score,
        "score_after": new_score,
        "change_amount": rating_change,
        "reason": reason,
        "is_admin_action": False,
        "related_review_id": log_id
    }).execute()
    
    await call.message.edit_text(
        f"✅ <b>Отзыв успешно {res_txt}!</b>\n\n"
        f"📊 Изменение рейтинга: <code>{rating_change:+d}</code>\n"
        f"🔢 Новый рейтинг: <b>{new_score}</b>",
        parse_mode="HTML",
        reply_markup=back_to_panel_kb(p_id)
    )
    
    # ФОРМИРУЕМ ЛОГ
    admin_text = (f"📢 <b>Отзыв {res_txt}:</b> {p['name']}\n"
                  f"Пользователь: @{call.from_user.username or call.from_user.id}\n"
                  f"Текст: <i>{data['txt']}</i>\n"
                  f"Оценка: {rate}/5\n"
                  f"📊 Изменение рейтинга: {rating_change:+d}\n"
                  f"🔢 Новый рейтинг: {new_score}\n"
                  f"Удалить: <code>/delrev {log_id}</code>")
    
    # Используем новую функцию для отправки логов
    await send_log_to_topics(admin_text, p['category'])

    await state.clear()
    await call.answer()

@router.callback_query(F.data.startswith("viewrev_"))
async def view_reviews(call: CallbackQuery):
    p_id = call.data.split("_")[1]
    revs = supabase.table("user_logs").select("*").eq("project_id", p_id).eq("action_type", "review").order("created_at", desc=True).limit(5).execute().data
    
    project = supabase.table("projects").select("name").eq("id", p_id).single().execute().data
    project_name = project['name'] if project else "Проект"
    
    if not revs: 
        text = f"<b>💬 ОТЗЫВЫ ПРОЕКТА</b>\n<b>{project_name}</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
        text += "📭 Отзывов еще нет\n"
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_panel_kb(p_id))
        await call.answer()
        return
    
    text = f"<b>💬 ПОСЛЕДНИЕ ОТЗЫВЫ</b>\n<b>{project_name}</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    for r in revs: 
        date = r['created_at'][:10] if r['created_at'] else ""
        stars = '⭐' * r['rating_val']
        text += f"{stars}\n<i>{r['review_text']}</i>\n📅 {date}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_panel_kb(p_id))
    await call.answer()

@router.callback_query(F.data.startswith("history_"))
async def view_history(call: CallbackQuery):
    """Показать историю изменений рейтинга проекта"""
    p_id = call.data.split("_")[1]
    
    # Получаем информацию о проекте
    project = supabase.table("projects").select("*").eq("id", p_id).single().execute().data
    if not project:
        await call.answer("Проект не найден.", show_alert=True)
        return
    
    # Получаем историю изменений
    history = supabase.table("rating_history").select("*")\
        .eq("project_id", p_id)\
        .order("created_at", desc=True)\
        .limit(10)\
        .execute().data
    
    text = f"<b>📊 ИСТОРИЯ ИЗМЕНЕНИЙ</b>\n<b>{project['name']}</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    
    if not history:
        text += "📭 История изменений пуста\n"
    else:
        for i, change in enumerate(history, 1):
            date_time = change['created_at'][:16] if change['created_at'] else ""
            
            if change['is_admin_action']:
                actor = f"👤 Админ: {change['admin_username'] or change['admin_id']}"
            else:
                actor = f"👤 Пользователь: {change['username'] or change['user_id']}"
            
            symbol = "📈" if change['change_amount'] > 0 else "📉" if change['change_amount'] < 0 else "➡️"
            
            text += f"{i}. {symbol} <b>{change['score_before']} → {change['score_after']}</b> ({change['change_amount']:+d})\n"
            text += f"   📝 {change['reason'][:50]}{'...' if len(change['reason']) > 50 else ''}\n"
            text += f"   {actor}\n"
            text += f"   📅 {date_time}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_panel_kb(p_id))
    await call.answer()

@router.callback_query(F.data.startswith("like_"))
async def handle_like(call: CallbackQuery):
    p_id = call.data.split("_")[1]
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "like").execute()
    if check.data: 
        await call.answer("Вы уже поддержали этот проект!", show_alert=True)
        return
    
    # Получаем текущий рейтинг
    project = supabase.table("projects").select("*").eq("id", p_id).single().execute().data
    if not project:
        await call.answer("Проект не найден.", show_alert=True)
        return
    
    old_score = project['score']
    new_score = old_score + 1
    
    # Обновляем рейтинг проекта
    supabase.table("projects").update({"score": new_score}).eq("id", p_id).execute()
    
    # Добавляем лайк в логи
    supabase.table("user_logs").insert({
        "user_id": call.from_user.id, 
        "project_id": p_id, 
        "action_type": "like"
    }).execute()
    
    # Добавляем запись в историю
    supabase.table("rating_history").insert({
        "project_id": p_id,
        "user_id": call.from_user.id,
        "username": call.from_user.username,
        "change_type": "like",
        "score_before": old_score,
        "score_after": new_score,
        "change_amount": 1,
        "reason": "Лайк от пользователя",
        "is_admin_action": False
    }).execute()
    
    # Обновляем панель с новым рейтингом
    recent_changes = supabase.table("rating_history").select("*")\
        .eq("project_id", p_id)\
        .order("created_at", desc=True)\
        .limit(2)\
        .execute().data
    
    text = f"<b>🔘 ПАНЕЛЬ УПРАВЛЕНИЯ</b>\n\n"
    text += f"<b>{project['name']}</b>\n"
    text += f"{project['description']}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    text += f"📊 Текущий рейтинг: <b>{new_score}</b> (+1)\n\n"
    
    if recent_changes:
        text += f"<b>📈 Последние изменения:</b>\n"
        for change in recent_changes:
            date = change['created_at'][:10] if change['created_at'] else ""
            symbol = "📈" if change['change_amount'] > 0 else "📉" if change['change_amount'] < 0 else "➡️"
            text += f"{symbol} <code>{change['change_amount']:+d}</code> — {change['reason'][:50]}... ({date})\n"
        text += f"\n"
    
    text += f"<i>Выберите действие:</i>"
    
    await call.message.edit_text(text, reply_markup=project_panel_kb(p_id), parse_mode="HTML")
    await call.answer("❤️ Голос учтен!")

async def main():
    logging.basicConfig(level=logging.INFO)
    dp.update.outer_middleware(AccessMiddleware())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
