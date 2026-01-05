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

class EditProjectState(StatesGroup):
    waiting_for_description = State()
    waiting_for_photo = State()

class UserAddProjectState(StatesGroup):
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_category = State()

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
    buttons.append([KeyboardButton(text="📤 Добавить проект")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def project_card_kb(p_id):
    """Чистая карточка проекта"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔘 Открыть панель", callback_data=f"panel_{p_id}")],
        [InlineKeyboardButton(text="❌ Удалить карточку", callback_data="delete_message")]
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
        [InlineKeyboardButton(text="❌ Закрыть панель", callback_data="close_panel")]
    ])

def back_to_panel_kb(p_id):
    """Кнопка назад к панели"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к панели", callback_data=f"panel_{p_id}")]
    ])

def categories_kb():
    """Клавиатура выбора категории"""
    buttons = []
    for key, value in CATEGORIES.items():
        buttons.append([InlineKeyboardButton(text=value, callback_data=f"cat_{key}")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_add")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def application_kb(application_id: int, user_id: int):
    """Клавиатура для заявки"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{application_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{application_id}")
        ],
        [InlineKeyboardButton(text="👤 Инфо", callback_data=f"info_{user_id}")]
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

# --- СИСТЕМА ЗАЯВОК ---
async def save_application(user_id: int, category: str, name: str, description: str) -> int:
    """Сохраняет заявку в базу и возвращает ID"""
    try:
        result = supabase.table("applications").insert({
            "user_id": user_id,
            "category": category,
            "name": name,
            "description": description,
            "status": "pending",
            "created_at": "now()"
        }).execute()
        
        if result.data:
            return result.data[0]['id']
    except Exception as e:
        logging.error(f"Ошибка сохранения заявки: {e}")
    return 0

async def update_application_status(application_id: int, status: str, admin_id: int, reason: str = ""):
    """Обновляет статус заявки"""
    try:
        supabase.table("applications").update({
            "status": status,
            "reviewed_by": admin_id,
            "review_reason": reason,
            "reviewed_at": "now()"
        }).eq("id", application_id).execute()
        return True
    except Exception as e:
        logging.error(f"Ошибка обновления заявки: {e}")
    return False

async def send_pinned_application(application_id: int, user_id: int, category: str, name: str, description: str):
    """Отправляет заявку с пин-сообщением"""
    try:
        # Получаем топик для категории
        topic_id = TOPICS_BY_CATEGORY.get(category, TOPIC_LOGS_ALL)
        
        # Формируем текст заявки
        app_text = f"📋 <b>НОВАЯ ЗАЯВКА НА ПРОЕКТ</b>\n\n"
        app_text += f"🆔 ID заявки: <code>{application_id}</code>\n"
        app_text += f"👤 ID пользователя: <code>{user_id}</code>\n"
        app_text += f"📂 Категория: <code>{category}</code>\n"
        app_text += f"🏷 Название: <b>{name}</b>\n"
        app_text += f"📝 Описание: {description}\n\n"
        app_text += f"<i>Заявка ожидает рассмотрения</i>"
        
        # Отправляем сообщение в нужный топик
        message = await bot.send_message(
            ADMIN_GROUP_ID,
            app_text,
            message_thread_id=topic_id,
            reply_markup=application_kb(application_id, user_id),
            parse_mode="HTML"
        )
        
        # Закрепляем сообщение
        try:
            await bot.pin_chat_message(
                chat_id=ADMIN_GROUP_ID,
                message_id=message.message_id,
                disable_notification=True
            )
            logging.info(f"Заявка {application_id} закреплена в топике {topic_id}")
        except Exception as e:
            logging.error(f"Ошибка закрепления сообщения: {e}")
        
        return message.message_id
        
    except Exception as e:
        logging.error(f"Ошибка отправки заявки: {e}")
    return 0

async def unpin_and_update_application(application_id: int, message_id: int, status: str, admin_id: int, reason: str = ""):
    """Открепляет сообщение и обновляет статус заявки"""
    try:
        # Открепляем сообщение
        await bot.unpin_chat_message(
            chat_id=ADMIN_GROUP_ID,
            message_id=message_id
        )
        
        # Обновляем статус заявки в базе
        await update_application_status(application_id, status, admin_id, reason)
        
        return True
    except Exception as e:
        logging.error(f"Ошибка открепления сообщения: {e}")
    return False

# --- ЛОГИКА ДОБАВЛЕНИЯ ПРОЕКТОВ ПОЛЬЗОВАТЕЛЯМИ ---

@router.message(F.text == "📤 Добавить проект")
async def user_add_project_start(message: Message, state: FSMContext):
    """Начало добавления проекта пользователем"""
    await state.clear()
    await state.set_state(UserAddProjectState.waiting_for_category)
    
    text = "📋 ДОБАВЛЕНИЕ НОВОГО ПРОЕКТА\n\n"
    text += "Выберите категорию для вашего проекта:\n\n"
    text += "ℹ️ Ваша заявка будет отправлена администраторам на проверку"
    
    await message.answer(text, reply_markup=categories_kb(), parse_mode="HTML")

@router.callback_query(F.data.startswith("cat_"), UserAddProjectState.waiting_for_category)
async def user_select_category(call: CallbackQuery, state: FSMContext):
    """Пользователь выбрал категорию"""
    category_key = call.data.split("_")[1]
    await state.update_data(category_key=category_key, category_name=CATEGORIES[category_key])
    await state.set_state(UserAddProjectState.waiting_for_name)
    
    text = f"📋 ДОБАВЛЕНИЕ ПРОЕКТА\n\n"
    text += f"📂 Категория: <b>{CATEGORIES[category_key]}</b>\n\n"
    text += "📝 Теперь введите <b>название проекта</b>:\n\n"
    text += "Название должно быть уникальным и отражать суть проекта"
    
    await safe_edit_message(call, text, reply_markup=None)
    await call.answer()

@router.message(UserAddProjectState.waiting_for_name)
async def user_enter_name(message: Message, state: FSMContext):
    """Пользователь ввел название проекта"""
    name = message.text.strip()
    
    if not name:
        await message.reply("❌ Название проекта не может быть пустым.")
        return
    
    if len(name) > 100:
        await message.reply("❌ Название проекта слишком длинное (макс. 100 символов).")
        return
    
    # Проверяем уникальность названия
    existing = supabase.table("projects").select("*").ilike("name", name).execute()
    if existing.data:
        await message.reply("❌ Проект с таким названием уже существует. Придумайте другое название.")
        return
    
    await state.update_data(project_name=name)
    await state.set_state(UserAddProjectState.waiting_for_description)
    
    text = f"📋 ДОБАВЛЕНИЕ ПРОЕКТА\n\n"
    text += f"📂 Категория: <b>{(await state.get_data())['category_name']}</b>\n"
    text += f"🏷 Название: <b>{name}</b>\n\n"
    text += "📝 Теперь введите <b>описание проекта</b>:\n\n"
    text += "Опишите ваш проект подробно (макс. 500 символов)"
    
    await message.answer(text, parse_mode="HTML")

@router.message(UserAddProjectState.waiting_for_description)
async def user_enter_description(message: Message, state: FSMContext):
    """Пользователь ввел описание проекта"""
    description = message.text.strip()
    
    if not description:
        await message.reply("❌ Описание проекта не может быть пустым.")
        return
    
    if len(description) > 500:
        await message.reply("❌ Описание слишком длинное (макс. 500 символов).")
        return
    
    data = await state.get_data()
    
    # Сохраняем заявку в базу
    application_id = await save_application(
        user_id=message.from_user.id,
        category=data['category_key'],
        name=data['project_name'],
        description=description
    )
    
    if application_id:
        # Отправляем заявку с пин-сообщением
        await send_pinned_application(
            application_id=application_id,
            user_id=message.from_user.id,
            category=data['category_key'],
            name=data['project_name'],
            description=description
        )
        
        # Отправляем подтверждение пользователю
        user_text = f"✅ ЗАЯВКА ОТПРАВЛЕНА!\n\n"
        user_text += f"Ваша заявка на добавление проекта отправлена администраторам.\n\n"
        user_text += f"Детали заявки:\n"
        user_text += f"📂 Категория: {data['category_name']}\n"
        user_text += f"🏷 Название: {data['project_name']}\n"
        user_text += f"📝 Описание: {description[:100]}...\n\n"
        user_text += f"Ожидайте рассмотрения заявки администраторами."
        
        await message.answer(user_text, reply_markup=main_kb(), parse_mode="HTML")
    else:
        await message.reply("❌ Произошла ошибка при отправке заявки. Попробуйте позже.")
    
    await state.clear()

@router.callback_query(F.data == "cancel_add")
async def cancel_add_project(call: CallbackQuery, state: FSMContext):
    """Отмена добавления проекта"""
    await state.clear()
    await safe_edit_message(call, "❌ Добавление проекта отменено.", reply_markup=None)
    await call.answer("Добавление отменено")

@router.callback_query(F.data.startswith("approve_"))
async def approve_application(call: CallbackQuery):
    """Одобрение заявки"""
    if not await is_user_admin(call.from_user.id):
        await call.answer("❌ Эта функция доступна только администраторам.", show_alert=True)
        return
    
    application_id = int(call.data.split("_")[1])
    
    try:
        # Получаем информацию о заявке
        result = supabase.table("applications").select("*").eq("id", application_id).execute()
        if not result.data:
            await call.answer("Заявка не найдена.", show_alert=True)
            return
        
        app = result.data[0]
        
        # Проверяем, существует ли уже проект с таким названием
        existing = supabase.table("projects").select("*").eq("name", app['name']).execute()
        if existing.data:
            # Обновляем статус заявки
            await unpin_and_update_application(
                application_id=application_id,
                message_id=call.message.message_id,
                status="rejected",
                admin_id=call.from_user.id,
                reason="Проект с таким названием уже существует"
            )
            
            # Обновляем сообщение
            await safe_edit_message(
                call,
                f"❌ ЗАЯВКА ОТКЛОНЕНА\n\n"
                f"ID: <code>{application_id}</code>\n"
                f"Причина: Проект с таким названием уже существует\n"
                f"Админ: <code>{call.from_user.id}</code>",
                reply_markup=None
            )
            
            await call.answer("Заявка отклонена: проект уже существует")
            return
        
        # Добавляем проект
        result = supabase.table("projects").insert({
            "name": app['name'], 
            "category": app['category'], 
            "description": app['description'],
            "score": 0
        }).execute()
        
        if result.data:
            # Добавляем запись в историю
            supabase.table("rating_history").insert({
                "project_id": result.data[0]['id'],
                "admin_id": call.from_user.id,
                "change_type": "create",
                "score_before": 0,
                "score_after": 0,
                "change_amount": 0,
                "reason": "Создание проекта (одобрена заявка)",
                "is_admin_action": True
            }).execute()
            
            # Обновляем статус заявки
            await unpin_and_update_application(
                application_id=application_id,
                message_id=call.message.message_id,
                status="approved",
                admin_id=call.from_user.id,
                reason="Заявка одобрена"
            )
            
            # Обновляем сообщение
            await safe_edit_message(
                call,
                f"✅ ЗАЯВКА ОДОБРЕНА\n\n"
                f"ID: <code>{application_id}</code>\n"
                f"Проект добавлен: <b>{app['name']}</b>\n"
                f"Админ: <code>{call.from_user.id}</code>",
                reply_markup=None
            )
            
            # Отправляем уведомление пользователю
            try:
                await bot.send_message(
                    app['user_id'],
                    f"✅ Ваша заявка на проект <b>{app['name']}</b> была одобрена!\n\n"
                    f"Теперь проект доступен в разделе {CATEGORIES[app['category']]}",
                    parse_mode="HTML"
                )
            except:
                pass  # Игнорируем ошибку отправки пользователю
            
            await call.answer("Заявка одобрена")
            
        else:
            await call.answer("Ошибка при добавлении проекта", show_alert=True)
            
    except Exception as e:
        logging.error(f"Ошибка одобрения заявки: {e}")
        await call.answer("Ошибка при обработке заявки", show_alert=True)

@router.callback_query(F.data.startswith("reject_"))
async def reject_application(call: CallbackQuery, state: FSMContext):
    """Отклонение заявки"""
    if not await is_user_admin(call.from_user.id):
        await call.answer("❌ Эта функция доступна только администраторам.", show_alert=True)
        return
    
    application_id = int(call.data.split("_")[1])
    
    # Сохраняем ID заявки и переходим к вводу причины
    await state.update_data(application_id=application_id, message_id=call.message.message_id)
    
    text = f"📝 УКАЖИТЕ ПРИЧИНУ ОТКЛОНЕНИЯ\n\n"
    text += f"Заявка ID: <code>{application_id}</code>\n\n"
    text += "Введите причину отклонения заявки:"
    
    await safe_edit_message(call, text, reply_markup=None)
    await call.answer()

@router.message(F.text, F.reply_to_message)
async def handle_reject_reason(message: Message, state: FSMContext):
    """Обработка причины отклонения заявки"""
    if not await is_user_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    if 'application_id' not in data:
        return
    
    application_id = data['application_id']
    message_id = data['message_id']
    reason = message.text.strip()
    
    if not reason:
        await message.reply("❌ Причина не может быть пустой.")
        return
    
    # Получаем информацию о заявке
    result = supabase.table("applications").select("*").eq("id", application_id).execute()
    if not result.data:
        await message.reply("Заявка не найдена.")
        await state.clear()
        return
    
    app = result.data[0]
    
    # Обновляем статус заявки
    await unpin_and_update_application(
        application_id=application_id,
        message_id=message_id,
        status="rejected",
        admin_id=message.from_user.id,
        reason=reason
    )
    
    # Обновляем оригинальное сообщение с заявкой
    try:
        await bot.edit_message_text(
            chat_id=ADMIN_GROUP_ID,
            message_id=message_id,
            text=f"❌ ЗАЯВКА ОТКЛОНЕНА\n\n"
                 f"ID: <code>{application_id}</code>\n"
                 f"Причина: {reason}\n"
                 f"Админ: <code>{message.from_user.id}</code>",
            parse_mode="HTML"
        )
    except:
        pass
    
    # Отправляем уведомление пользователю
    try:
        await bot.send_message(
            app['user_id'],
            f"❌ Ваша заявка на проект <b>{app['name']}</b> была отклонена.\n\n"
            f"Причина: {reason}",
            parse_mode="HTML"
        )
    except:
        pass  # Игнорируем ошибку отправки пользователю
    
    await message.reply(f"✅ Заявка ID {application_id} отклонена.")
    await state.clear()

@router.callback_query(F.data.startswith("info_"))
async def show_user_info(call: CallbackQuery):
    """Показать информацию о пользователе"""
    if not await is_user_admin(call.from_user.id):
        await call.answer("❌ Эта функция доступна только администраторам.", show_alert=True)
        return
    
    user_id = int(call.data.split("_")[1])
    
    # Получаем информацию о пользователе
    try:
        # Получаем историю заявок пользователя
        apps_result = supabase.table("applications")\
            .select("*")\
            .eq("user_id", user_id)\
            .order("created_at", desc=True)\
            .limit(5)\
            .execute()
        
        apps = apps_result.data if apps_result.data else []
        
        # Получаем историю отзывов пользователя
        reviews_result = supabase.table("user_logs")\
            .select("*")\
            .eq("user_id", user_id)\
            .eq("action_type", "review")\
            .order("created_at", desc=True)\
            .limit(5)\
            .execute()
        
        reviews = reviews_result.data if reviews_result.data else []
        
        text = f"👤 ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ\n\n"
        text += f"ID: <code>{user_id}</code>\n"
        text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        
        text += f"📋 Заявки ({len(apps)}):\n"
        for app in apps:
            status = "✅" if app['status'] == "approved" else "❌" if app['status'] == "rejected" else "⏳"
            text += f"{status} {app['name']} ({app['status']})\n"
        
        text += f"\n💬 Отзывы ({len(reviews)}):\n"
        for rev in reviews:
            date = rev['created_at'][:10] if rev['created_at'] else ""
            text += f"⭐{rev['rating_val']} ({date})\n"
        
        await call.answer(text, show_alert=True)
        
    except Exception as e:
        logging.error(f"Ошибка получения информации о пользователе: {e}")
        await call.answer("Ошибка при получении информации", show_alert=True)

@router.callback_query(F.data == "delete_message")
async def delete_message(call: CallbackQuery):
    """Удаление сообщения с карточкой проекта"""
    try:
        await call.message.delete()
    except Exception as e:
        logging.error(f"Ошибка удаления сообщения: {e}")
        await call.answer("Не удалось удалить сообщение")
    await call.answer()

@router.callback_query(F.data == "close_panel")
async def close_panel(call: CallbackQuery):
    """Закрытие панели - возврат к карточке проекта"""
    # Получаем ID проекта из текущего сообщения
    try:
        text = call.message.text or call.message.caption or ""
        lines = text.split('\n')
        project_name = ""
        
        for line in lines:
            if line.startswith('<b>') and '</b>' in line and not 'ПАНЕЛЬ' in line:
                project_name = line.replace('<b>', '').replace('</b>', '').strip()
                break
        
        if project_name:
            project = await find_project_by_name(project_name)
            if project:
                photo_file_id = await get_project_photo(project['id'])
                
                card = f"<b>{project['name']}</b>\n\n{project['description']}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                card += f"Текущий рейтинг: <b>{project['score']}</b>\n\n"
                card += f"Нажмите кнопку ниже для управления проектом"
                
                if photo_file_id and call.message.photo:
                    await safe_edit_media(call, card, reply_markup=project_card_kb(project['id']))
                else:
                    await safe_edit_message(call, card, reply_markup=project_card_kb(project['id']))
            else:
                await call.answer("Проект не найден")
        else:
            await call.answer("Не удалось определить проект")
    except Exception as e:
        logging.error(f"Ошибка закрытия панели: {e}")
        await call.answer("Ошибка закрытия панели")

# --- АДМИН-КОМАНДЫ (без юзернеймов) ---

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
                "change_type": "create",
                "score_before": 0,
                "score_after": 0,
                "change_amount": 0,
                "reason": "Создание проекта",
                "is_admin_action": True
            }).execute()
            
            # Отправляем лог
            log_text = (f"📋 Добавлен новый проект:\n\n"
                       f"🏷 Название: <b>{name}</b>\n"
                       f"📂 Категория: <code>{cat}</code>\n"
                       f"📝 Описание: {desc}\n"
                       f"👤 Админ ID: <code>{message.from_user.id}</code>")
            
            await send_log_to_topics(log_text, cat)
            
            await message.reply(
                f"✅ Проект <b>{name}</b> успешно добавлен!\n"
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
            await message.reply(
                f"❌ Проект <b>{name}</b> не найден!",
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
        log_text = (f"🗑 Проект удален:\n\n"
                   f"🏷 Название: <b>{project['name']}</b>\n"
                   f"📂 Категория: <code>{category}</code>\n"
                   f"📊 Удалено отзывов: {reviews_num}\n"
                   f"🔢 Финальный рейтинг: {score}\n"
                   f"👤 Админ ID: <code>{message.from_user.id}</code>")
        
        await send_log_to_topics(log_text, category)
        
        await message.reply(
            f"🗑 Проект <b>{project['name']}</b> удален!\n"
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
            await message.reply(
                f"❌ <b>{val_str}</b> не является числом!",
                parse_mode="HTML"
            )
            return
        
        # Ищем проект по названию
        project = await find_project_by_name(name)
        if not project:
            await message.reply(
                f"❌ Проект <b>{name}</b> не найден!",
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
        await message.reply(
            f"📝 Укажите причину изменения рейтинга для проекта <i>{project['name']}</i>:\n\n"
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
            "change_type": "admin_change",
            "score_before": old_score,
            "score_after": new_score,
            "change_amount": change_amount,
            "reason": reason,
            "is_admin_action": True
        }).execute()
        
        # Отправляем лог
        log_text = (f"⚖️ Изменен рейтинг проекта:\n\n"
                   f"🏷 Название: <b>{project_name}</b>\n"
                   f"📂 Категория: <code>{category}</code>\n"
                   f"🔢 Было: <b>{old_score}</b>\n"
                   f"🔢 Стало: <b>{new_score}</b>\n"
                   f"📊 Изменение: <code>{change_amount:+d}</code>\n"
                   f"📝 Причина: <i>{reason}</i>\n"
                   f"👤 Админ ID: <code>{message.from_user.id}</code>")
        
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

# ... остальные админ-команды аналогично без юзернеймов ...

# --- ЛОГИКА ПОЛЬЗОВАТЕЛЯ ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    top = supabase.table("projects").select("*").order("score", desc=True).limit(5).execute().data
    
    # Стартовое сообщение
    start_text = "ДОБРО ПОЖАЛОВАТЬ В РЕЙТИНГ ПРОЕКТОВ КМБП\n\n"
    start_text += "Здесь вы можете оценивать проекты, оставлять отзывы и следить за рейтингом лучших проектов сообщества.\n\n"
    
    if top:
        start_text += "ТОП-5 ПРОЕКТОВ:\n"
        for i, p in enumerate(top, 1):
            start_text += f"{i}. {p['name']} — {p['score']}\n"
    else: 
        start_text += "ТОП-5 ПРОЕКТОВ:\n"
        start_text += "Список пуст. Будьте первым, кто добавит проект!\n"
    
    start_text += "\nВыберите категорию или добавьте свой проект"
    
    try:
        photo = FSInputFile("start_photo.jpg")
        await message.answer_photo(
            photo=photo,
            caption=start_text,
            reply_markup=main_kb(),
            parse_mode="HTML"
        )
    except:
        await message.answer(start_text, reply_markup=main_kb(), parse_mode="HTML")

@router.message(F.text.in_(CATEGORIES.values()))
async def show_cat(message: Message):
    cat_key = [k for k, v in CATEGORIES.items() if v == message.text][0]
    data = supabase.table("projects").select("*").eq("category", cat_key).order("score", desc=True).execute().data
    if not data: 
        await message.answer(f"В разделе '{message.text}' пока нет проектов.", parse_mode="HTML")
        return
    
    for p in data:
        photo_file_id = await get_project_photo(p['id'])
        
        card = f"<b>{p['name']}</b>\n\n{p['description']}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        card += f"Текущий рейтинг: <b>{p['score']}</b>\n\n"
        card += f"Нажмите кнопку ниже для управления проектом"
        
        if photo_file_id:
            try:
                await message.answer_photo(
                    photo=photo_file_id,
                    caption=card,
                    reply_markup=project_card_kb(p['id']),
                    parse_mode="HTML"
                )
            except:
                await message.answer(card, reply_markup=project_card_kb(p['id']), parse_mode="HTML")
        else:
            await message.answer(card, reply_markup=project_card_kb(p['id']), parse_mode="HTML")

@router.callback_query(F.data.startswith("panel_"))
async def open_panel(call: CallbackQuery):
    """Открывает панель управления проектом в том же сообщении"""
    p_id = call.data.split("_")[1]
    
    project = supabase.table("projects").select("*").eq("id", p_id).single().execute().data
    if not project:
        await call.answer("Проект не найден.", show_alert=True)
        return
    
    photo_file_id = await get_project_photo(p_id)
    
    recent_changes = supabase.table("rating_history").select("*")\
        .eq("project_id", p_id)\
        .order("created_at", desc=True)\
        .limit(2)\
        .execute().data
    
    text = f"ПАНЕЛЬ УПРАВЛЕНИЯ\n\n"
    text += f"<b>{project['name']}</b>\n"
    text += f"{project['description']}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    text += f"Текущий рейтинг: <b>{project['score']}</b>\n\n"
    
    if recent_changes:
        text += f"Последние изменения:\n"
        for change in recent_changes:
            date = change['created_at'][:10] if change['created_at'] else ""
            symbol = "📈" if change['change_amount'] > 0 else "📉" if change['change_amount'] < 0 else "➡️"
            text += f"{symbol} {change['change_amount']:+d} — {change['reason'][:50]}... ({date})\n"
        text += f"\n"
    
    text += f"Выберите действие:"
    
    if call.message.photo:
        await safe_edit_media(call, text, reply_markup=project_panel_kb(p_id))
    else:
        await safe_edit_message(call, text, reply_markup=project_panel_kb(p_id))
    
    await call.answer()

# ... остальные функции пользователя (review, viewrev, history, like) аналогично без юзернеймов ...

async def main():
    logging.basicConfig(level=logging.INFO)
    dp.update.outer_middleware(AccessMiddleware())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
