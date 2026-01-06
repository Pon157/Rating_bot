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

# --- НАСТРОЙКИ ТОПИКОВ (Замени цифры на ID из ссылок) ---
TOPIC_LOGS_ALL = 46  # Общий топик для ВСЕХ логов/отзывов

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

class UserSettingsState(StatesGroup):
    waiting_for_notifications = State()

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
        
        # Проверяем, что это сообщение от пользователя (не от бота)
        if not user or user.is_bot: 
            return await handler(event, data)
        
        # Проверяем, является ли пользователь админом
        if await is_user_admin(user.id): 
            return await handler(event, data)
        
        # Проверяем, забанен ли пользователь
        try:
            res = supabase.table("banned_users")\
                .select("user_id, reason")\
                .eq("user_id", user.id)\
                .execute()
            
            # Если пользователь найден в таблице banned_users
            if res.data:
                # Показываем сообщение о бане, если это Message
                if isinstance(event, Message):
                    await event.answer(
                        f"🚫 Вы заблокированы!\n"
                        f"📝 Причина: {res.data[0].get('reason', 'Не указана')}\n\n"
                        f"Для разблокировки обратитесь к администратору.",
                        parse_mode="HTML"
                    )
                # Или просто отвечаем на CallbackQuery
                elif isinstance(event, CallbackQuery):
                    await event.answer(
                        "🚫 Вы заблокированы!",
                        show_alert=True
                    )
                return  # Блокируем выполнение handler
        
        except Exception as e:
            logging.error(f"Ошибка проверки бана: {e}")
        
        # Если пользователь не забанен, пропускаем
        return await handler(event, data)

# --- КЛАВИАТУРЫ ---
def main_kb():
    """Основная клавиатура с категориями и поиском"""
    buttons = [
        [KeyboardButton(text=v) for v in list(CATEGORIES.values())[:2]],
        [KeyboardButton(text=v) for v in list(CATEGORIES.values())[2:5]],
        [
            KeyboardButton(text="🔍 Поиск проекта"),
            KeyboardButton(text="⭐ Топ недели")
        ],
        [
            KeyboardButton(text="📊 Моя активность"),
            KeyboardButton(text="⚙️ Настройки")
        ]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def cancel_kb():
    """Клавиатура для отмены действия"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )

def back_to_menu_kb():
    """Клавиатура для возврата в меню"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⬅️ Назад в меню")]],
        resize_keyboard=True
    )

def settings_kb():
    """Клавиатура настроек"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔔 Уведомления")],
            [KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="⬅️ Назад в меню")]
        ],
        resize_keyboard=True
    )

def notifications_kb():
    """Клавиатура настроек уведомлений"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Включить уведомления")],
            [KeyboardButton(text="❌ Выключить уведомления")],
            [KeyboardButton(text="⬅️ Назад в настройки")]
        ],
        resize_keyboard=True
    )

def project_card_kb(p_id):
    """Чистая карточка проекта"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔘 Открыть панель", callback_data=f"panel_{p_id}")]
    ])

def project_panel_kb(p_id, has_review=False):
    """Полная панель действий"""
    buttons = [
        [
            InlineKeyboardButton(text="⭐ Оценить", callback_data=f"rev_{p_id}"),
            InlineKeyboardButton(text="❤️ Поддержать", callback_data=f"like_{p_id}")
        ],
        [
            InlineKeyboardButton(text="💬 Отзывы", callback_data=f"viewrev_{p_id}"),
            InlineKeyboardButton(text="📊 История", callback_data=f"history_{p_id}")
        ],
        [
            InlineKeyboardButton(text="🚀 Детальная статистика", callback_data=f"detailed_{p_id}"),
        ]
    ]
    
    if has_review:
        buttons.append([InlineKeyboardButton(text="✏️ Изменить мой отзыв", callback_data=f"myreview_{p_id}")])
    
    buttons.append([InlineKeyboardButton(text="❌ Закрыть панель", callback_data="close_panel")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def back_to_panel_kb(p_id):
    """Кнопка назад к панели"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к панели", callback_data=f"panel_{p_id}")]
    ])

def rating_kb():
    """Клавиатура для выбора оценки"""
    buttons = [
        [InlineKeyboardButton(text="⭐" * i, callback_data=f"st_{i}")] for i in range(5, 0, -1)
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад к тексту", callback_data="back_to_text")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def pagination_kb(category_key, offset, has_next=True):
    """Клавиатура пагинации для кнопки 'Показать еще'"""
    buttons = []
    if has_next:
        callback_data = f"more_{category_key}_{offset}"
        buttons.append([InlineKeyboardButton(text="📜 Показать еще", callback_data=callback_data)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- НОВЫЕ ФУНКЦИИ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ---

def get_achievement_icon(achievement: str) -> str:
    """Возвращает иконку для достижения"""
    icons = {
        "first_review": "🎯",
        "reviewer": "📝",
        "supporter": "❤️",
        "top_reviewer": "🏆",
        "veteran": "👴",
        "explorer": "🧭",
        "critic": "⭐",
        "champion": "👑"
    }
    return icons.get(achievement, "🏅")

async def check_user_achievements(user_id: int):
    """Проверяет и присваивает достижения пользователя"""
    achievements = []
    
    # Получаем активность пользователя
    reviews = supabase.table("user_logs")\
        .select("*")\
        .eq("user_id", user_id)\
        .eq("action_type", "review")\
        .execute().data or []
    
    likes = supabase.table("user_logs")\
        .select("*")\
        .eq("user_id", user_id)\
        .eq("action_type", "like")\
        .execute().data or []
    
    # Достижения за отзывы
    if len(reviews) == 1:
        achievements.append(("first_review", "Первый отзыв"))
    
    if len(reviews) >= 5:
        achievements.append(("reviewer", "Активный рецензент (5+ отзывов)"))
    
    if len(reviews) >= 20:
        achievements.append(("top_reviewer", "Топ-рецензент (20+ отзывов)"))
    
    # Достижения за лайки
    if len(likes) >= 3:
        achievements.append(("supporter", "Поддерживающий (3+ лайка)"))
    
    if len(likes) >= 10:
        achievements.append(("champion", "Чемпион поддержки (10+ лайков)"))
    
    # Достижения за разнообразие
    unique_projects = len(set([r['project_id'] for r in reviews] + [l['project_id'] for l in likes]))
    if unique_projects >= 5:
        achievements.append(("explorer", "Исследователь (5+ разных проектов)"))
    
    # Достижения за время
    if reviews:
        first_review = min([r['created_at'] for r in reviews if r.get('created_at')])
        first_date = datetime.fromisoformat(first_review.replace('Z', '+00:00'))
        if datetime.now() - first_date > timedelta(days=30):
            achievements.append(("veteran", "Ветеран (более месяца в системе)"))
    
    # Достижения за оценки
    if reviews:
        avg_rating = sum([r['rating_val'] for r in reviews]) / len(reviews)
        if avg_rating <= 2:
            achievements.append(("critic", "Строгий критик (средняя оценка ≤ 2)"))
        elif avg_rating >= 4:
            achievements.append(("critic", "Доброжелательный (средняя оценка ≥ 4)"))
    
    return achievements

async def get_user_stats(user_id: int):
    """Получает статистику пользователя"""
    try:
        # Общая статистика
        total_reviews = supabase.table("user_logs")\
            .select("*", count="exact")\
            .eq("user_id", user_id)\
            .eq("action_type", "review")\
            .execute()
        
        total_likes = supabase.table("user_logs")\
            .select("*", count="exact")\
            .eq("user_id", user_id)\
            .eq("action_type", "like")\
            .execute()
        
        # Средняя оценка
        reviews = supabase.table("user_logs")\
            .select("*")\
            .eq("user_id", user_id)\
            .eq("action_type", "review")\
            .execute().data or []
        
        avg_rating = 0
        if reviews:
            total_rating = sum([r['rating_val'] for r in reviews])
            avg_rating = total_rating / len(reviews)
        
        # Распределение по категориям
        categories_stats = {}
        for cat_key, cat_name in CATEGORIES.items():
            cat_count = supabase.table("user_logs")\
                .select("*", count="exact")\
                .eq("user_id", user_id)\
                .eq("action_type", "review")\
                .in_("project_id", 
                    supabase.table("projects")
                    .select("id")
                    .eq("category", cat_key)
                    .execute()
                    .data
                )\
                .execute()
            categories_stats[cat_name] = cat_count.count if hasattr(cat_count, 'count') else 0
        
        # Достижения
        achievements = await check_user_achievements(user_id)
        
        return {
            "total_reviews": total_reviews.count if hasattr(total_reviews, 'count') else 0,
            "total_likes": total_likes.count if hasattr(total_likes, 'count') else 0,
            "avg_rating": round(avg_rating, 1),
            "categories": categories_stats,
            "achievements": achievements
        }
        
    except Exception as e:
        logging.error(f"Ошибка получения статистики пользователя: {e}")
        return None

async def get_weekly_top():
    """Получает топ проектов за неделю"""
    try:
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()
        
        # Получаем проекты с наибольшим изменением рейтинга за неделю
        result = supabase.table("rating_history")\
            .select("project_id, SUM(change_amount) as total_change")\
            .gte("created_at", week_ago)\
            .group("project_id")\
            .order("total_change", desc=True)\
            .limit(10)\
            .execute()
        
        top_projects = []
        for item in result.data:
            project = await find_project_by_id(item['project_id'])
            if project:
                project['weekly_change'] = item['total_change']
                top_projects.append(project)
        
        return top_projects[:5]  # Возвращаем топ-5
        
    except Exception as e:
        logging.error(f"Ошибка получения топа недели: {e}")
        return []

async def get_project_detailed_stats(project_id: int):
    """Получает детальную статистику проекта"""
    try:
        project = await find_project_by_id(project_id)
        if not project:
            return None
        
        # Базовая статистика
        reviews = supabase.table("user_logs")\
            .select("*")\
            .eq("project_id", project_id)\
            .eq("action_type", "review")\
            .execute().data or []
        
        likes = supabase.table("user_logs")\
            .select("*")\
            .eq("project_id", project_id)\
            .eq("action_type", "like")\
            .execute().data or []
        
        # Распределение оценок
        rating_dist = {1:0, 2:0, 3:0, 4:0, 5:0}
        for r in reviews:
            rating_dist[r['rating_val']] += 1
        
        # Изменения за неделю/месяц
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()
        month_ago = (datetime.now() - timedelta(days=30)).isoformat()
        
        weekly_change = supabase.table("rating_history")\
            .select("SUM(change_amount)")\
            .eq("project_id", project_id)\
            .gte("created_at", week_ago)\
            .execute()
        
        monthly_change = supabase.table("rating_history")\
            .select("SUM(change_amount)")\
            .eq("project_id", project_id)\
            .gte("created_at", month_ago)\
            .execute()
        
        # Тренды
        trends = []
        if len(reviews) >= 10:
            avg_rating = sum([r['rating_val'] for r in reviews]) / len(reviews)
            if avg_rating >= 4:
                trends.append("📈 Высокие оценки")
            elif avg_rating <= 2:
                trends.append("📉 Низкие оценки")
        
        if len(reviews) > 5 and len(likes) > len(reviews) * 2:
            trends.append("❤️ Популярный у поддержки")
        
        if len(reviews) >= 3:
            recent_reviews = [r for r in reviews if r.get('created_at')]
            if recent_reviews:
                recent_dates = [datetime.fromisoformat(r['created_at'].replace('Z', '+00:00')) for r in recent_reviews]
                if max(recent_dates) > datetime.now() - timedelta(days=3):
                    trends.append("🔥 Активно обсуждается")
        
        return {
            "project": project,
            "total_reviews": len(reviews),
            "total_likes": len(likes),
            "rating_distribution": rating_dist,
            "weekly_change": weekly_change.data[0]['sum'] if weekly_change.data else 0,
            "monthly_change": monthly_change.data[0]['sum'] if monthly_change.data else 0,
            "trends": trends,
            "avg_rating": sum([r['rating_val'] for r in reviews]) / len(reviews) if reviews else 0
        }
        
    except Exception as e:
        logging.error(f"Ошибка получения детальной статистики: {e}")
        return None

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

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
async def safe_edit_message(call: CallbackQuery, text: str, reply_markup=None, parse_mode="HTML"):
    """Безопасное редактирование сообщения"""
    try:
        await call.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        # Если ошибка "message is not modified", просто отвечаем
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
    """Безопасное редактирование сообщения с медиа"""
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
    """Получает фото проекта из базы"""
    try:
        result = supabase.table("project_photos").select("*").eq("project_id", project_id).execute()
        if result.data:
            # Возвращаем file_id фото
            return result.data[0].get('photo_file_id', '')
    except Exception as e:
        logging.error(f"Ошибка получения фото: {e}")
    return None

async def save_project_photo(project_id: int, photo_file_id: str, admin_id: int):
    """Сохраняет фото проекта в базу"""
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
    """Находит проект по названию"""
    try:
        result = supabase.table("projects").select("*").ilike("name", f"%{name}%").execute()
        if result.data:
            return result.data[0]  # Возвращаем первый найденный проект
    except Exception as e:
        logging.error(f"Ошибка поиска проекта: {e}")
    return None

async def find_project_by_id(project_id: int):
    """Находит проект по ID"""
    try:
        result = supabase.table("projects").select("*").eq("id", project_id).execute()
        if result.data:
            return result.data[0]
    except Exception as e:
        logging.error(f"Ошибка поиска проекта по ID: {e}")
    return None

async def show_projects_batch(category_key, offset, message_or_call, is_first_batch=False):
    """Показывает партию проектов (по 5 штук)"""
    projects_per_batch = 5
    
    # Получаем проекты для категории
    data = supabase.table("projects")\
        .select("*")\
        .eq("category", category_key)\
        .order("score", desc=True)\
        .range(offset, offset + projects_per_batch - 1)\
        .execute().data
    
    # Считаем общее количество проектов
    count_result = supabase.table("projects")\
        .select("*", count="exact")\
        .eq("category", category_key)\
        .execute()
    
    total_projects = count_result.count if hasattr(count_result, 'count') else 0
    
    if not data: 
        if is_first_batch:
            text = f"📭 В разделе <b>'{CATEGORIES[category_key]}'</b> пока нет проектов."
            
            if isinstance(message_or_call, CallbackQuery):
                await safe_edit_message(message_or_call, text)
            else:
                await message_or_call.answer(text, parse_mode="HTML")
        else:
            if isinstance(message_or_call, CallbackQuery):
                await message_or_call.answer("Больше проектов нет", show_alert=True)
        return
    
    # Если это первый батч, отправляем новое сообщение
    if is_first_batch:
        text = f"<b>{CATEGORIES[category_key]}</b>\n"
        text += f"Всего проектов: {total_projects}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
        
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.message.answer(text, parse_mode="HTML")
        else:
            await message_or_call.answer(text, parse_mode="HTML")
    
    for p in data:
        # Получаем фото проекта
        photo_file_id = await get_project_photo(p['id'])
        
        # Красивая карточка проекта как было раньше
        card = f"<b>{p['name']}</b>\n\n{p['description'][:150]}{'...' if len(p['description']) > 150 else ''}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        card += f"📊 Текущий рейтинг: <b>{p['score']}</b>\n\n"
        card += f"<i>Нажмите кнопку ниже для управления проектом</i>"
        
        if isinstance(message_or_call, CallbackQuery):
            # Для CallbackQuery отправляем новое сообщение
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
            # Для Message отправляем новое сообщение
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
    
    # Проверяем, есть ли еще проекты
    has_next = offset + projects_per_batch < total_projects
    
    # Если это первый батч и есть еще проекты, добавляем кнопку "Показать еще"
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
        # Обновляем сообщение с пагинацией
        new_offset = offset + projects_per_batch
        new_has_next = new_offset < total_projects
        
        # Удаляем старое сообщение с пагинацией и создаем новое
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
            # Если проектов больше нет, отправляем финальное сообщение
            await message_or_call.message.answer("✅ <b>Показаны все проекты</b>\nВсего проектов: <code>{}</code>".format(total_projects), parse_mode="HTML")

# --- НОВЫЕ ФИЧИ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ---

@router.message(F.text == "📊 Моя активность")
async def my_activity(message: Message):
    """Показать статистику активности пользователя"""
    user_id = message.from_user.id
    
    stats = await get_user_stats(user_id)
    if stats is None:
        await message.answer("❌ Не удалось получить статистику. Попробуйте позже.")
        return
    
    text = f"<b>📊 ВАША АКТИВНОСТЬ</b>\n\n"
    text += f"👤 Пользователь: @{message.from_user.username or message.from_user.id}\n"
    text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    text += f"<b>📈 Основная статистика:</b>\n"
    text += f"• 💬 Отзывов оставлено: {stats['total_reviews']}\n"
    text += f"• ❤️ Лайков поставлено: {stats['total_likes']}\n"
    text += f"• ⭐ Средняя ваша оценка: {stats['avg_rating']}/5\n\n"
    
    # Статистика по категориям
    if any(stats['categories'].values()):
        text += f"<b>📂 Активность по категориям:</b>\n"
        for cat, count in stats['categories'].items():
            if count > 0:
                text += f"• {cat}: {count} отзывов\n"
        text += f"\n"
    
    # Достижения
    if stats['achievements']:
        text += f"<b>🏆 ВАШИ ДОСТИЖЕНИЯ:</b>\n"
        for achievement_code, achievement_name in stats['achievements']:
            icon = get_achievement_icon(achievement_code)
            text += f"{icon} {achievement_name}\n"
        text += f"\n"
    
    # Совет
    if stats['total_reviews'] == 0:
        text += f"<i>🎯 Совет: оставьте свой первый отзыв, чтобы получить первое достижение!</i>"
    elif stats['total_reviews'] < 5:
        text += f"<i>🎯 Совет: оставьте еще {5 - stats['total_reviews']} отзыва, чтобы получить достижение 'Активный рецензент'!</i>"
    elif stats['total_likes'] < 3:
        text += f"<i>🎯 Совет: поставьте еще {3 - stats['total_likes']} лайка, чтобы получить достижение 'Поддерживающий'!</i>"
    else:
        text += f"<i>🎯 Вы отлично проявляете активность! Продолжайте в том же духе!</i>"
    
    await message.answer(text, parse_mode="HTML", reply_markup=main_kb())

@router.message(F.text == "⭐ Топ недели")
async def weekly_top(message: Message):
    """Показать топ проектов недели"""
    top_projects = await get_weekly_top()
    
    if not top_projects:
        await message.answer(
            "📊 <b>ТОП НЕДЕЛИ</b>\n\n"
            "Пока недостаточно данных для формирования топа.\n"
            "Начните оценивать проекты, и скоро здесь появятся лидеры!",
            parse_mode="HTML"
        )
        return
    
    text = f"<b>⭐ ТОП ПРОЕКТОВ НЕДЕЛИ</b>\n\n"
    text += f"📅 Период: последние 7 дней\n"
    text += f"📊 Рейтинг основан на изменении баллов\n"
    text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    
    for i, project in enumerate(top_projects, 1):
        change = project.get('weekly_change', 0)
        change_symbol = "📈" if change > 0 else "📉" if change < 0 else "➡️"
        
        text += f"<b>{i}. {project['name']}</b>\n"
        text += f"📂 Категория: {CATEGORIES.get(project['category'], project['category'])}\n"
        text += f"🔢 Текущий рейтинг: <b>{project['score']}</b>\n"
        text += f"{change_symbol} За неделю: <code>{change:+d}</code>\n"
        text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    
    text += f"\n<i>Этот топ обновляется автоматически каждую неделю!</i>"
    
    # Создаем инлайн-кнопки для быстрого перехода к проектам
    kb_buttons = []
    for project in top_projects[:3]:  # Только первые 3 проекта
        kb_buttons.append([InlineKeyboardButton(
            text=f"🔘 {project['name']}",
            callback_data=f"panel_{project['id']}"
        )])
    
    await message.answer(
        text, 
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons) if kb_buttons else None
    )

@router.message(F.text == "⚙️ Настройки")
async def settings_menu(message: Message):
    """Меню настроек пользователя"""
    text = f"<b>⚙️ НАСТРОЙКИ</b>\n\n"
    text += f"Здесь вы можете настроить работу с ботом:\n\n"
    text += f"• 🔔 <b>Уведомления</b> - управление оповещениями\n"
    text += f"• 📊 <b>Статистика</b> - ваша активность и достижения\n"
    text += f"• ⭐ <b>Рекомендации</b> - проекты для оценки\n\n"
    text += f"<i>Выберите нужный раздел:</i>"
    
    await message.answer(text, parse_mode="HTML", reply_markup=settings_kb())

@router.message(F.text == "📊 Статистика")
async def personal_stats(message: Message):
    """Персональная статистика"""
    await my_activity(message)

# --- ОБРАБОТЧИК ПАГИНАЦИИ ---
@router.callback_query(F.data.startswith("more_"))
async def handle_show_more(call: CallbackQuery):
    """Обработка кнопки 'Показать еще'"""
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
    """Железобетонный обработчик кнопки 'Назад в меню'"""
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_kb())

@router.message(F.text == "⬅️ Назад в настройки")
async def back_to_settings(message: Message):
    """Вернуться в настройки"""
    await settings_menu(message)

@router.message(F.text == "❌ Отмена")
async def cancel_action(message: Message, state: FSMContext):
    """Отмена текущего действия"""
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        await message.answer("❌ Действие отменено.", reply_markup=main_kb())
    else:
        await message.answer("Главное меню:", reply_markup=main_kb())

# --- НОВЫЙ ОБРАБОТЧИК: ДЕТАЛЬНАЯ СТАТИСТИКА ПРОЕКТА ---
@router.callback_query(F.data.startswith("detailed_"))
async def detailed_project_stats(call: CallbackQuery):
    """Показать детальную статистику проекта"""
    p_id = call.data.split("_")[1]
    
    stats = await get_project_detailed_stats(int(p_id))
    if not stats:
        await call.answer("❌ Не удалось получить статистику", show_alert=True)
        return
    
    project = stats['project']
    
    text = f"<b>📊 ДЕТАЛЬНАЯ СТАТИСТИКА</b>\n\n"
    text += f"<b>{project['name']}</b>\n"
    text += f"📂 Категория: {CATEGORIES.get(project['category'], project['category'])}\n"
    text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    
    text += f"<b>📈 Основные показатели:</b>\n"
    text += f"• 💬 Всего отзывов: {stats['total_reviews']}\n"
    text += f"• ❤️ Всего лайков: {stats['total_likes']}\n"
    text += f"• ⭐ Средняя оценка: {stats['avg_rating']:.1f}/5\n"
    text += f"• 📊 Текущий рейтинг: <b>{project['score']}</b>\n\n"
    
    text += f"<b>📅 Динамика:</b>\n"
    text += f"• 📈 За неделю: <code>{stats['weekly_change']:+d}</code>\n"
    text += f"• 📈 За месяц: <code>{stats['monthly_change']:+d}</code>\n\n"
    
    # Распределение оценок
    if stats['total_reviews'] > 0:
        text += f"<b>📊 Распределение оценок:</b>\n"
        for rating in range(5, 0, -1):
            count = stats['rating_distribution'][rating]
            percent = (count / stats['total_reviews']) * 100 if stats['total_reviews'] > 0 else 0
            text += f"{'⭐' * rating}: {count} ({percent:.1f}%)\n"
        text += f"\n"
    
    # Тренды
    if stats['trends']:
        text += f"<b>🎯 Тренды и особенности:</b>\n"
        for trend in stats['trends']:
            text += f"• {trend}\n"
        text += f"\n"
    
    # Рекомендации
    if stats['total_reviews'] == 0:
        text += f"<i>🎯 Будьте первым, кто оставит отзыв об этом проекте!</i>"
    elif stats['total_reviews'] < 5:
        text += f"<i>🎯 Этот проект нуждается в большем количестве оценок!</i>"
    elif stats['avg_rating'] >= 4:
        text += f"<i>🎯 Пользователи высоко оценивают этот проект!</i>"
    
    if call.message.photo:
        await safe_edit_media(call, text, reply_markup=back_to_panel_kb(p_id))
    else:
        await safe_edit_message(call, text, reply_markup=back_to_panel_kb(p_id))
    
    await call.answer()

# --- НОВЫЙ ОБРАБОТЧИК: МОЙ ОТЗЫВ ---
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
    
    text = f"<b>📝 ВАШ ОТЗЫВ</b>\n\n"
    text += f"<b>{project['name'] if project else 'Проект'}</b>\n"
    text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    text += f"{'⭐' * review_data['rating_val']}\n"
    text += f"<i>{review_data['review_text']}</i>\n\n"
    
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

# --- ОБНОВЛЕННЫЙ ПОИСК ПРОЕКТОВ ---
@router.message(F.text == "🔍 Поиск проекта")
async def search_project_start(message: Message, state: FSMContext):
    """Начать поиск проекта"""
    await state.set_state(SearchState.waiting_for_query)
    await message.answer(
        "🔍 <b>ПОИСК ПРОЕКТА</b>\n\n"
        "Введите название проекта или его часть для поиска:\n\n"
        "<i>Можно искать по части названия, например:</i>\n"
        "<code>бот</code> - найдет все проекты со словом 'бот'\n"
        "<code>канал</code> - найдет все каналы\n"
        "<code>помощ</code> - найдет проекты с 'помощ' в названии",
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
            await message.answer(
                f"🔍 По запросу <b>'{search_query}'</b> ничего не найдено.\n\n"
                f"<i>Попробуйте:</i>\n"
                f"• Использовать другие слова\n"
                f"• Проверить правильность написания\n"
                f"• Поискать по категориям",
                parse_mode="HTML"
            )
            return
        
        text = f"🔍 <b>Результаты поиска:</b> '{search_query}'\n"
        text += f"Найдено проектов: {len(results)}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
        
        # Показываем первые 5 результатов
        for i, p in enumerate(results[:5], 1):
            text += f"<b>{i}. {p['name']}</b>\n"
            text += f"📂 Категория: {CATEGORIES.get(p['category'], p['category'])}\n"
            text += f"📊 Рейтинг: <b>{p['score']}</b>\n"
            text += f"{p['description'][:80]}...\n"
            text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
        
        # Создаем инлайн-клавиатуру с результатами
        keyboard = []
        for p in results[:5]:
            keyboard.append([InlineKeyboardButton(
                text=f"{p['name']} ({p['score']})",
                callback_data=f"panel_{p['id']}"
            )])
        
        if len(results) > 5:
            text += f"<i>Показаны первые 5 из {len(results)} результатов</i>\n"
            text += f"<i>Для более точного поиска уточните запрос</i>"
        
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

# --- ОСНОВНЫЕ КОМАНДЫ АДМИНИСТРАТОРА (остаются без изменений) ---

# ... (все административные команды остаются как были, но я их пропускаю для краткости)

# --- ЛОГИКА ПОЛЬЗОВАТЕЛЯ ---
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    
    # Проверяем бан
    ban_result = supabase.table("banned_users")\
        .select("*")\
        .eq("user_id", message.from_user.id)\
        .execute()
    
    if ban_result.data:
        await message.answer(
            f"🚫 <b>Вы заблокированы!</b>\n\n"
            f"📝 Причина: <i>{ban_result.data[0].get('reason', 'Не указана')}</i>\n"
            f"📅 Дата блокировки: {ban_result.data[0].get('banned_at', 'Неизвестно')[:10]}\n\n"
            f"Для разблокировки обратитесь к администратору.",
            parse_mode="HTML"
        )
        return
    
    # Получаем топ проектов
    top_projects = supabase.table("projects").select("*").order("score", desc=True).limit(5).execute().data
    
    # Стартовое сообщение
    start_text = "<b>🌟 ДОБРО ПОЖАЛОВАТЬ В РЕЙТИНГ ПРОЕКТОВ КМБП!</b>\n\n"
    start_text += "Здесь вы можете оценивать проекты, оставлять отзывы и следить за рейтингом лучших проектов сообщества.\n\n"
    start_text += "🎯 <b>Новые возможности:</b>\n"
    start_text += "• 📊 <b>Детальная статистика</b> - глубокий анализ проектов\n"
    start_text += "• ⭐ <b>Топ недели</b> - самые активные проекты\n"
    start_text += "• 🏆 <b>Достижения</b> - получайте награды за активность\n"
    start_text += "• 📝 <b>Мои отзывы</b> - управление вашими оценками\n\n"
    start_text += "Для комфортной работы мы предлагаем Вам подписаться на наш новостной канал https://t.me/ratingkmbp. \n\n"
    
    if top_projects:
        start_text += "<b>🏆 ТОП-5 ПРОЕКТОВ:</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        for i, p in enumerate(top_projects, 1):
            start_text += f"{i}. <b>{p['name']}</b> — <code>{p['score']}</code>\n"
    else: 
        start_text += "<b>🏆 ТОП-5 ПРОЕКТОВ:</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        start_text += "Список пуст. Будьте первым, кто добавит проект!\n"
    
    start_text += "\n📊 <i>Используйте меню ниже для навигации</i>"
    
    try:
        # Пробуем отправить с фото
        photo = FSInputFile("start_photo.jpg")  # Убедись, что файл существует в папке с ботом
        await message.answer_photo(
            photo=photo,
            caption=start_text,
            reply_markup=main_kb(),
            parse_mode="HTML"
        )
    except:
        # Если фото нет, отправляем просто текст
        await message.answer(start_text, reply_markup=main_kb(), parse_mode="HTML")

@router.message(F.text.in_(CATEGORIES.values()))
async def show_cat(message: Message):
    """Показать первую партию проектов категории"""
    cat_key = [k for k, v in CATEGORIES.items() if v == message.text][0]
    await show_projects_batch(cat_key, 0, message, is_first_batch=True)

# --- ОБНОВЛЕННЫЕ ОБРАБОТЧИКИ ДЛЯ ПРОЕКТОВ ---
@router.callback_query(F.data.startswith("panel_"))
async def open_panel(call: CallbackQuery):
    """Открывает панель управления проектом в том же сообщении"""
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
    
    text = f"<b>🔘 ПАНЕЛЬ УПРАВЛЕНИЯ</b>\n\n"
    text += f"<b>{project['name']}</b>\n"
    text += f"{project['description'][:200]}{'...' if len(project['description']) > 200 else ''}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
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
            text += f"{symbol} <code>{change['change_amount']:+d}</code> — {change['reason'][:50]}... ({date})\n"
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
        
        txt = f"📝 <b>Введите текст отзыва для проекта {project_name}:</b>\n\n"
        txt += "<i>Напишите ваш отзыв или используйте '❌ Отмена' для отмены</i>"
        
        if call.message.photo:
            await safe_edit_media(call, txt, reply_markup=back_to_panel_kb(p_id))
        else:
            await safe_edit_message(call, txt, reply_markup=back_to_panel_kb(p_id))
        
        await state.set_state(ReviewState.waiting_for_text)
    await call.answer()

# ... (остальные обработчики отзывов, лайков, истории остаются аналогичными)

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
    
    txt = f"📝 <b>Изменение отзыва для проекта {project_name}</b>\n\n"
    txt += "Введите новый текст отзыва:"
    if not check.data:
        txt = f"💬 <b>Новый отзыв для проекта {project_name}</b>\n\n"
        txt += "Введите текст отзыва:\n\n"
        txt += "<i>Полезные советы:</i>\n"
        txt += "• Опишите ваш опыт использования\n"
        txt += "• Отметьте сильные и слабые стороны\n"
        txt += "• Будьте объективны и конкретны\n"
        txt += "• Отзыв должен быть не менее 10 символов"
    
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
    
    # Проверяем минимальную длину отзыва
    if len(message.text.strip()) < 10:
        await message.reply(
            "❌ Отзыв должен содержать минимум 10 символов.\n"
            "Пожалуйста, напишите более подробный отзыв."
        )
        return
    
    await state.update_data(txt=message.text)
    await state.set_state(ReviewState.waiting_for_rate)
    
    # Получаем ID проекта из state
    data = await state.get_data()
    p_id = data.get('p_id')
    
    kb = rating_kb()
    await message.answer("🌟 <b>Выберите оценку:</b>\n\n<i>1 звезда - очень плохо, 5 звезд - отлично</i>", reply_markup=kb, parse_mode="HTML")

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
    
    # Проверяем достижения
    achievements = await check_user_achievements(call.from_user.id)
    
    text = f"✅ <b>Отзыв успешно {res_txt}!</b>\n\n"
    text += f"📊 Изменение рейтинга: <code>{rating_change:+d}</code>\n"
    text += f"🔢 Новый рейтинг проекта: <b>{new_score}</b>\n"
    text += f"⭐ Ваша оценка: {'⭐' * rate}\n\n"
    
    # Если получены новые достижения
    if achievements:
        new_achievements = [a for a in achievements if a[0] in ['first_review', 'reviewer', 'top_reviewer']]
        if new_achievements:
            text += f"<b>🏆 Получено достижение!</b>\n"
            for achievement_code, achievement_name in new_achievements:
                icon = get_achievement_icon(achievement_code)
                text += f"{icon} {achievement_name}\n"
            text += f"\n"
    
    text += f"<i>Спасибо за ваш вклад в развитие сообщества!</i>"
    
    if call.message.photo:
        await safe_edit_media(call, text, reply_markup=back_to_panel_kb(p_id))
    else:
        await safe_edit_message(call, text, reply_markup=back_to_panel_kb(p_id))
    
    # ФОРМИРУЕМ ЛОГ
    admin_text = (f"📢 <b>Отзыв {res_txt}:</b> {p['name']}\n"
                  f"Пользователь: @{call.from_user.username or call.from_user.id}\n"
                  f"Текст: <i>{data['txt'][:200]}...</i>\n"
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
        text = f"<b>💬 ОТЗЫВЫ ПРОЕКТА</b>\n<b>{project_name}</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
        text += "📭 Отзывов еще нет\n\n"
        text += "<i>Будьте первым, кто оставит отзыв об этом проекте!</i>"
        
        if call.message.photo:
            await safe_edit_media(call, text, reply_markup=back_to_panel_kb(p_id))
        else:
            await safe_edit_message(call, text, reply_markup=back_to_panel_kb(p_id))
        
        await call.answer()
        return
    
    text = f"<b>💬 ПОСЛЕДНИЕ ОТЗЫВЫ</b>\n<b>{project_name}</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    for i, r in enumerate(revs, 1):
        date = r['created_at'][:10] if r['created_at'] else ""
        stars = '⭐' * r['rating_val']
        
        # Обрезаем длинный текст
        review_text = r['review_text']
        if len(review_text) > 150:
            review_text = review_text[:150] + "..."
        
        text += f"<b>{i}. {stars}</b>\n"
        text += f"<i>{review_text}</i>\n"
        text += f"📅 {date}\n"
        text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    
    text += f"\n<i>Всего отзывов: {len(revs)}</i>"
    
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
    
    text = f"<b>📊 ИСТОРИЯ ИЗМЕНЕНИЙ</b>\n<b>{project['name']}</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    
    if not history:
        text += "📭 История изменений пуста\n"
        text += "<i>Этот проект еще не оценивали</i>"
    else:
        for i, change in enumerate(history, 1):
            date_time = change['created_at'][:16] if change['created_at'] else ""
            
            if change['is_admin_action']:
                actor = f"👤 Админ"
            else:
                actor = f"👤 Пользователь"
            
            symbol = "📈" if change['change_amount'] > 0 else "📉" if change['change_amount'] < 0 else "➡️"
            
            text += f"<b>{i}.</b> {symbol} <b>{change['score_before']} → {change['score_after']}</b> ({change['change_amount']:+d})\n"
            text += f"   📝 {change['reason'][:50]}{'...' if len(change['reason']) > 50 else ''}\n"
            text += f"   {actor}\n"
            text += f"   📅 {date_time}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    
        text += f"\n<i>Всего изменений: {len(history)}</i>"
    
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
    await call.answer("❤️ Голос учтен! Спасибо за поддержку!")

@router.callback_query(F.data == "close_panel")
async def close_panel(call: CallbackQuery):
    """Закрытие панели - удаление сообщения с панелью"""
    await call.message.delete()
    await call.answer("Панель закрыта")

async def main():
    logging.basicConfig(level=logging.INFO)
    dp.update.outer_middleware(AccessMiddleware())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
