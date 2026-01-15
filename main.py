import asyncio
import os
import logging
from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, FSInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from supabase import create_client, Client
from dotenv import load_dotenv
from datetime import datetime, timedelta
from html import escape
import uuid
import threading

# --- НАСТРОЙКИ ТОПИКОВ ---
TOPIC_LOGS_ALL = 46
TOPICS_BY_CATEGORY = {
    "support_bots": 38,
    "support_admins": 41,
    "lot_channels": 39,
    "check_channels": 42,
    "kmbp_channels": 40
}

# --- ИНИЦИАЛИЗАЦИЯ ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_GROUP_ID = int(os.getenv("ADMIN_CHAT_ID", 0))

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
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

class EditProjectState(StatesGroup):
    waiting_for_description = State()
    waiting_for_photo = State()

class SearchState(StatesGroup):
    waiting_for_query = State()

# --- СИСТЕМА РЕФЕРАЛОВ ---
class ReferralState(StatesGroup):
    waiting_for_referral_code = State()

# --- ПРОВЕРКА ПРАВ ---
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
        
        if not user or user.is_bot:
            return await handler(event, data)
        
        if await is_user_admin(user.id):
            return await handler(event, data)
        
        try:
            res = supabase.table("banned_users")\
                .select("user_id, reason")\
                .eq("user_id", user.id)\
                .execute()
            
            if res.data:
                if isinstance(event, Message):
                    await event.answer(
                        f"🚫 Вы заблокированы!\n"
                        f"📝 Причина: {res.data[0].get('reason', 'Не указана')}\n\n"
                        f"Для разблокировки обратитесь к администратору.",
                        parse_mode="HTML"
                    )
                elif isinstance(event, CallbackQuery):
                    await event.answer(
                        "🚫 Вы заблокированы!",
                        show_alert=True
                    )
                return
        
        except Exception as e:
            logging.error(f"Ошибка проверки бана: {e}")
        
        return await handler(event, data)

# --- КЛАВИАТУРЫ ---
def main_kb():
    buttons = [
        [KeyboardButton(text=v) for v in list(CATEGORIES.values())[:2]],
        [KeyboardButton(text=v) for v in list(CATEGORIES.values())[2:5]],
        [
            KeyboardButton(text="Поиск проекта"),
            KeyboardButton(text="Топ недели"),
            KeyboardButton(text="Топ месяца")
        ],
        [
            KeyboardButton(text="Реферальная система"),
            KeyboardButton(text="Мой прогресс")
        ]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отмена")]],
        resize_keyboard=True
    )

def back_to_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Назад в меню")]],
        resize_keyboard=True
    )

def project_card_kb(p_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть панель", callback_data=f"panel_{p_id}")]
    ])

def project_panel_kb(p_id, has_review=False):
    buttons = [
        [
            InlineKeyboardButton(text="⭐ Оценить", callback_data=f"rev_{p_id}"),
            InlineKeyboardButton(text="❤️ Поддержать", callback_data=f"like_{p_id}")
        ],
        [
            InlineKeyboardButton(text="💬 Отзывы", callback_data=f"viewrev_{p_id}"),
            InlineKeyboardButton(text="📊 История", callback_data=f"history_{p_id}")
        ]
    ]
    
    if has_review:
        buttons.append([InlineKeyboardButton(text="✏️ Изменить мой отзыв", callback_data=f"myreview_{p_id}")])
    
    buttons.append([InlineKeyboardButton(text="❌ Закрыть панель", callback_data="close_panel")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def back_to_panel_kb(p_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к панели", callback_data=f"panel_{p_id}")]
    ])

def rating_kb():
    buttons = [
        [InlineKeyboardButton(text="⭐" * i, callback_data=f"st_{i}")] for i in range(5, 0, -1)
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад к тексту", callback_data="back_to_text")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def pagination_kb(category_key, offset, has_next=True):
    buttons = []
    if has_next:
        callback_data = f"more_{category_key}_{offset}"
        buttons.append([InlineKeyboardButton(text="📜 Показать еще", callback_data=callback_data)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def referral_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Получить реф. ссылку", callback_data="get_referral")],
        [InlineKeyboardButton(text="📋 Ввести реф. код", callback_data="enter_referral")],
        [InlineKeyboardButton(text="📊 Мои рефералы", callback_data="my_referrals")]
    ])

# --- ФУНКЦИЯ ОТПРАВКИ ЛОГОВ ---
async def send_log_to_topics(admin_text: str, category: str = None):
    try:
        if TOPIC_LOGS_ALL:
            await bot.send_message(
                ADMIN_GROUP_ID,
                admin_text,
                message_thread_id=TOPIC_LOGS_ALL,
                parse_mode="HTML"
            )
        
        if category:
            cat_topic = TOPICS_BY_CATEGORY.get(category)
            if cat_topic:
                await bot.send_message(
                    ADMIN_GROUP_ID,
                    admin_text,
                    message_thread_id=cat_topic,
                    parse_mode="HTML"
                )
        
        elif not TOPIC_LOGS_ALL and ADMIN_GROUP_ID:
            await bot.send_message(ADMIN_GROUP_ID, admin_text, parse_mode="HTML")
            
    except Exception as e:
        logging.error(f"Ошибка отправки лога: {e}")

# --- РЕФЕРАЛЬНАЯ СИСТЕМА ---
async def generate_referral_code(user_id: int) -> str:
    """Генерация уникального реферального кода"""
    code = str(uuid.uuid4())[:8].upper()
    
    # Проверяем уникальность
    result = supabase.table("referrals")\
        .select("code")\
        .eq("code", code)\
        .execute()
    
    if not result.data:
        # Сохраняем код
        supabase.table("referrals").insert({
            "user_id": user_id,
            "code": code,
            "created_at": "now()"
        }).execute()
        return code
    else:
        # Если код существует, генерируем новый
        return await generate_referral_code(user_id)

async def get_user_referral_code(user_id: int) -> str:
    """Получить реферальный код пользователя"""
    result = supabase.table("referrals")\
        .select("code")\
        .eq("user_id", user_id)\
        .execute()
    
    if result.data:
        return result.data[0]['code']
    else:
        return await generate_referral_code(user_id)

async def process_referral(inviter_id: int, referred_id: int, referral_code: str):
    """Обработка реферала"""
    try:
        # Проверяем, не активировал ли уже пользователь реферал
        existing = supabase.table("referral_logs")\
            .select("*")\
            .eq("referred_user_id", referred_id)\
            .execute()
        
        if existing.data:
            return False, "Вы уже активировали реферальный код ранее"
        
        # Проверяем, не является ли пользователь самим собой
        if inviter_id == referred_id:
            return False, "Нельзя использовать собственный реферальный код"
        
        # Находим пользователя по коду
        code_result = supabase.table("referrals")\
            .select("user_id")\
            .eq("code", referral_code)\
            .execute()
        
        if not code_result.data:
            return False, "Неверный реферальный код"
        
        inviter_id_from_code = code_result.data[0]['user_id']
        
        # Логируем активацию
        supabase.table("referral_logs").insert({
            "inviter_id": inviter_id_from_code,
            "referred_user_id": referred_id,
            "referral_code": referral_code,
            "activated_at": "now()"
        }).execute()
        
        # Обновляем статистику пригласившего
        inviter_stats = supabase.table("user_stats")\
            .select("*")\
            .eq("user_id", inviter_id_from_code)\
            .execute()
        
        if inviter_stats.data:
            # Увеличиваем счетчик рефералов
            supabase.table("user_stats")\
                .update({"referral_count": inviter_stats.data[0]['referral_count'] + 1})\
                .eq("user_id", inviter_id_from_code)\
                .execute()
        else:
            # Создаем запись статистики
            supabase.table("user_stats").insert({
                "user_id": inviter_id_from_code,
                "referral_count": 1,
                "reviews_count": 0,
                "likes_count": 0
            }).execute()
        
        # Создаем запись для приглашенного
        referred_stats = supabase.table("user_stats")\
            .select("*")\
            .eq("user_id", referred_id)\
            .execute()
        
        if not referred_stats.data:
            supabase.table("user_stats").insert({
                "user_id": referred_id,
                "referral_count": 0,
                "reviews_count": 0,
                "likes_count": 0
            }).execute()
        
        # Отправляем уведомление в логи
        inviter_info = await bot.get_chat(inviter_id_from_code)
        referred_info = await bot.get_chat(referred_id)
        
        log_text = (
            f"👥 <b>НОВЫЙ РЕФЕРАЛ!</b>\n\n"
            f"👤 Пригласил: @{inviter_info.username or inviter_info.id} (ID: {inviter_id_from_code})\n"
            f"👤 Приглашенный: @{referred_info.username or referred_info.id} (ID: {referred_id})\n"
            f"🔗 Код: <code>{referral_code}</code>\n"
            f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        await send_log_to_topics(log_text)
        
        return True, "Реферальный код успешно активирован!"
        
    except Exception as e:
        logging.error(f"Ошибка обработки реферала: {e}")
        return False, "Ошибка при активации кода"

async def get_user_stats(user_id: int):
    """Получить статистику пользователя"""
    result = supabase.table("user_stats")\
        .select("*")\
        .eq("user_id", user_id)\
        .execute()
    
    if result.data:
        return result.data[0]
    else:
        # Создаем пустую статистику
        supabase.table("user_stats").insert({
            "user_id": user_id,
            "referral_count": 0,
            "reviews_count": 0,
            "likes_count": 0
        }).execute()
        return {"user_id": user_id, "referral_count": 0, "reviews_count": 0, "likes_count": 0}

async def update_user_stats(user_id: int, field: str):
    """Обновить статистику пользователя"""
    stats = await get_user_stats(user_id)
    current_value = stats.get(field, 0)
    
    supabase.table("user_stats")\
        .update({field: current_value + 1})\
        .eq("user_id", user_id)\
        .execute()

# --- СИСТЕМА НЕДЕЛЬНОГО РЕЙТИНГА ---
async def get_weekly_top(limit: int = 10):
    """Получить топ проектов за неделю"""
    try:
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()
        
        # Сначала получаем все изменения за неделю
        result = supabase.table("rating_history")\
            .select("project_id, change_amount")\
            .gte("created_at", week_ago)\
            .execute()
        
        if not result.data:
            return []
        
        # Группируем вручную в Python
        changes_by_project = {}
        for item in result.data:
            project_id = item['project_id']
            change_amount = item['change_amount'] or 0
            if project_id in changes_by_project:
                changes_by_project[project_id] += change_amount
            else:
                changes_by_project[project_id] = change_amount
        
        # Сортируем по изменению
        sorted_projects = sorted(changes_by_project.items(), key=lambda x: x[1], reverse=True)[:limit]
        
        top_projects = []
        for project_id, total_change in sorted_projects:
            project_result = supabase.table("projects")\
                .select("*")\
                .eq("id", project_id)\
                .execute()
            
            if project_result.data:
                project = project_result.data[0]
                project['weekly_change'] = total_change
                top_projects.append(project)
        
        return top_projects
        
    except Exception as e:
        logging.error(f"Ошибка получения топа недели: {e}")
        return []

# --- СИСТЕМА МЕСЯЧНОГО РЕЙТИНГА ---
async def get_monthly_top(limit: int = 10):
    """Получить топ проектов за месяц"""
    try:
        month_ago = (datetime.now() - timedelta(days=30)).isoformat()
        
        # Сначала получаем все изменения за месяц
        result = supabase.table("rating_history")\
            .select("project_id, change_amount")\
            .gte("created_at", month_ago)\
            .execute()
        
        if not result.data:
            return []
        
        # Группируем вручную в Python
        changes_by_project = {}
        for item in result.data:
            project_id = item['project_id']
            change_amount = item['change_amount'] or 0
            if project_id in changes_by_project:
                changes_by_project[project_id] += change_amount
            else:
                changes_by_project[project_id] = change_amount
        
        # Сортируем по изменению
        sorted_projects = sorted(changes_by_project.items(), key=lambda x: x[1], reverse=True)[:limit]
        
        top_projects = []
        for project_id, total_change in sorted_projects:
            project_result = supabase.table("projects")\
                .select("*")\
                .eq("id", project_id)\
                .execute()
            
            if project_result.data:
                project = project_result.data[0]
                project['monthly_change'] = total_change
                top_projects.append(project)
        
        return top_projects
        
    except Exception as e:
        logging.error(f"Ошибка получения топа месяца: {e}")
        return []

async def get_weekly_leaders(limit: int = 10):
    """Получить лидеров недели (пользователей)"""
    try:
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()
        
        # Получаем активность пользователей за неделю
        result = supabase.table("rating_history")\
            .select("user_id, username, change_amount")\
            .gte("created_at", week_ago)\
            .not_.is_("user_id", None)\
            .execute()
        
        if not result.data:
            return []
        
        # Группируем вручную
        impact_by_user = {}
        for item in result.data:
            user_id = item['user_id']
            change_amount = item['change_amount'] or 0
            username = item['username']
            
            if user_id in impact_by_user:
                impact_by_user[user_id]['impact'] += change_amount
            else:
                impact_by_user[user_id] = {
                    'user_id': user_id,
                    'username': username,
                    'impact': change_amount
                }
        
        # Сортируем по влиянию
        leaders = sorted(impact_by_user.values(), key=lambda x: x['impact'], reverse=True)[:limit]
        
        return leaders
        
    except Exception as e:
        logging.error(f"Ошибка получения лидеров недели: {e}")
        return []

async def get_monthly_leaders(limit: int = 10):
    """Получить лидеров месяца (пользователей)"""
    try:
        month_ago = (datetime.now() - timedelta(days=30)).isoformat()
        
        # Получаем активность пользователей за месяц
        result = supabase.table("rating_history")\
            .select("user_id, username, change_amount")\
            .gte("created_at", month_ago)\
            .not_.is_("user_id", None)\
            .execute()
        
        if not result.data:
            return []
        
        # Группируем вручную
        impact_by_user = {}
        for item in result.data:
            user_id = item['user_id']
            change_amount = item['change_amount'] or 0
            username = item['username']
            
            if user_id in impact_by_user:
                impact_by_user[user_id]['impact'] += change_amount
            else:
                impact_by_user[user_id] = {
                    'user_id': user_id,
                    'username': username,
                    'impact': change_amount
                }
        
        # Сортируем по влиянию
        leaders = sorted(impact_by_user.values(), key=lambda x: x['impact'], reverse=True)[:limit]
        
        return leaders
        
    except Exception as e:
        logging.error(f"Ошибка получения лидеров месяца: {e}")
        return []

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
async def safe_edit_message(call: CallbackQuery, text: str, reply_markup=None, parse_mode="HTML"):
    try:
        await call.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        if "message is not modified" in str(e):
            await call.answer()
        else:
            logging.error(f"Ошибка редактирования сообщения: {e}")
            try:
                await call.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
            except Exception as e2:
                logging.error(f"Ошибка отправки сообщения: {e2}")
                await call.answer()

async def safe_edit_media(call: CallbackQuery, caption: str, reply_markup=None, parse_mode="HTML"):
    try:
        await call.message.edit_caption(caption=caption, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        if "message is not modified" in str(e):
            await call.answer()
        else:
            logging.error(f"Ошибка редактирования медиа: {e}")
            try:
                await call.message.answer(caption, reply_markup=reply_markup, parse_mode=parse_mode)
            except Exception as e2:
                logging.error(f"Ошибка отправки сообщения: {e2}")
                await call.answer()

async def get_project_photo(project_id: int):
    try:
        result = supabase.table("project_photos").select("*").eq("project_id", project_id).execute()
        if result.data:
            return result.data[0].get('photo_file_id', '')
    except Exception as e:
        logging.error(f"Ошибка получения фото: {e}")
    return None

async def save_project_photo(project_id: int, photo_file_id: str, admin_id: int):
    try:
        supabase.table("project_photos").upsert({
            "project_id": project_id,
            "photo_file_id": photo_file_id,
            "updated_by": admin_id,
            "updated_at": "now()"
        }).execute()
        return True
    except Exception as e:
        logging.error(f"Ошибка сохранения фото: {e}")
        return False

async def find_project_by_name(name: str):
    try:
        result = supabase.table("projects").select("*").ilike("name", f"%{name}%").execute()
        if result.data:
            return result.data[0]
    except Exception as e:
        logging.error(f"Ошибка поиска проекта: {e}")
    return None

async def find_project_by_id(project_id: int):
    try:
        result = supabase.table("projects").select("*").eq("id", project_id).execute()
        if result.data:
            return result.data[0]
    except Exception as e:
        logging.error(f"Ошибка поиска проекта по ID: {e}")
    return None

async def show_projects_batch(category_key, offset, message_or_call, is_first_batch=False):
    projects_per_batch = 5
    
    data = supabase.table("projects")\
        .select("*")\
        .eq("category", category_key)\
        .order("score", desc=True)\
        .range(offset, offset + projects_per_batch - 1)\
        .execute().data
    
    count_result = supabase.table("projects")\
        .select("*", count="exact")\
        .eq("category", category_key)\
        .execute()
    
    total_projects = count_result.count if hasattr(count_result, 'count') else 0
    
    if not data:
        if is_first_batch:
            category_name = CATEGORIES[category_key]
            text = f"📭 В разделе <b>'{escape(category_name)}'</b> пока нет проектов."
            
            if isinstance(message_or_call, CallbackQuery):
                await safe_edit_message(message_or_call, text)
            else:
                await message_or_call.answer(text, parse_mode="HTML")
        else:
            if isinstance(message_or_call, CallbackQuery):
                await message_or_call.answer("Больше проектов нет", show_alert=True)
        return
    
    if is_first_batch:
        category_name = CATEGORIES[category_key]
        text = f"<b>{escape(category_name)}</b>\n"
        text += f"Всего проектов: {total_projects}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
        
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.message.answer(text, parse_mode="HTML")
        else:
            await message_or_call.answer(text, parse_mode="HTML")
    
    for p in data:
        photo_file_id = await get_project_photo(p['id'])
        
        project_name_escaped = escape(str(p['name']))
        description_escaped = escape(str(p['description']))
        
        card = f"<b>{project_name_escaped}</b>\n\n{description_escaped[:150]}{'...' if len(p['description']) > 150 else ''}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        card += f"📊 Текущий рейтинг: <b>{p['score']}</b>\n\n"
        card += f"<i>Нажмите кнопку ниже для управления проектом</i>"
        
        if isinstance(message_or_call, CallbackQuery):
            if photo_file_id:
                try:
                    await message_or_call.message.answer_photo(
                        photo=photo_file_id,
                        caption=card,
                        reply_markup=project_card_kb(p['id']),
                        parse_mode="HTML"
                    )
                except:
                    await message_or_call.message.answer(card, reply_markup=project_card_kb(p['id']), parse_mode="HTML")
            else:
                await message_or_call.message.answer(card, reply_markup=project_card_kb(p['id']), parse_mode="HTML")
        else:
            if photo_file_id:
                try:
                    await message_or_call.answer_photo(
                        photo=photo_file_id,
                        caption=card,
                        reply_markup=project_card_kb(p['id']),
                        parse_mode="HTML"
                    )
                except:
                    await message_or_call.answer(card, reply_markup=project_card_kb(p['id']), parse_mode="HTML")
            else:
                await message_or_call.answer(card, reply_markup=project_card_kb(p['id']), parse_mode="HTML")
    
    has_next = offset + projects_per_batch < total_projects
    
    if is_first_batch and has_next:
        kb = pagination_kb(category_key, offset + projects_per_batch, has_next)
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.message.answer("⬇️ <b>Показано:</b> <code>{}-{}</code> из <code>{}</code> проектов".format(
                offset + 1, min(offset + projects_per_batch, total_projects), total_projects
            ), reply_markup=kb, parse_mode="HTML")
        else:
            await message_or_call.answer("⬇️ <b>Показано:</b> <code>{}-{}</code> из <code>{}</code> проектов".format(
                offset + 1, min(offset + projects_per_batch, total_projects), total_projects
            ), reply_markup=kb, parse_mode="HTML")
    elif isinstance(message_or_call, CallbackQuery) and not is_first_batch:
        new_offset = offset + projects_per_batch
        new_has_next = new_offset < total_projects
        
        try:
            await message_or_call.message.delete()
        except:
            pass
            
        if new_has_next:
            kb = pagination_kb(category_key, new_offset, new_has_next)
            await message_or_call.message.answer("⬇️ <b>Показано:</b> <code>{}-{}</code> из <code>{}</code> проектов".format(
                offset + projects_per_batch + 1, min(new_offset + projects_per_batch, total_projects), total_projects
            ), reply_markup=kb, parse_mode="HTML")
        else:
            await message_or_call.message.answer("✅ <b>Показаны все проекты</b>\nВсего проектов: <code>{}</code>".format(total_projects), parse_mode="HTML")

# --- НОВЫЕ КОМАНДЫ: ТОП НЕДЕЛИ И МЕСЯЦА ---
@router.message(F.text == "⭐ Топ недели")
async def weekly_top_command(message: Message):
    """Показать топ проектов недели"""
    top_projects = await get_weekly_top(10)
    
    if not top_projects:
        await message.answer(
            "📊 <b>ТОП НЕДЕЛИ (ПРОЕКТЫ)</b>\n\n"
            "Пока недостаточно данных для формирования топа.\n"
            "Начните оценивать проекты, и скоро здесь появятся лидеры!",
            parse_mode="HTML"
        )
        return
    
    text = f"<b>⭐ ТОП ПРОЕКТОВ НЕДЕЛИ</b>\n\n"
    text += f"📅 Период: последние 7 дней\n"
    text += f"📊 Рейтинг основан на изменении баллов за неделю\n"
    text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    
    for i, project in enumerate(top_projects, 1):
        change = project.get('weekly_change', 0)
        change_symbol = "📈" if change > 0 else "📉" if change < 0 else "➡️"
        
        project_name_escaped = escape(str(project['name']))
        category_escaped = escape(str(CATEGORIES.get(project['category'], project['category'])))
        
        text += f"<b>{i}. {project_name_escaped}</b>\n"
        text += f"📂 Категория: {category_escaped}\n"
        text += f"🔢 Текущий рейтинг: <b>{project['score']}</b>\n"
        text += f"{change_symbol} Изменение за неделю: <code>{change:+d}</code>\n"
        text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    
    # Добавляем лидеров недели (пользователей)
    user_leaders = await get_weekly_leaders(5)
    if user_leaders:
        text += f"\n<b>🏆 ЛИДЕРЫ НЕДЕЛИ (ПОЛЬЗОВАТЕЛИ):</b>\n"
        for i, leader in enumerate(user_leaders, 1):
            username = escape(str(leader.get('username', 'Аноним')))
            impact = leader.get('impact', 0)
            text += f"{i}. @{username} — <code>{impact:+d}</code> баллов влияния\n"
    
    text += f"\n<i>Топ обновляется автоматически каждую неделю</i>"
    
    await message.answer(text, parse_mode="HTML")

@router.message(F.text == "📊 Топ месяца")
async def monthly_top_command(message: Message):
    """Показать топ проектов месяца"""
    top_projects = await get_monthly_top(10)
    
    if not top_projects:
        await message.answer(
            "📊 <b>ТОП МЕСЯЦА (ПРОЕКТЫ)</b>\n\n"
            "Пока недостаточно данных для формирования топа.\n"
            "Начните оценивать проекты, и скоро здесь появятся лидеры!",
            parse_mode="HTML"
        )
        return
    
    text = f"<b>📊 ТОП ПРОЕКТОВ МЕСЯЦА</b>\n\n"
    text += f"📅 Период: последние 30 дней\n"
    text += f"📊 Рейтинг основан на изменении баллов за месяц\n"
    text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    
    for i, project in enumerate(top_projects, 1):
        change = project.get('monthly_change', 0)
        change_symbol = "📈" if change > 0 else "📉" if change < 0 else "➡️"
        
        project_name_escaped = escape(str(project['name']))
        category_escaped = escape(str(CATEGORIES.get(project['category'], project['category'])))
        
        text += f"<b>{i}. {project_name_escaped}</b>\n"
        text += f"📂 Категория: {category_escaped}\n"
        text += f"🔢 Текущий рейтинг: <b>{project['score']}</b>\n"
        text += f"{change_symbol} Изменение за месяц: <code>{change:+d}</code>\n"
        text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    
    # Добавляем лидеров месяца (пользователей)
    user_leaders = await get_monthly_leaders(5)
    if user_leaders:
        text += f"\n<b>🏆 ЛИДЕРЫ МЕСЯЦА (ПОЛЬЗОВАТЕЛИ):</b>\n"
        for i, leader in enumerate(user_leaders, 1):
            username = escape(str(leader.get('username', 'Аноним')))
            impact = leader.get('impact', 0)
            text += f"{i}. @{username} — <code>{impact:+d}</code> баллов влияния\n"
    
    text += f"\n<i>Топ обновляется автоматически каждый месяц</i>"
    
    await message.answer(text, parse_mode="HTML")

# --- РЕФЕРАЛЬНАЯ СИСТЕМА - КОМАНДЫ ---
@router.message(F.text == "👥 Реферальная система")
async def referral_system_menu(message: Message):
    """Меню реферальной системы"""
    text = (
        "<b>👥 РЕФЕРАЛЬНАЯ СИСТЕМА</b>\n\n"
        "Приглашайте друзей и получайте бонусы!\n\n"
        "✨ <b>Как это работает:</b>\n"
        "1. Получите свою реферальную ссылку\n"
        "2. Отправьте ее друзьям\n"
        "3. Когда друг активирует ваш код, вы получите уведомление\n"
        "4. Следите за своим рейтингом в таблице лидеров\n\n"
        "🎁 <b>Преимущества:</b>\n"
        "• Ваше имя в таблице лидеров\n"
        "• Уведомление о каждом приглашенном друге\n"
        "• Дополнительный статус в сообществе"
    )
    
    await message.answer(text, reply_markup=referral_kb(), parse_mode="HTML")

@router.callback_query(F.data == "get_referral")
async def get_referral_link(call: CallbackQuery):
    """Получить реферальную ссылку"""
    user_id = call.from_user.id
    code = await get_user_referral_code(user_id)
    
    referral_link = f"https://t.me/{(await bot.me()).username}?start=ref_{code}"
    
    text = (
        f"<b>🔗 ВАША РЕФЕРАЛЬНАЯ ССЫЛКА</b>\n\n"
        f"📋 <b>Код:</b> <code>{code}</code>\n"
        f"🔗 <b>Ссылка:</b> {referral_link}\n\n"
        f"<i>Отправьте эту ссылку другу. Когда он перейдет по ней и начнет использовать бота, вы получите уведомление!</i>\n\n"
        f"📊 <b>Статистика:</b>\n"
    )
    
    stats = await get_user_stats(user_id)
    text += f"• Приглашено друзей: <b>{stats['referral_count']}</b>\n"
    
    # Получаем список рефералов
    referrals_result = supabase.table("referral_logs")\
        .select("referred_user_id, activated_at")\
        .eq("inviter_id", user_id)\
        .order("activated_at", desc=True)\
        .execute()
    
    if referrals_result.data:
        text += f"\n<b>📋 ПОСЛЕДНИЕ РЕФЕРАЛЫ:</b>\n"
        for i, ref in enumerate(referrals_result.data[:5], 1):
            date = ref['activated_at'][:10] if ref['activated_at'] else "Неизвестно"
            text += f"{i}. ID: <code>{ref['referred_user_id']}</code> — {date}\n"
    
    await safe_edit_message(call, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Ввести реф. код", callback_data="enter_referral")],
        [InlineKeyboardButton(text="📊 Мои рефералы", callback_data="my_referrals")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_referral_menu")]
    ]))
    
    await call.answer()

@router.callback_query(F.data == "enter_referral")
async def enter_referral_code(call: CallbackQuery, state: FSMContext):
    """Ввод реферального кода"""
    await state.set_state(ReferralState.waiting_for_referral_code)
    
    text = (
        "<b>📋 ВВОД РЕФЕРАЛЬНОГО КОДА</b>\n\n"
        "Введите реферальный код, который вам дал друг:\n\n"
        "<i>Код должен состоять из 8 символов (буквы и цифры)</i>"
    )
    
    await safe_edit_message(call, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_referral_menu")]
    ]))
    
    await call.answer()

@router.message(ReferralState.waiting_for_referral_code)
async def process_referral_code(message: Message, state: FSMContext):
    """Обработка введенного реферального кода"""
    referral_code = message.text.strip().upper()
    
    if len(referral_code) != 8:
        await message.answer(
            "❌ <b>Неверный формат кода!</b>\n\n"
            "Реферальный код должен состоять из 8 символов.\n"
            "Попробуйте еще раз или нажмите 'Отмена'.",
            parse_mode="HTML"
        )
        return
    
    success, result_message = await process_referral(
        inviter_id=0,  # Будет найден по коду
        referred_id=message.from_user.id,
        referral_code=referral_code
    )
    
    if success:
        await message.answer(
            f"✅ <b>{result_message}</b>\n\n"
            f"Теперь вы в реферальной сети!\n"
            f"Вы также можете приглашать друзей и получать уведомления.",
            parse_mode="HTML",
            reply_markup=main_kb()
        )
    else:
        await message.answer(
            f"❌ <b>{result_message}</b>\n\n"
            f"Попробуйте другой код или обратитесь к тому, кто дал вам этот код.",
            parse_mode="HTML",
            reply_markup=main_kb()
        )
    
    await state.clear()

@router.callback_query(F.data == "my_referrals")
async def show_my_referrals(call: CallbackQuery):
    """Показать моих рефералов"""
    user_id = call.from_user.id
    
    referrals_result = supabase.table("referral_logs")\
        .select("referred_user_id, activated_at")\
        .eq("inviter_id", user_id)\
        .order("activated_at", desc=True)\
        .execute()
    
    stats = await get_user_stats(user_id)
    
    text = f"<b>📊 МОИ РЕФЕРАЛЫ</b>\n\n"
    text += f"👥 Всего приглашено: <b>{stats['referral_count']}</b>\n"
    text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    
    if referrals_result.data:
        text += f"<b>📋 СПИСОК РЕФЕРАЛОВ:</b>\n"
        for i, ref in enumerate(referrals_result.data, 1):
            date = ref['activated_at'][:10] if ref['activated_at'] else "Неизвестно"
            text += f"{i}. ID: <code>{ref['referred_user_id']}</code> — {date}\n"
        
        if len(referrals_result.data) > 10:
            text += f"\n<i>Показано {len(referrals_result.data)} из {stats['referral_count']} рефералов</i>"
    else:
        text += "📭 У вас еще нет рефералов.\n"
        text += "Пригласите друзей, чтобы они появились здесь!"
    
    await safe_edit_message(call, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Получить реф. ссылку", callback_data="get_referral")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_referral_menu")]
    ]))
    
    await call.answer()

@router.callback_query(F.data == "back_to_referral_menu")
async def back_to_referral_menu(call: CallbackQuery):
    """Возврат в меню реферальной системы"""
    text = (
        "<b>👥 РЕФЕРАЛЬНАЯ СИСТЕМА</b>\n\n"
        "Приглашайте друзей и получайте бонусы!\n\n"
        "✨ <b>Как это работает:</b>\n"
        "1. Получите свою реферальную ссылку\n"
        "2. Отправьте ее друзьям\n"
        "3. Когда друг активирует ваш код, вы получите уведомление\n"
        "4. Следите за своим рейтингом в таблице лидеров"
    )
    
    await safe_edit_message(call, text, reply_markup=referral_kb())
    await call.answer()

@router.message(F.text == "📈 Мой прогресс")
async def my_progress(message: Message):
    """Показать прогресс пользователя"""
    user_id = message.from_user.id
    stats = await get_user_stats(user_id)
    
    # Получаем активность пользователя
    user_activity = supabase.table("rating_history")\
        .select("change_amount, created_at, reason")\
        .eq("user_id", user_id)\
        .order("created_at", desc=True)\
        .limit(10)\
        .execute()
    
    # Получаем место в недельном рейтинге
    weekly_leaders = await get_weekly_leaders(100)
    weekly_position = None
    weekly_impact = 0
    
    for i, leader in enumerate(weekly_leaders, 1):
        if leader['user_id'] == user_id:
            weekly_position = i
            weekly_impact = leader.get('impact', 0)
            break
    
    # Получаем место в месячном рейтинге
    monthly_leaders = await get_monthly_leaders(100)
    monthly_position = None
    monthly_impact = 0
    
    for i, leader in enumerate(monthly_leaders, 1):
        if leader['user_id'] == user_id:
            monthly_position = i
            monthly_impact = leader.get('impact', 0)
            break
    
    text = f"<b>📈 ВАШ ПРОГРЕСС</b>\n\n"
    text += f"👤 ID: <code>{user_id}</code>\n"
    text += f"📛 Имя: {message.from_user.first_name or ''} {message.from_user.last_name or ''}\n"
    text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    
    text += f"<b>📊 ОБЩАЯ СТАТИСТИКА:</b>\n"
    text += f"• 👥 Приглашено друзей: <b>{stats['referral_count']}</b>\n"
    text += f"• 💬 Оставлено отзывов: <b>{stats.get('reviews_count', 0)}</b>\n"
    text += f"• ❤️ Поставлено лайков: <b>{stats.get('likes_count', 0)}</b>\n\n"
    
    if weekly_position:
        text += f"<b>⭐ НЕДЕЛЬНЫЙ РЕЙТИНГ:</b>\n"
        text += f"• 🏆 Место: <b>{weekly_position}</b>\n"
        text += f"• 📈 Влияние: <code>{weekly_impact:+d}</code> баллов\n\n"
    else:
        text += f"<b>⭐ НЕДЕЛЬНЫЙ РЕЙТИНГ:</b>\n"
        text += f"• 📊 Вы еще не в рейтинге этой недели\n\n"
    
    if monthly_position:
        text += f"<b>📊 МЕСЯЧНЫЙ РЕЙТИНГ:</b>\n"
        text += f"• 🏆 Место: <b>{monthly_position}</b>\n"
        text += f"• 📈 Влияние: <code>{monthly_impact:+d}</code> баллов\n\n"
    else:
        text += f"<b>📊 МЕСЯЧНЫЙ РЕЙТИНГ:</b>\n"
        text += f"• 📊 Вы еще не в рейтинге этого месяца\n\n"
    
    if user_activity.data:
        text += f"<b>📝 ПОСЛЕДНИЕ ДЕЙСТВИЯ:</b>\n"
        for i, activity in enumerate(user_activity.data[:5], 1):
            date = activity['created_at'][:10] if activity['created_at'] else ""
            reason = escape(str(activity['reason']))
            change = activity['change_amount']
            symbol = "📈" if change > 0 else "📉" if change < 0 else "➡️"
            
            text += f"{i}. {symbol} <code>{change:+d}</code> — {reason[:30]}... ({date})\n"
    
    text += f"\n<i>Продолжайте участвовать в жизни сообщества!</i>"
    
    await message.answer(text, parse_mode="HTML")

# --- ОБНОВЛЕННЫЙ START ДЛЯ РЕФЕРАЛЬНОЙ СИСТЕМЫ ---
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    
    # Проверяем бан
    ban_result = supabase.table("banned_users") \
        .select("*") \
        .eq("user_id", message.from_user.id) \
        .execute()
    
    if ban_result.data:
        reason_escaped = escape(str(ban_result.data[0].get('reason', 'Не указана')))
        await message.answer(
            f"🚫 <b>Вы заблокированы!</b>\n\n"
            f"📝 Причина: <i>{reason_escaped}</i>\n"
            f"📅 Дата блокировки: {ban_result.data[0].get('banned_at', 'Неизвестно')[:10]}\n\n"
            f"Для разблокировки обратитесь к администратору.",
            parse_mode="HTML"
        )
        return
    
    # Проверяем реферальный код в команде
    referral_code = None
    if len(message.text.split()) > 1:
        arg = message.text.split()[1]
        if arg.startswith("ref_"):
            referral_code = arg[4:]  # Убираем "ref_"
    
    # Обработка реферального кода
    if referral_code and len(referral_code) == 8:
        success, result_message = await process_referral(
            inviter_id=0,
            referred_id=message.from_user.id,
            referral_code=referral_code.upper()
        )
        
        if success:
            await message.answer(
                f"✅ <b>Реферальный код активирован!</b>\n\n"
                f"{result_message}",
                parse_mode="HTML"
            )
    
    # Получаем топ проектов
    top_projects = supabase.table("projects") \
        .select("*") \
        .order("score", desc=True) \
        .limit(5) \
        .execute().data

    start_text = "<b>🌟 ДОБРО ПОЖАЛОВАТЬ В РЕЙТИНГ ПРОЕКТОВ КМБП!</b>\n\n"
    start_text += "Здесь вы можете оценивать проекты, оставлять отзывы и следить за рейтингом лучших проектов сообщества.\n\n"

    if top_projects:
        start_text += "<b>🏆 ТОП-5 ПРОЕКТОВ:</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        for i, p in enumerate(top_projects, 1):
            project_name_escaped = escape(str(p['name']))
            start_text += f"{i}. <b>{project_name_escaped}</b> — <code>{p['score']}</code>\n"
    else:
        start_text += "<b>🏆 ТОП-5 ПРОЕКТОВ:</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        start_text += "Список пуст. Будьте первым, кто добавит проект!\n"

    start_text += "\n<b>✨ НОВЫЕ ВОЗМОЖНОСТИ:</b>\n"
    start_text += "• 📊 <b>Топ недели</b> - лучшие проекты за 7 дней\n"
    start_text += "• 📈 <b>Топ месяца</b> - лидеры за 30 дней\n"
    start_text += "• 👥 <b>Реферальная система</b> - приглашайте друзей\n"
    start_text += "• 📈 <b>Мой прогресс</b> - следите за своей активностью\n\n"
    
    start_text += "<i>Нажмите на категорию ниже, чтобы увидеть все проекты</i>"
    start_text += "\n<b><i>Партнеры KMBP Monthly Awards Season 1</i></b>"
    start_text += "\n✴ @The_infernal_paradise_bot"

    try:
        photo = FSInputFile("start_photo.jpg")
        await message.answer_photo(
            photo=photo,
            caption=start_text,
            reply_markup=main_kb(),
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"Ошибка отправки фото: {e}")
        await message.answer(start_text, reply_markup=main_kb(), parse_mode="HTML")

# --- ОБНОВЛЕННЫЕ ОБРАБОТЧИКИ ДЛЯ УЧЕТА СТАТИСТИКИ ---
@router.callback_query(F.data.startswith("rev_"))
async def rev_start(call: CallbackQuery, state: FSMContext):
    p_id = call.data.split("_")[1]
    
    ban_result = supabase.table("banned_users")\
        .select("*")\
        .eq("user_id", call.from_user.id)\
        .execute()
    
    if ban_result.data:
        await call.answer("🚫 Вы заблокированы и не можете оставлять отзывы!", show_alert=True)
        return
    
    check = supabase.table("user_logs").select("*").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "review").execute()
    await state.update_data(p_id=p_id)
    await state.set_state(ReviewState.waiting_for_text)
    
    project = await find_project_by_id(int(p_id))
    project_name = project['name'] if project else "Проект"
    
    project_name_escaped = escape(str(project_name))
    txt = f"📝 <b>Изменение отзыва для проекта {project_name_escaped}</b>\n\nВведите новый текст отзыва:"
    if not check.data:
        txt = f"💬 <b>Новый отзыв для проекта {project_name_escaped}</b>\n\nВведите текст отзыва. <b>Важно. Если вы пишите негативный отзыв, просим вас прикреплять аргументацию со ссылками на облачные хранилища, в противном случае мы будем вынуждены удалить Ваш отзыв</b>"
    
    if call.message.photo:
        await safe_edit_media(call, txt, reply_markup=back_to_panel_kb(p_id))
    else:
        await safe_edit_message(call, txt, reply_markup=back_to_panel_kb(p_id))
    
    await call.answer()

@router.callback_query(F.data.startswith("st_"), ReviewState.waiting_for_rate)
async def rev_end(call: CallbackQuery, state: FSMContext):
    rate = int(call.data.split("_")[1])
    data = await state.get_data()
    p_id = data['p_id']
    
    ban_result = supabase.table("banned_users")\
        .select("*")\
        .eq("user_id", call.from_user.id)\
        .execute()
    
    if ban_result.data:
        await call.answer("🚫 Вы заблокированы и не можете оставлять отзывы!", show_alert=True)
        await state.clear()
        return
    
    old_rev = supabase.table("user_logs").select("*").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "review").execute()
    p = await find_project_by_id(int(p_id))
    
    if not p:
        await call.answer("❌ Проект не найден", show_alert=True)
        await state.clear()
        return
    
    old_score = p['score']
    rating_change = RATING_MAP[rate]
    
    if old_rev.data:
        old_rating_change = RATING_MAP[old_rev.data[0]['rating_val']]
        rating_change = RATING_MAP[rate] - old_rating_change
        new_score = old_score + rating_change
        supabase.table("user_logs").update({"review_text": data['txt'], "rating_val": rate}).eq("id", old_rev.data[0]['id']).execute()
        res_txt = "обновлен"
        log_id = old_rev.data[0]['id']
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
        res_txt = "добавлен"
        log_id = log.data[0]['id']
        reason = f"Новый отзыв: {rate}/5"
        
        # Обновляем статистику пользователя
        await update_user_stats(call.from_user.id, "reviews_count")

    supabase.table("projects").update({"score": new_score}).eq("id", p_id).execute()
    
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
    
    text = f"✅ <b>Отзыв успешно {res_txt}!</b>\n\n"
    text += f"📊 Изменение рейтинга: <code>{rating_change:+d}</code>\n"
    text += f"🔢 Новый рейтинг: <b>{new_score}</b>"
    
    if call.message.photo:
        await safe_edit_media(call, text, reply_markup=back_to_panel_kb(p_id))
    else:
        await safe_edit_message(call, text, reply_markup=back_to_panel_kb(p_id))
    
    project_name_escaped = escape(str(p['name']))
    review_text_escaped = escape(str(data['txt']))
    
    admin_text = (f"📢 <b>Отзыв {res_txt}:</b> {project_name_escaped}\n"
                  f"Пользователь: @{call.from_user.username or call.from_user.id}\n"
                  f"Текст: <i>{review_text_escaped}</i>\n"
                  f"Оценка: {rate}/5\n"
                  f"📊 Изменение рейтинга: {rating_change:+d}\n"
                  f"🔢 Новый рейтинг: {new_score}\n"
                  f"Удалить: <code>/delrev {log_id}</code>")
    
    await send_log_to_topics(admin_text, p['category'])

    await state.clear()
    await call.answer()

@router.callback_query(F.data.startswith("like_"))
async def handle_like(call: CallbackQuery):
    p_id = call.data.split("_")[1]
    
    ban_result = supabase.table("banned_users")\
        .select("*")\
        .eq("user_id", call.from_user.id)\
        .execute()
    
    if ban_result.data:
        await call.answer("🚫 Вы заблокированы и не можете ставить лайки!", show_alert=True)
        return
    
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "like").execute()
    if check.data:
        await call.answer("Вы уже поддержали этот проект!", show_alert=True)
        return
    
    project = await find_project_by_id(int(p_id))
    if not project:
        await call.answer("Проект не найден.", show_alert=True)
        return
    
    old_score = project['score']
    new_score = old_score + 1
    
    supabase.table("projects").update({"score": new_score}).eq("id", p_id).execute()
    
    supabase.table("user_logs").insert({
        "user_id": call.from_user.id,
        "project_id": p_id,
        "action_type": "like"
    }).execute()
    
    # Обновляем статистику пользователя
    await update_user_stats(call.from_user.id, "likes_count")
    
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
    
    await open_panel(call)
    await call.answer("❤️ Голос учтен!")

# --- ОБНОВЛЕННЫЕ АДМИН КОМАНДЫ ДЛЯ РЕФЕРАЛОВ ---
@router.message(Command("referralstats"))
async def admin_referral_stats(message: Message):
    """Статистика реферальной системы для админов"""
    if not await is_user_admin(message.from_user.id):
        return
    
    try:
        # Общая статистика
        total_referrals = supabase.table("referral_logs")\
            .select("*", count="exact")\
            .execute()
        
        total_users_with_ref = supabase.table("user_stats")\
            .select("*", count="exact")\
            .gt("referral_count", 0)\
            .execute()
        
        # Топ приглашающих
        top_inviters = supabase.table("user_stats")\
            .select("user_id, referral_count")\
            .order("referral_count", desc=True)\
            .limit(10)\
            .execute()
        
        # Последние рефералы
        recent_referrals = supabase.table("referral_logs")\
            .select("*")\
            .order("activated_at", desc=True)\
            .limit(5)\
            .execute()
        
        text = "<b>📊 СТАТИСТИКА РЕФЕРАЛЬНОЙ СИСТЕМЫ</b>\n\n"
        
        total_refs = total_referrals.count if hasattr(total_referrals, 'count') else 0
        total_with_ref = total_users_with_ref.count if hasattr(total_users_with_ref, 'count') else 0
        
        text += f"📈 <b>Общая статистика:</b>\n"
        text += f"• Всего рефералов: <b>{total_refs}</b>\n"
        text += f"• Пользователей с рефералами: <b>{total_with_ref}</b>\n"
        text += f"• Среднее на пользователя: <b>{total_refs/max(total_with_ref, 1):.1f}</b>\n\n"
        
        if top_inviters.data:
            text += f"🏆 <b>ТОП-10 ПРИГЛАШАЮЩИХ:</b>\n"
            for i, inviter in enumerate(top_inviters.data, 1):
                try:
                    user_info = await bot.get_chat(inviter['user_id'])
                    username = user_info.username or user_info.id
                except:
                    username = inviter['user_id']
                
                text += f"{i}. @{username} — <b>{inviter['referral_count']}</b> рефералов\n"
        
        if recent_referrals.data:
            text += f"\n🕒 <b>ПОСЛЕДНИЕ РЕФЕРАЛЫ:</b>\n"
            for ref in recent_referrals.data:
                date = ref['activated_at'][:16] if ref['activated_at'] else "Неизвестно"
                text += f"• Код: <code>{ref['referral_code']}</code> — {date}\n"
        
        await message.reply(text, parse_mode="HTML")
        
    except Exception as e:
        logging.error(f"Ошибка в /referralstats: {e}")
        await message.reply("❌ Ошибка при получении статистики.")

# --- ОБРАБОТЧИК ПАГИНАЦИИ ---
@router.callback_query(F.data.startswith("more_"))
async def handle_show_more(call: CallbackQuery):
    try:
        callback_data = call.data
        parts = callback_data.split("_")
        
        if len(parts) >= 3:
            category_key = "_".join(parts[1:-1])
            offset_str = parts[-1]
            
            try:
                offset = int(offset_str)
                await show_projects_batch(category_key, offset, call, is_first_batch=False)
                await call.answer()
            except ValueError:
                await call.answer("❌ Ошибка: неверный формат данных", show_alert=True)
        else:
            await call.answer("❌ Ошибка: неверный формат callback данных", show_alert=True)
            
    except Exception as e:
        logging.error(f"Ошибка пагинации: {e}")
        await call.answer("❌ Ошибка загрузки проектов", show_alert=True)

# --- ОБРАБОТЧИК КНОПКИ НАЗАД В МЕНЮ ---
@router.message(F.text == "⬅️ Назад в меню")
async def back_to_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_kb())

@router.message(F.text == "❌ Отмена")
async def cancel_action(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        await message.answer("❌ Действие отменено.", reply_markup=main_kb())
    else:
        await message.answer("Главное меню:", reply_markup=main_kb())

# --- ПОИСК ПРОЕКТОВ ---
@router.message(F.text == "🔍 Поиск проекта")
async def search_project_start(message: Message, state: FSMContext):
    await state.set_state(SearchState.waiting_for_query)
    await message.answer(
        "🔍 <b>Поиск проекта</b>\n\n"
        "Введите название проекта или его часть для поиска:",
        parse_mode="HTML",
        reply_markup=back_to_menu_kb()
    )
@router.message(SearchState.waiting_for_query, F.text)
async def search_project_execute(message: Message, state: FSMContext):
    """Выполнить поиск проекта"""
    if message.text == "⬅️ Назад в меню":
        await state.clear()
        await message.answer("Главное меню:", reply_markup=main_kb())
        return
    
    search_query = message.text.strip()
    
    if len(search_query) < 2:
        await message.answer(
            "❌ Слишком короткий запрос. Введите минимум 2 символа."
        )
        return
    
    try:
        # Ищем проекты по названию
        results = supabase.table("projects")\
            .select("*")\
            .ilike("name", f"%{search_query}%")\
            .order("score", desc=True)\
            .limit(10)\
            .execute().data
        
        if not results:
            search_query_escaped = escape(search_query)
            await message.answer(
                f"🔍 По запросу <b>'{search_query_escaped}'</b> ничего не найдено.",
                parse_mode="HTML"
            )
            return
        
        search_query_escaped = escape(search_query)
        text = f"🔍 <b>Результаты поиска:</b> '{search_query_escaped}'\n"
        text += f"Найдено проектов: {len(results)}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
        
        # Показываем первые 5 результатов
        for i, p in enumerate(results[:5], 1):
            # Экранируем данные
            project_name_escaped = escape(str(p['name']))
            category_escaped = escape(str(CATEGORIES.get(p['category'], p['category'])))
            description_escaped = escape(str(p['description']))
            
            text += f"<b>{i}. {project_name_escaped}</b>\n"
            text += f"📂 Категория: {category_escaped}\n"
            text += f"📊 Рейтинг: <b>{p['score']}</b>\n"
            text += f"{description_escaped[:80]}...\n"
            text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
        
        # Создаем инлайн-клавиатуру с результатами
        keyboard = []
        for p in results[:5]:
            keyboard.append([InlineKeyboardButton(
                text=f"{p['name']} ({p['score']})",
                callback_data=f"panel_{p['id']}"
            )])
        
        if len(results) > 5:
            text += f"<i>Показаны первые 5 из {len(results)} результатов</i>"
        
        await message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None,
            parse_mode="HTML"
        )
        
    except Exception as e:
        logging.error(f"Ошибка поиска: {e}")
        await message.answer(
            "❌ Ошибка при выполнении поиска. Попробуйте позже."
        )

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
            categories_list = "\n".join([f"- <code>{escape(str(k))}</code> ({escape(str(v))})" for k, v in CATEGORIES.items()])
            await message.reply(
                f"❌ Неверная категория. Доступные:\n{categories_list}",
                parse_mode="HTML"
            )
            return
        
        existing = supabase.table("projects").select("*").eq("name", name).execute()
        if existing.data:
            name_escaped = escape(name)
            await message.reply(
                f"⚠️ Проект <b>{name_escaped}</b> уже существует!",
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
            name_escaped = escape(name)
            desc_escaped = escape(desc)
            log_text = (f"📋 <b>Добавлен новый проект:</b>\n\n"
                       f"🏷 Название: <b>{name_escaped}</b>\n"
                       f"📂 Категория: <code>{cat}</code>\n"
                       f"📝 Описание: {desc_escaped}\n"
                       f"👤 Админ: @{message.from_user.username or message.from_user.id}")
            
            await send_log_to_topics(log_text, cat)
            
            await message.reply(
                f"✅ Проект <b>{name_escaped}</b> успешно добавлен!\n"
                f"🆔 ID проекта: <code>{result.data[0]['id']}</code>",
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
        
        # Ищем проект по названию
        project = await find_project_by_name(name)
        if not project:
            name_escaped = escape(name)
            await message.reply(
                f"❌ Проект <b>{name_escaped}</b> не найден!",
                parse_mode="HTML"
            )
            return
        
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
        supabase.table("project_photos").delete().eq("project_id", project_id).execute()
        
        # Отправляем лог
        project_name_escaped = escape(str(project['name']))
        log_text = (f"🗑 <b>Проект удален:</b>\n\n"
                   f"🏷 Название: <b>{project_name_escaped}</b>\n"
                   f"📂 Категория: <code>{category}</code>\n"
                   f"📊 Удалено отзывов: {reviews_num}\n"
                   f"🔢 Финальный рейтинг: {score}\n"
                   f"👤 Админ: @{message.from_user.username or message.from_user.id}")
        
        await send_log_to_topics(log_text, category)
        
        await message.reply(
            f"🗑 Проект <b>{project_name_escaped}</b> удален!\n"
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
                "<code>/score Название проекта | число</code>\n\n"
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
            val_str_escaped = escape(val_str)
            await message.reply(
                f"❌ <b>{val_str_escaped}</b> не является числом!",
                parse_mode="HTML"
            )
            return
        
        # Ищем проект по названию
        project = await find_project_by_name(name)
        if not project:
            name_escaped = escape(name)
            await message.reply(
                f"❌ Проект <b>{name_escaped}</b> не найден!",
                parse_mode="HTML"
            )
            return
        
        await state.update_data(
            project_id=project['id'],
            project_name=project['name'],
            category=project['category'],
            old_score=project['score'],
            change_amount=val
        )
        
        await state.set_state(AdminScoreState.waiting_for_reason)
        
        project_name_escaped = escape(str(project['name']))
        await message.reply(
            f"📝 <b>Укажите причину изменения рейтинга для проекта <i>{project_name_escaped}</i>:</b>\n\n"
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
        project_name_escaped = escape(str(project_name))
        reason_escaped = escape(reason)
        log_text = (f"⚖️ <b>Изменен рейтинг проекта:</b>\n\n"
                   f"🏷 Название: <b>{project_name_escaped}</b>\n"
                   f"📂 Категория: <code>{category}</code>\n"
                   f"🔢 Было: <b>{old_score}</b>\n"
                   f"🔢 Стало: <b>{new_score}</b>\n"
                   f"📊 Изменение: <code>{change_amount:+d}</code>\n"
                   f"📝 Причина: <i>{reason_escaped}</i>\n"
                   f"👤 Админ: @{message.from_user.username or message.from_user.id}")
        
        await send_log_to_topics(log_text, category)
        
        change_symbol = "📈" if change_amount > 0 else "📉" if change_amount < 0 else "➡️"
        project_name_escaped = escape(str(project_name))
        await message.reply(
            f"{change_symbol} <b>Рейтинг проекта изменен!</b>\n\n"
            f"🏷 Проект: <b>{project_name_escaped}</b>\n"
            f"🔢 {old_score} → <b>{new_score}</b> ({change_amount:+d})\n"
            f"📝 Причина: <i>{reason_escaped}</i>",
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
            log_id_str_escaped = escape(log_id_str)
            await message.reply(
                f"❌ <b>{log_id_str_escaped}</b> не является числовым ID!",
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
        project_name_escaped = escape(str(project['name']))
        review_text_escaped = escape(str(rev['review_text']))
        log_text = (f"🗑 <b>Удален отзыв:</b>\n\n"
                   f"🏷 Проект: <b>{project_name_escaped}</b>\n"
                   f"📂 Категория: <code>{project['category']}</code>\n"
                   f"🆔 ID отзыва: <code>{log_id}</code>\n"
                   f"⭐ Оценка: {rev['rating_val']}/5\n"
                   f"📊 Изменение рейтинга: {rating_change:+d}\n"
                   f"🔢 Новый рейтинг: {new_score}\n"
                   f"📝 Текст отзыва: <i>{review_text_escaped[:100]}...</i>\n"
                   f"👤 Удалил: @{message.from_user.username or message.from_user.id}")
        
        await send_log_to_topics(log_text, project['category'])
        
        await message.reply(
            f"🗑 Отзыв <b>#{log_id}</b> удален!\n"
            f"📁 Проект: <b>{project_name_escaped}</b>\n"
            f"📊 Рейтинг: {old_score} → {new_score} ({rating_change:+d})",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logging.error(f"Ошибка в /delrev: {e}")
        await message.reply(
            "❌ Ошибка при удалении отзыва."
        )

# --- ДОПОЛНИТЕЛЬНЫЕ АДМИН-КОМАНДЫ ---

@router.message(Command("editdesc"))
async def admin_edit_desc(message: Message):
    """Изменить описание проекта"""
    if not await is_user_admin(message.from_user.id): 
        return
        
    try:
        if len(message.text.split()) < 2:
            await message.reply(
                "❌ Неверный формат. Используйте:\n"
                "<code>/editdesc Название проекта | Новое описание</code>\n\n"
                "Пример: <code>/editdesc Бот Помощи | Обновленный бот с новыми функциями</code>",
                parse_mode="HTML"
            )
            return
        
        raw = message.text.split(maxsplit=1)[1]
        parts = raw.split("|")
        
        if len(parts) < 2:
            await message.reply(
                "❌ Неверный формат. Нужно два параметра через '|':\n"
                "1. Название проекта\n"
                "2. Новое описание",
                parse_mode="HTML"
            )
            return
        
        name, new_desc = [p.strip() for p in parts[:2]]
        
        # Ищем проект по названию
        project = await find_project_by_name(name)
        if not project:
            name_escaped = escape(name)
            await message.reply(
                f"❌ Проект <b>{name_escaped}</b> не найден!",
                parse_mode="HTML"
            )
            return
        
        old_desc = project['description']
        
        # Обновляем описание
        supabase.table("projects").update({"description": new_desc}).eq("id", project['id']).execute()
        
        # Отправляем лог
        project_name_escaped = escape(str(project['name']))
        old_desc_escaped = escape(str(old_desc[:200]))
        new_desc_escaped = escape(str(new_desc[:200]))
        
        log_text = (f"📝 <b>Изменено описание проекта:</b>\n\n"
                   f"🏷 Проект: <b>{project_name_escaped}</b> (ID: {project['id']})\n"
                   f"📂 Категория: <code>{project['category']}</code>\n"
                   f"📝 <b>Было:</b> <i>{old_desc_escaped}...</i>\n"
                   f"📝 <b>Стало:</b> <i>{new_desc_escaped}...</i>\n"
                   f"👤 Админ: @{message.from_user.username or message.from_user.id}")
        
        await send_log_to_topics(log_text, project['category'])
        
        await message.reply(
            f"✅ Описание проекта <b>{project_name_escaped}</b> успешно обновлено!",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logging.error(f"Ошибка в /editdesc: {e}")
        await message.reply(
            "❌ Ошибка при изменении описания."
        )

@router.message(Command("addphoto"))
async def admin_add_photo(message: Message, state: FSMContext):
    """Добавить фото к проекту"""
    if not await is_user_admin(message.from_user.id): 
        return
        
    try:
        if len(message.text.split()) < 2:
            await message.reply(
                "❌ Неверный формат. Используйте:\n"
                "<code>/addphoto Название проекта</code>\n\n"
                "Пример: <code>/addphoto Бот Помощи</code>\n\n"
                "После отправки команды отправьте фото в ответ на это сообщение.",
                parse_mode="HTML"
            )
            return
        
        name = message.text.split(maxsplit=1)[1].strip()
        
        # Ищем проект по названию
        project = await find_project_by_name(name)
        if not project:
            name_escaped = escape(name)
            await message.reply(
                f"❌ Проект <b>{name_escaped}</b> не найден!",
                parse_mode="HTML"
            )
            return
        
        # Сохраняем данные в state и ждем фото
        await state.update_data(
            project_id=project['id'],
            project_name=project['name'],
            category=project['category']
        )
        await state.set_state(EditProjectState.waiting_for_photo)
        
        project_name_escaped = escape(str(project['name']))
        await message.reply(
            f"📸 <b>Отправьте фотографию для проекта:</b>\n\n"
            f"🏷 Проект: <b>{project_name_escaped}</b>\n"
            f"🆔 ID: <code>{project['id']}</code>\n\n"
            f"<i>Отправьте фото в ответ на это сообщение</i>",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logging.error(f"Ошибка в /addphoto: {e}")
        await message.reply(
            "❌ Ошибка при обработке команды."
        )

@router.message(EditProjectState.waiting_for_photo, F.photo)
async def admin_save_photo(message: Message, state: FSMContext):
    """Сохранение фото проекта"""
    data = await state.get_data()
    project_id = data['project_id']
    project_name = data['project_name']
    category = data['category']
    
    # Получаем самую большую версию фото
    photo = message.photo[-1]
    photo_file_id = photo.file_id
    
    # Сохраняем фото в базу
    success = await save_project_photo(project_id, photo_file_id, message.from_user.id)
    
    if success:
        # Отправляем лог
        project_name_escaped = escape(str(project_name))
        log_text = (f"🖼️ <b>Добавлено фото проекта:</b>\n\n"
                   f"🏷 Проект: <b>{project_name_escaped}</b> (ID: {project_id})\n"
                   f"📂 Категория: <code>{category}</code>\n"
                   f"👤 Админ: @{message.from_user.username or message.from_user.id}")
        
        await send_log_to_topics(log_text, category)
        
        # Показываем превью фото
        project_name_escaped = escape(str(project_name))
        await message.reply_photo(
            photo=photo_file_id,
            caption=f"✅ Фото для проекта <b>{project_name_escaped}</b> успешно сохранено!",
            parse_mode="HTML"
        )
    else:
        await message.reply(
            "❌ Ошибка при сохранении фото."
        )
    
    await state.clear()

@router.message(EditProjectState.waiting_for_photo)
async def admin_wrong_photo(message: Message):
    """Неправильный ввод при ожидании фото"""
    await message.reply(
        "❌ Пожалуйста, отправьте фотографию.\n"
        "Отправьте фото или используйте /cancel для отмены."
    )

@router.message(Command("stats"))
async def admin_stats(message: Message):
    """Показать статистику проекта"""
    if not await is_user_admin(message.from_user.id): 
        return
        
    try:
        if len(message.text.split()) < 2:
            await message.reply(
                "❌ Укажите название проекта для просмотра статистики.\n"
                "<code>/stats Название проекта</code>\n\n"
                "Пример: <code>/stats Бот Помощи</code>",
                parse_mode="HTML"
            )
            return
        
        name = message.text.split(maxsplit=1)[1].strip()
        
        # Ищем проект по названию
        project = await find_project_by_name(name)
        if not project:
            name_escaped = escape(name)
            await message.reply(
                f"❌ Проект <b>{name_escaped}</b> не найден!",
                parse_mode="HTML"
            )
            return
        
        # Экранируем ВСЕ данные из базы
        project_name_escaped = escape(str(project['name']))
        category_escaped = escape(str(project['category']))
        
        # Получаем статистику
        reviews_result = supabase.table("user_logs")\
            .select("*")\
            .eq("project_id", project['id'])\
            .eq("action_type", "review")\
            .execute()
        
        likes_result = supabase.table("user_logs")\
            .select("*")\
            .eq("project_id", project['id'])\
            .eq("action_type", "like")\
            .execute()
        
        history_result = supabase.table("rating_history")\
            .select("*")\
            .eq("project_id", project['id'])\
            .execute()
        
        reviews = reviews_result.data if reviews_result.data else []
        likes = likes_result.data if likes_result.data else []
        history = history_result.data if history_result.data else []
        
        # Считаем среднюю оценку
        avg_rating = 0
        if reviews:
            total_rating = sum([r['rating_val'] for r in reviews])
            avg_rating = total_rating / len(reviews)
        
        text = f"<b>📊 СТАТИСТИКА ПРОЕКТА</b>\n\n"
        text += f"🏷 <b>{project_name_escaped}</b>\n"
        text += f"🆔 ID: <code>{project['id']}</code>\n"
        text += f"📂 Категория: <code>{category_escaped}</code>\n"
        text += f"🔢 Текущий рейтинг: <b>{project['score']}</b>\n"
        text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        text += f"📈 <b>Общая статистика:</b>\n"
        text += f"• 💬 Отзывов: {len(reviews)}\n"
        text += f"• ❤️ Лайков: {len(likes)}\n"
        text += f"• ⭐ Средняя оценка: {avg_rating:.1f}/5\n"
        text += f"• 📊 Всего изменений рейтинга: {len(history)}\n\n"
        
        if reviews:
            # Распределение оценок
            rating_dist = {1:0, 2:0, 3:0, 4:0, 5:0}
            for r in reviews:
                rating_dist[r['rating_val']] += 1
            
            text += f"📊 <b>Распределение оценок:</b>\n"
            for rating in range(5, 0, -1):
                count = rating_dist[rating]
                percent = (count / len(reviews)) * 100 if reviews else 0
                text += f"{'⭐' * rating}: {count} ({percent:.1f}%)\n"
        
        # Получаем фото проекта
        photo_file_id = await get_project_photo(project['id'])
        
        if photo_file_id:
            try:
                await message.reply_photo(
                    photo=photo_file_id,
                    caption=text,
                    parse_mode="HTML"
                )
            except:
                await message.reply(text, parse_mode="HTML")
        else:
            await message.reply(text, parse_mode="HTML")
        
    except Exception as e:
        logging.error(f"Ошибка в /stats: {e}")
        await message.reply(
            "❌ Ошибка при получении статистики."
        )

@router.message(Command("list"))
async def admin_list_projects(message: Message):
    """Список всех проектов"""
    if not await is_user_admin(message.from_user.id): 
        return
        
    try:
        # Отправляем сообщение о загрузке
        loading_msg = await message.reply("⏳ Загружаем список проектов...")
        
        # Получаем все проекты
        projects = supabase.table("projects").select("*").order("score", desc=True).execute().data
        
        if not projects:
            await loading_msg.delete()
            await message.reply("📭 Список проектов пуст.")
            return
        
        # Получаем все отзывы одним запросом
        all_reviews = supabase.table("user_logs")\
            .select("project_id")\
            .eq("action_type", "review")\
            .execute().data
        
        # Создаем словарь: project_id -> количество отзывов
        review_counts = {}
        for review in all_reviews:
            project_id = review['project_id']
            review_counts[project_id] = review_counts.get(project_id, 0) + 1
        
        # Удаляем сообщение о загрузке
        await loading_msg.delete()
        
        # Отправляем общую статистику
        total_projects = len(projects)
        total_reviews = len(all_reviews)
        
        stats_text = (
            f"<b>📊 ОБЩАЯ СТАТИСТИКА</b>\n\n"
            f"📋 Всего проектов: <b>{total_projects}</b>\n"
            f"💬 Всего отзывов: <b>{total_reviews}</b>\n"
            f"📈 Среднее отзывов на проект: <b>{total_reviews/total_projects:.1f}</b>\n\n"
            f"<i>Отправляю список проектов...</i>"
        )
        
        await message.reply(stats_text, parse_mode="HTML")
        
        # Разбиваем проекты на части по 20 штук
        chunk_size = 20
        for chunk_num in range(0, len(projects), chunk_size):
            chunk = projects[chunk_num:chunk_num + chunk_size]
            
            text = f"<b>📋 ПРОЕКТЫ {chunk_num+1}-{min(chunk_num+chunk_size, total_projects)} из {total_projects}</b>\n\n"
            
            for i, p in enumerate(chunk, start=chunk_num+1):
                # Получаем количество отзывов из словаря
                reviews_num = review_counts.get(p['id'], 0)
                
                # Экранируем специальные символы в данных
                project_name = escape(str(p['name']))
                category = escape(str(p['category']))
                
                text += f"<b>{i}. {project_name}</b>\n"
                text += f"   🆔 ID: <code>{p['id']}</code>\n"
                text += f"   📂 Категория: <code>{category}</code>\n"
                text += f"   🔢 Рейтинг: <b>{p['score']}</b>\n"
                text += f"   💬 Отзывов: {reviews_num}\n"
                text += f"   ⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            
            # Если это последний чанк, добавляем итоговую статистику
            if chunk_num + chunk_size >= total_projects:
                # Находим проект с максимальным рейтингом
                top_project = max(projects, key=lambda x: x['score'])
                top_project_name = escape(str(top_project['name']))
                
                text += f"\n<b>🏆 ЛИДЕР:</b>\n"
                text += f"<b>{top_project_name}</b> — <code>{top_project['score']}</code> баллов\n"
                text += f"💬 Отзывов: {review_counts.get(top_project['id'], 0)}"
            
            await message.answer(text, parse_mode="HTML")
            
            # Небольшая пауза между сообщениями
            if chunk_num + chunk_size < total_projects:
                await asyncio.sleep(0.5)
        
    except Exception as e:
        logging.error(f"Ошибка в /list: {e}")
        await message.reply(
            f"❌ Ошибка при получении списка проектов: {str(e)[:100]}"
        )

# --- КОМАНДЫ УПРАВЛЕНИЯ БАНОМ ---

@router.message(Command("ban"))
async def admin_ban(message: Message):
    """Забанить пользователя"""
    if not await is_user_admin(message.from_user.id): 
        return
    
    try:
        if len(message.text.split()) < 2:
            await message.reply(
                "❌ Неверный формат. Используйте:\n"
                "<code>/ban ID_пользователя [причина]</code>\n\n"
                "Пример: <code>/ban 123456789 Нарушение правил</code>",
                parse_mode="HTML"
            )
            return
        
        parts = message.text.split(maxsplit=2)
        user_id_str = parts[1]
        reason = parts[2] if len(parts) > 2 else "Без указания причины"
        
        try:
            user_id = int(user_id_str)
        except ValueError:
            user_id_str_escaped = escape(user_id_str)
            await message.reply(
                f"❌ <b>{user_id_str_escaped}</b> не является числовым ID пользователя!",
                parse_mode="HTML"
            )
            return
        
        # Проверяем, не админ ли это
        if await is_user_admin(user_id):
            await message.reply(
                "❌ Нельзя забанить администратора!",
                parse_mode="HTML"
            )
            return
        
        # Проверяем, не забанен ли уже
        existing = supabase.table("banned_users")\
            .select("*")\
            .eq("user_id", user_id)\
            .execute()
        
        if existing.data:
            await message.reply(
                f"⚠️ Пользователь <code>{user_id}</code> уже забанен!",
                parse_mode="HTML"
            )
            return
        
        # Баним пользователя
        result = supabase.table("banned_users").insert({
            "user_id": user_id,
            "banned_by": message.from_user.id,
            "banned_by_username": message.from_user.username,
            "reason": reason,
            "banned_at": "now()"
        }).execute()
        
        if result.data:
            # Отправляем лог
            reason_escaped = escape(reason)
            log_text = (f"🚫 <b>Пользователь забанен:</b>\n\n"
                       f"🆔 ID: <code>{user_id}</code>\n"
                       f"📝 Причина: <i>{reason_escaped}</i>\n"
                       f"👮 Админ: @{message.from_user.username or message.from_user.id}")
            
            await send_log_to_topics(log_text)
            
            reason_escaped = escape(reason)
            await message.reply(
                f"🚫 Пользователь <code>{user_id}</code> забанен!\n"
                f"📝 Причина: <i>{reason_escaped}</i>",
                parse_mode="HTML"
            )
        else:
            await message.reply(
                "❌ Ошибка при добавлении в бан-лист."
            )
            
    except Exception as e:
        logging.error(f"Ошибка в /ban: {e}")
        await message.reply(
            "❌ Ошибка при выполнении команды."
        )

@router.message(Command("unban"))
async def admin_unban(message: Message):
    """Разбанить пользователя"""
    if not await is_user_admin(message.from_user.id): 
        return
    
    try:
        if len(message.text.split()) < 2:
            await message.reply(
                "❌ Неверный формат. Используйте:\n"
                "<code>/unban ID_пользователя</code>\n\n"
                "Пример: <code>/unban 123456789</code>",
                parse_mode="HTML"
            )
            return
        
        user_id_str = message.text.split()[1]
        
        try:
            user_id = int(user_id_str)
        except ValueError:
            user_id_str_escaped = escape(user_id_str)
            await message.reply(
                f"❌ <b>{user_id_str_escaped}</b> не является числовым ID пользователя!",
                parse_mode="HTML"
            )
            return
        
        # Проверяем, есть ли пользователь в бане
        existing = supabase.table("banned_users")\
            .select("*")\
            .eq("user_id", user_id)\
            .execute()
        
        if not existing.data:
            await message.reply(
                f"⚠️ Пользователь <code>{user_id}</code> не находится в бане!",
                parse_mode="HTML"
            )
            return
        
        # Удаляем из бана
        supabase.table("banned_users")\
            .delete()\
            .eq("user_id", user_id)\
            .execute()
        
        # Отправляем лог
        log_text = (f"✅ <b>Пользователь разбанен:</b>\n\n"
                   f"🆔 ID: <code>{user_id}</code>\n"
                   f"👮 Админ: @{message.from_user.username or message.from_user.id}")
        
        await send_log_to_topics(log_text)
        
        await message.reply(
            f"✅ Пользователь <code>{user_id}</code> разбанен!",
            parse_mode="HTML"
        )
            
    except Exception as e:
        logging.error(f"Ошибка в /unban: {e}")
        await message.reply(
            "❌ Ошибка при выполнении команды."
        )

@router.message(Command("banlist"))
async def admin_banlist(message: Message):
    """Показать список забаненных пользователей"""
    if not await is_user_admin(message.from_user.id): 
        return
    
    try:
        banned_users = supabase.table("banned_users")\
            .select("*")\
            .order("banned_at", desc=True)\
            .execute().data
    
        if not banned_users:
            await message.reply("📭 Список забаненных пользователей пуст.")
            return
    
        text = "<b>🚫 СПИСОК ЗАБАНЕННЫХ ПОЛЬЗОВАТЕЛЕЙ</b>\n\n"
    
        for i, ban in enumerate(banned_users, 1):
            # Форматируем дату
            banned_at = ban['banned_at'][:19] if ban['banned_at'] else "Неизвестно"
            reason_escaped = escape(str(ban.get('reason', 'Не указана')))
            banned_by_escaped = escape(str(ban.get('banned_by_username', ban.get('banned_by', 'Неизвестно'))))
    
            text += f"<b>{i}. ID:</b> <code>{ban['user_id']}</code>\n"
            text += f"   <b>Причина:</b> <i>{reason_escaped}</i>\n"
            text += f"   <b>Забанен:</b> {banned_at}\n"
            text += f"   <b>Админ:</b> {banned_by_escaped}\n"
            text += f"   ⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    
        text += f"\n📊 Всего забанено: <b>{len(banned_users)}</b> пользователей"
    
        # Разбиваем на части если слишком длинное
        if len(text) > 4000:
            parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
            for part in parts:
                await message.answer(part, parse_mode="HTML")
        else:
            await message.reply(text, parse_mode="HTML")
    
    except Exception as e:
        logging.error(f"Ошибка в /banlist: {e}")
        await message.reply(
            "❌ Ошибка при получении списка банов."
        )

@router.message(Command("mystatus"))
async def check_my_status(message: Message):
    """Проверить свой статус (админ/бан)"""
    user_id = message.from_user.id
    
    # Проверяем бан
    ban_result = supabase.table("banned_users")\
        .select("*")\
        .eq("user_id", user_id)\
        .execute()
    
    # Проверяем админку
    is_admin = await is_user_admin(user_id)
    
    text = f"<b>👤 ВАШ СТАТУС</b>\n\n"
    text += f"🆔 ID: <code>{user_id}</code>\n"
    text += f"👤 Username: @{message.from_user.username or 'Нет'}\n"
    text += f"📛 Имя: {message.from_user.first_name or ''} {message.from_user.last_name or ''}\n"
    text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    
    if is_admin:
        text += "✅ <b>Статус: АДМИНИСТРАТОР</b>\n"
        text += "Вы имеете доступ ко всем командам управления."
    elif ban_result.data:
        reason_escaped = escape(str(ban_result.data[0].get('reason', 'Не указана')))
        text += "🚫 <b>Статус: ЗАБЛОКИРОВАН</b>\n"
        text += f"📝 Причина: <i>{reason_escaped}</i>\n"
        if ban_result.data[0].get('banned_at'):
            text += f"📅 Дата блокировки: {ban_result.data[0].get('banned_at')[:10]}"
    else:
        text += "✅ <b>Статус: ПОЛЬЗОВАТЕЛЬ</b>\n"
        text += "Вы можете оставлять отзывы и ставить лайки."
    
    await message.reply(text, parse_mode="HTML")

@router.message(Command("finduser"))
async def admin_find_user(message: Message):
    """Найти информацию о пользователе"""
    if not await is_user_admin(message.from_user.id): 
        return
    
    try:
        if len(message.text.split()) < 2:
            await message.reply(
                "❌ Неверный формат. Используйте:\n"
                "<code>/finduser ID_пользователя</code>\n\n"
                "Пример: <code>/finduser 123456789</code>",
                parse_mode="HTML"
            )
            return
        
        query = message.text.split(maxsplit=1)[1].strip()
        
        try:
            user_id = int(query)
            # Ищем по ID в banned_users
            ban_result = supabase.table("banned_users")\
                .select("*")\
                .eq("user_id", user_id)\
                .execute()
        except ValueError:
            # Если не число, ищем в логах
            user_logs = supabase.table("user_logs")\
                .select("user_id")\
                .execute()
            
            # Это упрощенный поиск
            user_id = None
            ban_result = None
        
        text = f"<b>🔍 ПОИСК ПОЛЬЗОВАТЕЛЯ</b>\n\n"
        query_escaped = escape(query)
        text += f"🔎 Запрос: <code>{query_escaped}</code>\n"
        text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        
        if ban_result and ban_result.data:
            ban = ban_result.data[0]
            reason_escaped = escape(str(ban.get('reason', 'Не указана')))
            banned_by_escaped = escape(str(ban.get('banned_by_username', ban.get('banned_by', 'Неизвестно'))))
            
            text += f"🚫 <b>СТАТУС: ЗАБАНЕН</b>\n\n"
            text += f"🆔 ID: <code>{ban['user_id']}</code>\n"
            text += f"📝 Причина: <i>{reason_escaped}</i>\n"
            if ban.get('banned_at'):
                text += f"📅 Дата: {ban['banned_at'][:10]}\n"
            text += f"👮 Админ: {banned_by_escaped}\n\n"
            text += f"<i>Используйте</i> <code>/unban {ban['user_id']}</code> <i>для разблокировки</i>"
        elif user_id:
            text += f"✅ <b>СТАТУС: НЕ ЗАБАНЕН</b>\n\n"
            text += f"🆔 ID: <code>{user_id}</code>\n\n"
            text += f"<i>Используйте</i> <code>/ban {user_id} причина</code> <i>для блокировки</i>"
        else:
            text += "Пользователь не найден."
        
        await message.reply(text, parse_mode="HTML")
        
    except Exception as e:
        logging.error(f"Ошибка в /finduser: {e}")
        await message.reply(
            "❌ Ошибка при поиске пользователя."
        )

# --- ЛОГИКА ПОЛЬЗОВАТЕЛЯ ---
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    
    # Проверяем бан
    ban_result = supabase.table("banned_users") \
        .select("*") \
        .eq("user_id", message.from_user.id) \
        .execute()
    
    if ban_result.data:
        reason_escaped = escape(str(ban_result.data[0].get('reason', 'Не указана')))
        await message.answer(
            f"🚫 <b>Вы заблокированы!</b>\n\n"
            f"📝 Причина: <i>{reason_escaped}</i>\n"
            f"📅 Дата блокировки: {ban_result.data[0].get('banned_at', 'Неизвестно')[:10]}\n\n"
            f"Для разблокировки обратитесь к администратору.",
            parse_mode="HTML"
        )
        return
    
    # Получаем топ проектов
    top_projects = supabase.table("projects") \
        .select("*") \
        .order("score", desc=True) \
        .limit(5) \
        .execute().data

    # Стартовое сообщение (внутри функции!)
    start_text = "<b>🌟 ДОБРО ПОЖАЛОВАТЬ В РЕЙТИНГ ПРОЕКТОВ КМБП!</b>\n\n"
    start_text += "Здесь вы можете оценивать проекты, оставлять отзывы и следить за рейтингом лучших проектов сообщества.\n\n"

    if top_projects:
        start_text += "<b>🏆 ТОП-5 ПРОЕКТОВ:</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        for i, p in enumerate(top_projects, 1):
            project_name_escaped = escape(str(p['name']))
            start_text += f"{i}. <b>{project_name_escaped}</b> — <code>{p['score']}</code>\n"
    else:
        start_text += "<b>🏆 ТОП-5 ПРОЕКТОВ:</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        start_text += "Список пуст. Будьте первым, кто добавит проект!\n"


    start_text += "\n📊 <i>Нажмите на категорию ниже, чтобы увидеть все проекты</i>"
    start_text += "\n<b><i>Партнеры KMBP Monthly Awards Season 1</i></b>"
    start_text += "\n✴ @The_infernal_paradise_bot"

    try:
        # Пробуем отправить с фото
        photo = FSInputFile("start_photo.jpg")
        await message.answer_photo(
            photo=photo,
            caption=start_text,
            reply_markup=main_kb(),
            parse_mode="HTML"
        )
    except Exception as e:
        # Если фото нет, отправляем просто текст
        print(f"Ошибка отправки фото: {e}")  # Логирование ошибки
        await message.answer(start_text, reply_markup=main_kb(), parse_mode="HTML")




@router.message(F.text.in_(CATEGORIES.values()))
async def show_cat(message: Message):
    """Показать первую партию проектов категории"""
    cat_key = [k for k, v in CATEGORIES.items() if v == message.text][0]
    await show_projects_batch(cat_key, 0, message, is_first_batch=True)



# --- ОСНОВНЫЕ ОБРАБОТЧИКИ ПРОЕКТОВ ---
@router.callback_query(F.data.startswith("panel_"))
async def open_panel(call: CallbackQuery):
    """Открывает панель управления проектом"""
    p_id = call.data.split("_")[1]

    
    # Получаем информацию о проекте
    project = await find_project_by_id(int(p_id))
    if not project:
        await call.answer("Проект не найден.", show_alert=True)
        return
    
    # Проверяем, есть ли у пользователя отзыв
    user_review = supabase.table("user_logs")\
        .select("*")\
        .eq("user_id", call.from_user.id)\
        .eq("project_id", p_id)\
        .eq("action_type", "review")\
        .execute()
    
    has_review = bool(user_review.data)
    
    # Получаем фото проекта
    photo_file_id = await get_project_photo(int(p_id))
    
    # Получаем последние изменения
    recent_changes = supabase.table("rating_history").select("*")\
        .eq("project_id", p_id)\
        .order("created_at", desc=True)\
        .limit(2)\
        .execute().data
    
    # Экранируем данные
    project_name_escaped = escape(str(project['name']))
    description_escaped = escape(str(project['description']))
    
    text = f"<b>🔘 ПАНЕЛЬ УПРАВЛЕНИЯ</b>\n\n"
    text += f"<b>{project_name_escaped}</b>\n"
    text += f"{description_escaped[:200]}{'...' if len(project['description']) > 200 else ''}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    text += f"📊 Текущий рейтинг: <b>{project['score']}</b>\n"
    
    if has_review:
        text += f"✅ <i>Вы уже оставили отзыв об этом проекте</i>\n"
    else:
        text += f"📝 <i>Вы еще не оценивали этот проект</i>\n"
    
    text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    
    if recent_changes:
        text += f"<b>📈 Последние изменения:</b>\n"
        for change in recent_changes:
            date = change['created_at'][:10] if change['created_at'] else ""
            symbol = "📈" if change['change_amount'] > 0 else "📉" if change['change_amount'] < 0 else "➡️"
            reason_escaped = escape(str(change['reason']))
            text += f"{symbol} <code>{change['change_amount']:+d}</code> — {reason_escaped[:50]}... ({date})\n"
        text += f"\n"
    
    text += f"<i>Выберите действие:</i>"
    
    # Если в исходном сообщении есть фото
    if call.message.photo:
        await safe_edit_media(call, text, reply_markup=project_panel_kb(p_id, has_review))
    else:
        await safe_edit_message(call, text, reply_markup=project_panel_kb(p_id, has_review))
    
    await call.answer()

@router.callback_query(F.data.startswith("back_"))
async def back_to_panel_callback(call: CallbackQuery):
    """Возврат к панели из других разделов"""
    p_id = call.data.split("_")[1]
    await open_panel(call)

@router.callback_query(F.data == "back_to_text")
async def back_to_text(call: CallbackQuery, state: FSMContext):
    """Возврат к вводу текста отзыва"""
    data = await state.get_data()
    if 'p_id' in data:
        p_id = data['p_id']
        project = await find_project_by_id(int(p_id))
        project_name = project['name'] if project else "Проект"
        
        project_name_escaped = escape(str(project_name))
        txt = f"📝 <b>Введите текст отзыва для проекта {project_name_escaped}. Важно. Если вы пишите негативный отзыв, просим вас прикреплять аргументацию со ссылками на облачные хранилища, в противном случае мы будем вынуждены удалить Ваш отзыв</b>\n\n"
        txt += "<i>Напишите ваш отзыв или используйте '❌ Отмена' для отмены</i>"
        
        if call.message.photo:
            await safe_edit_media(call, txt, reply_markup=back_to_panel_kb(p_id))
        else:
            await safe_edit_message(call, txt, reply_markup=back_to_panel_kb(p_id))
        
        await state.set_state(ReviewState.waiting_for_text)
    await call.answer()

@router.callback_query(F.data.startswith("rev_"))
async def rev_start(call: CallbackQuery, state: FSMContext):
    p_id = call.data.split("_")[1]
    
    # Проверяем, не забанен ли пользователь
    ban_result = supabase.table("banned_users")\
        .select("*")\
        .eq("user_id", call.from_user.id)\
        .execute()
    
    if ban_result.data:
        await call.answer("🚫 Вы заблокированы и не можете оставлять отзывы!", show_alert=True)
        return
    
    check = supabase.table("user_logs").select("*").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "review").execute()
    await state.update_data(p_id=p_id)
    await state.set_state(ReviewState.waiting_for_text)
    
    project = await find_project_by_id(int(p_id))
    project_name = project['name'] if project else "Проект"
    
    project_name_escaped = escape(str(project_name))
    txt = f"📝 <b>Изменение отзыва для проекта {project_name_escaped}</b>\n\nВведите новый текст отзыва:"
    if not check.data:
        txt = f"💬 <b>Новый отзыв для проекта {project_name_escaped}</b>\n\nВведите текст отзыва. <b> Важно. Если вы пишите негативный отзыв, просим вас прикреплять аргументацию со ссылками на облачные хранилища, в противном случае мы будем вынуждены удалить Ваш отзыв </b>"
    
    if call.message.photo:
        await safe_edit_media(call, txt, reply_markup=back_to_panel_kb(p_id))
    else:
        await safe_edit_message(call, txt, reply_markup=back_to_panel_kb(p_id))
    
    await call.answer()

@router.message(ReviewState.waiting_for_text)
async def rev_text(message: Message, state: FSMContext):
    # Железобетонная обработка кнопки "Назад в меню"
    if message.text == "⬅️ Назад в меню":
        await state.clear()
        await message.answer("Главное меню:", reply_markup=main_kb())
        return
    
    # Обработка кнопки "Отмена"
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Создание отзыва отменено.", reply_markup=main_kb())
        return
    
    if message.text and message.text.startswith("/"): 
        return 
    
    await state.update_data(txt=message.text)
    await state.set_state(ReviewState.waiting_for_rate)
    
    # Получаем ID проекта из state
    data = await state.get_data()
    p_id = data.get('p_id')
    
    kb = rating_kb()
    await message.answer("🌟 <b>Выберите оценку:</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("st_"), ReviewState.waiting_for_rate)
async def rev_end(call: CallbackQuery, state: FSMContext):
    rate = int(call.data.split("_")[1])
    data = await state.get_data()
    p_id = data['p_id']
    
    # Проверяем, не забанен ли пользователь
    ban_result = supabase.table("banned_users")\
        .select("*")\
        .eq("user_id", call.from_user.id)\
        .execute()
    
    if ban_result.data:
        await call.answer("🚫 Вы заблокированы и не можете оставлять отзывы!", show_alert=True)
        await state.clear()
        return
    
    old_rev = supabase.table("user_logs").select("*").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "review").execute()
    p = await find_project_by_id(int(p_id))
    
    if not p:
        await call.answer("❌ Проект не найден", show_alert=True)
        await state.clear()
        return
    
    old_score = p['score']
    rating_change = RATING_MAP[rate]
    
    if old_rev.data:
        # Учитываем старую оценку при пересчете
        old_rating_change = RATING_MAP[old_rev.data[0]['rating_val']]
        rating_change = RATING_MAP[rate] - old_rating_change
        new_score = old_score + rating_change
        supabase.table("user_logs").update({"review_text": data['txt'], "rating_val": rate}).eq("id", old_rev.data[0]['id']).execute()
        res_txt = "обновлен"
        log_id = old_rev.data[0]['id']
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
        res_txt = "добавлен"
        log_id = log.data[0]['id']
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
    
    text = f"✅ <b>Отзыв успешно {res_txt}!</b>\n\n"
    text += f"📊 Изменение рейтинга: <code>{rating_change:+d}</code>\n"
    text += f"🔢 Новый рейтинг: <b>{new_score}</b>"
    
    if call.message.photo:
        await safe_edit_media(call, text, reply_markup=back_to_panel_kb(p_id))
    else:
        await safe_edit_message(call, text, reply_markup=back_to_panel_kb(p_id))
    
    # ФОРМИРУЕМ ЛОГ
    project_name_escaped = escape(str(p['name']))
    review_text_escaped = escape(str(data['txt']))
    
    admin_text = (f"📢 <b>Отзыв {res_txt}:</b> {project_name_escaped}\n"
                  f"Пользователь: @{call.from_user.username or call.from_user.id}\n"
                  f"Текст: <i>{review_text_escaped}</i>\n"
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
    
    project = await find_project_by_id(int(p_id))
    project_name = project['name'] if project else "Проект"
    
    if not revs: 
        project_name_escaped = escape(str(project_name))
        text = f"<b>💬 ОТЗЫВЫ ПРОЕКТА</b>\n<b>{project_name_escaped}</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
        text += "📭 Отзывов еще нет\n"
        
        if call.message.photo:
            await safe_edit_media(call, text, reply_markup=back_to_panel_kb(p_id))
        else:
            await safe_edit_message(call, text, reply_markup=back_to_panel_kb(p_id))
        
        await call.answer()
        return
    
    project_name_escaped = escape(str(project_name))
    text = f"<b>💬 ПОСЛЕДНИЕ ОТЗЫВЫ</b>\n<b>{project_name_escaped}</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    for r in revs: 
        date = r['created_at'][:10] if r['created_at'] else ""
        stars = '⭐' * r['rating_val']
        review_text_escaped = escape(str(r['review_text']))
        text += f"{stars}\n<i>{review_text_escaped}</i>\n📅 {date}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    
    if call.message.photo:
        await safe_edit_media(call, text, reply_markup=back_to_panel_kb(p_id))
    else:
        await safe_edit_message(call, text, reply_markup=back_to_panel_kb(p_id))
    
    await call.answer()

@router.callback_query(F.data.startswith("history_"))
async def view_history(call: CallbackQuery):
    """Показать историю изменений рейтинга проекта"""
    p_id = call.data.split("_")[1]
    
    # Получаем информацию о проекте
    project = await find_project_by_id(int(p_id))
    if not project:
        await call.answer("Проект не найден.", show_alert=True)
        return
    
    # Получаем историю изменений
    history = supabase.table("rating_history").select("*")\
        .eq("project_id", p_id)\
        .order("created_at", desc=True)\
        .limit(10)\
        .execute().data
    
    project_name_escaped = escape(str(project['name']))
    text = f"<b>📊 ИСТОРИЯ ИЗМЕНЕНИЙ</b>\n<b>{project_name_escaped}</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    
    if not history:
        text += "📭 История изменений пуста\n"
    else:
        for i, change in enumerate(history, 1):
            date_time = change['created_at'][:16] if change['created_at'] else ""
            
            if change['is_admin_action']:
                admin_username = change.get('admin_username') or change.get('admin_id', 'Неизвестно')
                actor = f"👤 Админ: {admin_username}"
            else:
                username = change.get('username', 'Пользователь')
                actor = f"👤 Пользователь: {username}"
            
            symbol = "📈" if change['change_amount'] > 0 else "📉" if change['change_amount'] < 0 else "➡️"
            reason_escaped = escape(str(change['reason']))
            
            text += f"{i}. {symbol} <b>{change['score_before']} → {change['score_after']}</b> ({change['change_amount']:+d})\n"
            text += f"   📝 {reason_escaped[:50]}{'...' if len(change['reason']) > 50 else ''}\n"
            text += f"   {actor}\n"
            text += f"   📅 {date_time}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    
    if call.message.photo:
        await safe_edit_media(call, text, reply_markup=back_to_panel_kb(p_id))
    else:
        await safe_edit_message(call, text, reply_markup=back_to_panel_kb(p_id))
    
    await call.answer()

@router.callback_query(F.data.startswith("like_"))
async def handle_like(call: CallbackQuery):
    p_id = call.data.split("_")[1]
    
    # Проверяем, не забанен ли пользователь
    ban_result = supabase.table("banned_users")\
        .select("*")\
        .eq("user_id", call.from_user.id)\
        .execute()
    
    if ban_result.data:
        await call.answer("🚫 Вы заблокированы и не можете ставить лайки!", show_alert=True)
        return
    
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "like").execute()
    if check.data: 
        await call.answer("Вы уже поддержали этот проект!", show_alert=True)
        return
    
    # Получаем текущий рейтинг
    project = await find_project_by_id(int(p_id))
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
    await open_panel(call)
    await call.answer("❤️ Голос учтен!")

@router.callback_query(F.data.startswith("myreview_"))
async def show_my_review(call: CallbackQuery):
    """Показать мой отзыв о проекте"""
    p_id = call.data.split("_")[1]
    user_id = call.from_user.id
    
    # Ищем отзыв пользователя
    review = supabase.table("user_logs")\
        .select("*")\
        .eq("user_id", user_id)\
        .eq("project_id", p_id)\
        .eq("action_type", "review")\
        .single()\
        .execute()
    
    if not review.data:
        await call.answer("У вас еще нет отзыва об этом проекте", show_alert=True)
        return
    
    review_data = review.data
    project = await find_project_by_id(int(p_id))
    
    project_name_escaped = escape(str(project['name'])) if project else "Проект"
    text = f"<b>📝 ВАШ ОТЗЫВ</b>\n\n"
    text += f"<b>{project_name_escaped}</b>\n"
    text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    text += f"{'⭐' * review_data['rating_val']}\n"
    
    review_text_escaped = escape(str(review_data['review_text']))
    text += f"<i>{review_text_escaped}</i>\n\n"
    
    if review_data.get('created_at'):
        created = review_data['created_at'][:10]
        text += f"📅 Дата отзыва: {created}\n"
    
    # Кнопка для изменения
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить отзыв", callback_data=f"rev_{p_id}")],
        [InlineKeyboardButton(text="⬅️ Назад к панели", callback_data=f"panel_{p_id}")]
    ])
    
    if call.message.photo:
        await safe_edit_media(call, text, reply_markup=kb)
    else:
        await safe_edit_message(call, text, reply_markup=kb)
    
    await call.answer()

@router.callback_query(F.data == "close_panel")
async def close_panel(call: CallbackQuery):
    """Закрытие панели - удаление сообщения с панелью"""
    await call.message.delete()
    await call.answer("Панель закрыта")

# --- ЗАПУСК БОТА ---
async def main():
    logging.basicConfig(level=logging.INFO)
    dp.update.outer_middleware(AccessMiddleware())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
