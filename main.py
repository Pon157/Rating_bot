import asyncio
import os
import logging
import secrets
from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, FSInputFile, InlineQuery,
    InlineQueryResultArticle, InputTextMessageContent, ChosenInlineResult
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from supabase import create_client, Client
from dotenv import load_dotenv

# --- НАСТРОЙКИ ТОПИКОВ (Замени цифры на ID из ссылок) ---
TOPIC_LOGS_ALL = 46  # Общий топик для ВСЕХ логов/отзывов
TOPIC_REQUESTS = 420  # Топик для заявок на проекты (добавь свой ID)

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

class AddProjectState(StatesGroup):
    waiting_for_name = State()
    waiting_for_category = State()
    waiting_for_description = State()

class AdminScoreState(StatesGroup):
    waiting_for_reason = State()

class EditProjectState(StatesGroup):
    waiting_for_description = State()
    waiting_for_photo = State()

# --- ГЕНЕРАЦИЯ ПУБЛИЧНОГО ID ---
def generate_public_id():
    """Генерирует уникальный короткий ID для проекта"""
    return secrets.token_urlsafe(8)[:10]

async def get_or_create_public_id(project_id: int):
    """Получает или создает публичный ID для проекта"""
    try:
        project = supabase.table("projects").select("public_id").eq("id", project_id).single().execute().data
        if project.get('public_id'):
            return project['public_id']
        
        # Генерируем новый уникальный ID
        while True:
            public_id = generate_public_id()
            check = supabase.table("projects").select("id").eq("public_id", public_id).execute()
            if not check.data:
                supabase.table("projects").update({"public_id": public_id}).eq("id", project_id).execute()
                return public_id
    except Exception as e:
        logging.error(f"Ошибка генерации public_id: {e}")
        return None

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
        if not user or user.is_bot: 
            return await handler(event, data)
        
        if await is_user_admin(user.id): 
            return await handler(event, data)
        
        res = supabase.table("banned_users").select("user_id").eq("user_id", user.id).execute()
        if res.data: 
            return
        
        return await handler(event, data)

# --- КЛАВИАТУРЫ ---
def main_kb():
    buttons = [
        [KeyboardButton(text=v) for v in list(CATEGORIES.values())[:3]],
        [KeyboardButton(text=v) for v in list(CATEGORIES.values())[3:]],
        [KeyboardButton(text="📤 Добавить проект")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def project_card_kb(p_id, public_id=None):
    """Карточка проекта с кнопкой поделиться"""
    buttons = [
        [InlineKeyboardButton(text="🔘 Открыть панель", callback_data=f"panel_{p_id}")]
    ]
    
    if public_id:
        share_text = f"https://t.me/share/url?url=https://t.me/your_bot?start=project_{public_id}"
        buttons.append([InlineKeyboardButton(text="🔗 Поделиться", url=share_text)])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def project_panel_kb(p_id, public_id=None):
    """Полная панель действий"""
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
    
    if public_id:
        share_text = f"https://t.me/share/url?url=https://t.me/your_bot?start=project_{public_id}"
        buttons.append([InlineKeyboardButton(text="🔗 Поделиться проектом", url=share_text)])
    
    buttons.append([InlineKeyboardButton(text="❌ Закрыть", callback_data=f"close_{p_id}")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def back_to_panel_kb(p_id):
    """Кнопка назад к панели"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к панели", callback_data=f"panel_{p_id}")]
    ])

def choose_category_kb():
    """Выбор категории для добавления проекта"""
    buttons = []
    for cat_key, cat_name in CATEGORIES.items():
        buttons.append([InlineKeyboardButton(text=cat_name, callback_data=f"addcat_{cat_key}")])
    
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_add")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def request_decision_kb(request_id):
    """Кнопки для модерации заявки"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{request_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{request_id}")
        ]
    ])

# --- ФУНКЦИЯ ОТПРАВКИ ЛОГОВ ---
async def send_log_to_topics(admin_text: str, category: str = None, topic_id: int = None):
    """Отправляет лог во все нужные топики"""
    try:
        # Если указан конкретный топик
        if topic_id:
            await bot.send_message(
                ADMIN_GROUP_ID, 
                admin_text, 
                message_thread_id=topic_id, 
                parse_mode="HTML"
            )
            return
        
        # 1. Шлем в общий топик логов
        if TOPIC_LOGS_ALL:
            await bot.send_message(
                ADMIN_GROUP_ID, 
                admin_text, 
                message_thread_id=TOPIC_LOGS_ALL, 
                parse_mode="HTML"
            )
        
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
        
        # 3. Если общий топик не указан, отправляем в основной чат
        elif not TOPIC_LOGS_ALL and ADMIN_GROUP_ID:
            await bot.send_message(ADMIN_GROUP_ID, admin_text, parse_mode="HTML")
            
    except Exception as e:
        logging.error(f"Ошибка отправки лога: {e}")

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
async def safe_edit_message(call: CallbackQuery, text: str, reply_markup=None, parse_mode="HTML"):
    """Безопасное редактирование сообщения"""
    try:
        await call.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        if "message is not modified" in str(e):
            await call.answer()
        else:
            logging.error(f"Ошибка редактирования сообщения: {e}")

async def safe_edit_media(call: CallbackQuery, caption: str, reply_markup=None, parse_mode="HTML"):
    """Безопасное редактирование сообщения с медиа"""
    try:
        await call.message.edit_caption(caption=caption, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        if "message is not modified" in str(e):
            await call.answer()
        else:
            logging.error(f"Ошибка редактирования медиа: {e}")

async def get_project_photo(project_id: int):
    """Получает фото проекта из базы"""
    try:
        result = supabase.table("project_photos").select("*").eq("project_id", project_id).execute()
        if result.data:
            return result.data[0].get('photo_file_id', '')
    except Exception as e:
        logging.error(f"Ошибка получения фото: {e}")
    return None

async def find_project_by_name(name: str):
    """Находит проект по названию"""
    try:
        result = supabase.table("projects").select("*").ilike("name", f"%{name}%").execute()
        if result.data:
            return result.data[0]
    except Exception as e:
        logging.error(f"Ошибка поиска проекта: {e}")
    return None

async def get_project_by_public_id(public_id: str):
    """Находит проект по публичному ID"""
    try:
        result = supabase.table("projects").select("*").eq("public_id", public_id).execute()
        if result.data:
            return result.data[0]
    except Exception as e:
        logging.error(f"Ошибка поиска проекта по public_id: {e}")
    return None

# --- ОБРАБОТКА СТАРТА С ПАРАМЕТРАМИ ---
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    
    # Проверяем, есть ли параметры в стартовой ссылке
    if len(message.text.split()) > 1:
        param = message.text.split()[1]
        
        # Если ссылка на проект
        if param.startswith("project_"):
            public_id = param.replace("project_", "")
            project = await get_project_by_public_id(public_id)
            
            if project:
                # Показываем проект
                await show_project_card(message, project['id'])
                return
    
    # Обычный старт
    top = supabase.table("projects").select("*").order("score", desc=True).limit(5).execute().data
    
    start_text = "<b>🌟 ДОБРО ПОЖАЛОВАТЬ В РЕЙТИНГ ПРОЕКТОВ!</b>\n\n"
    start_text += "Здесь вы можете оценивать проекты, оставлять отзывы и следить за рейтингом лучших проектов.\n\n"
    
    if top:
        start_text += "<b>🏆 ТОП-5 ПРОЕКТОВ:</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        for i, p in enumerate(top, 1):
            start_text += f"{i}. <b>{p['name']}</b> — <code>{p['score']}</code>\n"
    else: 
        start_text += "<b>🏆 ТОП-5 ПРОЕКТОВ:</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        start_text += "Список пуст. Добавьте первый проект!\n"
    
    start_text += "\n📊 <i>Выберите действие ниже</i>"
    
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

# --- ПОКАЗ ПРОЕКТА ---
async def show_project_card(message, project_id, edit_message=None):
    """Показывает карточку проекта"""
    project = supabase.table("projects").select("*").eq("id", project_id).single().execute().data
    if not project:
        return
    
    public_id = await get_or_create_public_id(project_id)
    photo_file_id = await get_project_photo(project_id)
    
    card = f"<b>{project['name']}</b>\n\n{project['description']}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    card += f"📊 Текущий рейтинг: <b>{project['score']}</b>\n\n"
    card += f"<i>Нажмите кнопку ниже для управления проектом</i>"
    
    if edit_message:
        if edit_message.photo:
            await edit_message.edit_caption(
                caption=card,
                reply_markup=project_card_kb(project_id, public_id),
                parse_mode="HTML"
            )
        else:
            await edit_message.edit_text(
                text=card,
                reply_markup=project_card_kb(project_id, public_id),
                parse_mode="HTML"
            )
    else:
        if photo_file_id:
            try:
                await message.answer_photo(
                    photo=photo_file_id,
                    caption=card,
                    reply_markup=project_card_kb(project_id, public_id),
                    parse_mode="HTML"
                )
            except:
                await message.answer(card, reply_markup=project_card_kb(project_id, public_id), parse_mode="HTML")
        else:
            await message.answer(card, reply_markup=project_card_kb(project_id, public_id), parse_mode="HTML")

# --- ДОБАВЛЕНИЕ ПРОЕКТА ПОЛЬЗОВАТЕЛЕМ ---
@router.message(F.text == "📤 Добавить проект")
async def user_add_project_start(message: Message, state: FSMContext):
    await state.set_state(AddProjectState.waiting_for_name)
    await message.answer(
        "📝 <b>Добавление нового проекта</b>\n\n"
        "Введите название вашего проекта:",
        parse_mode="HTML"
    )

@router.message(AddProjectState.waiting_for_name)
async def user_add_project_name(message: Message, state: FSMContext):
    if not message.text or len(message.text.strip()) < 2:
        await message.answer("❌ Название должно содержать минимум 2 символа.")
        return
    
    # Проверяем, не существует ли уже проект с таким названием
    existing = supabase.table("projects").select("*").ilike("name", f"%{message.text.strip()}%").execute()
    if existing.data:
        await message.answer("❌ Проект с таким названием уже существует!")
        return
    
    await state.update_data(name=message.text.strip())
    await state.set_state(AddProjectState.waiting_for_category)
    
    await message.answer(
        "📂 <b>Выберите категорию проекта:</b>",
        reply_markup=choose_category_kb(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("addcat_"))
async def user_add_project_category(call: CallbackQuery, state: FSMContext):
    category = call.data.replace("addcat_", "")
    
    if category not in CATEGORIES:
        await call.answer("Неверная категория!")
        return
    
    await state.update_data(category=category)
    await state.set_state(AddProjectState.waiting_for_description)
    
    await call.message.edit_text(
        "📝 <b>Введите описание проекта:</b>\n\n"
        "<i>Опишите ваш проект подробно. Это поможет другим пользователям понять его суть.</i>",
        parse_mode="HTML"
    )
    
    await call.answer()

@router.message(AddProjectState.waiting_for_description)
async def user_add_project_description(message: Message, state: FSMContext):
    if not message.text or len(message.text.strip()) < 10:
        await message.answer("❌ Описание должно содержать минимум 10 символов.")
        return
    
    data = await state.get_data()
    
    # Сохраняем заявку
    try:
        result = supabase.table("project_requests").insert({
            "name": data['name'],
            "category": data['category'],
            "description": message.text.strip(),
            "user_id": message.from_user.id,
            "status": "pending"
        }).execute()
        
        if result.data:
            request_id = result.data[0]['id']
            
            # Отправляем заявку в топик для модерации
            request_text = (
                f"📥 <b>НОВАЯ ЗАЯВКА НА ПРОЕКТ</b>\n\n"
                f"🆔 ID заявки: <code>{request_id}</code>\n"
                f"🏷 Название: <b>{data['name']}</b>\n"
                f"📂 Категория: <code>{data['category']}</code>\n"
                f"📝 Описание: {message.text.strip()}\n"
                f"👤 Пользователь: ID{message.from_user.id}\n\n"
                f"<i>Заявка ожидает модерации</i>"
            )
            
            await send_log_to_topics(request_text, topic_id=TOPIC_REQUESTS)
            
            await message.answer(
                "✅ <b>Заявка успешно отправлена!</b>\n\n"
                "Ваш проект отправлен на модерацию. Обычно это занимает до 24 часов.\n"
                "Вы получите уведомление, когда заявка будет рассмотрена.",
                parse_mode="HTML"
            )
        else:
            await message.answer("❌ Ошибка при отправке заявки. Попробуйте позже.")
    
    except Exception as e:
        logging.error(f"Ошибка сохранения заявки: {e}")
        await message.answer("❌ Произошла ошибка. Попробуйте позже.")
    
    await state.clear()

@router.callback_query(F.data == "cancel_add")
async def cancel_add_project(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.delete()
    await call.answer("Добавление проекта отменено")

# --- МОДЕРАЦИЯ ЗАЯВОК (АДМИН) ---
@router.callback_query(F.data.startswith("approve_"))
async def approve_project_request(call: CallbackQuery):
    if not await is_user_admin(call.from_user.id):
        await call.answer("Нет прав!")
        return
    
    request_id = int(call.data.replace("approve_", ""))
    
    # Получаем заявку
    request = supabase.table("project_requests").select("*").eq("id", request_id).single().execute().data
    if not request or request['status'] != 'pending':
        await call.answer("Заявка уже обработана!")
        return
    
    # Добавляем проект
    result = supabase.table("projects").insert({
        "name": request['name'],
        "category": request['category'],
        "description": request['description'],
        "score": 0
    }).execute()
    
    if result.data:
        project_id = result.data[0]['id']
        
        # Обновляем статус заявки
        supabase.table("project_requests").update({
            "status": "approved",
            "admin_id": call.from_user.id,
            "processed_at": "now()"
        }).eq("id", request_id).execute()
        
        # Добавляем запись в историю
        supabase.table("rating_history").insert({
            "project_id": project_id,
            "admin_id": call.from_user.id,
            "change_type": "create",
            "score_before": 0,
            "score_after": 0,
            "change_amount": 0,
            "reason": "Создание проекта через заявку",
            "is_admin_action": True
        }).execute()
        
        # Отправляем лог
        log_text = (
            f"✅ <b>ЗАЯВКА ОДОБРЕНА</b>\n\n"
            f"🆔 Заявка: <code>{request_id}</code>\n"
            f"🏷 Проект: <b>{request['name']}</b>\n"
            f"🆔 ID проекта: <code>{project_id}</code>\n"
            f"📂 Категория: <code>{request['category']}</code>\n"
            f"👤 Одобрил: ID{call.from_user.id}"
        )
        
        await send_log_to_topics(log_text, request['category'])
        
        # Уведомляем пользователя
        try:
            await bot.send_message(
                request['user_id'],
                f"✅ <b>Ваша заявка одобрена!</b>\n\n"
                f"Проект <b>{request['name']}</b> успешно добавлен в рейтинг.\n"
                f"Теперь другие пользователи могут оценивать его и оставлять отзывы.",
                parse_mode="HTML"
            )
        except:
            pass
        
        # Обновляем сообщение с заявкой
        await call.message.edit_text(
            f"✅ <b>ЗАЯВКА ОДОБРЕНА</b>\n\n"
            f"🆔 Заявка: <code>{request_id}</code>\n"
            f"🏷 Проект: <b>{request['name']}</b>\n"
            f"🆔 ID проекта: <code>{project_id}</code>\n"
            f"📂 Категория: <code>{request['category']}</code>\n"
            f"👤 Одобрил: ID{call.from_user.id}",
            parse_mode="HTML"
        )
        
        await call.answer("Заявка одобрена!")
    else:
        await call.answer("Ошибка при добавлении проекта!")

@router.callback_query(F.data.startswith("reject_"))
async def reject_project_request(call: CallbackQuery, state: FSMContext):
    if not await is_user_admin(call.from_user.id):
        await call.answer("Нет прав!")
        return
    
    request_id = int(call.data.replace("reject_", ""))
    
    # Сохраняем ID заявки в state
    await state.update_data(request_id=request_id)
    
    # Запрашиваем причину отклонения
    await call.message.edit_text(
        f"📝 <b>Укажите причину отклонения заявки #{request_id}:</b>\n\n"
        f"<i>Эта причина будет отправлена пользователю</i>",
        parse_mode="HTML"
    )
    
    # Создаем временное состояние для ввода причины
    class RejectState(StatesGroup):
        waiting_for_reason = State()
    
    await state.set_state(RejectState.waiting_for_reason)
    await call.answer()

@router.message(F.text)
async def handle_reject_reason(message: Message, state: FSMContext):
    """Обработка причины отклонения заявки"""
    from aiogram.fsm.state import State, StatesGroup
    
    class RejectState(StatesGroup):
        waiting_for_reason = State()
    
    current_state = await state.get_state()
    
    if current_state == RejectState.waiting_for_reason:
        data = await state.get_data()
        request_id = data.get('request_id')
        
        if not request_id:
            await state.clear()
            return
        
        # Получаем заявку
        request = supabase.table("project_requests").select("*").eq("id", request_id).single().execute().data
        
        if request and request['status'] == 'pending':
            # Обновляем статус заявки
            supabase.table("project_requests").update({
                "status": "rejected",
                "reject_reason": message.text,
                "admin_id": message.from_user.id,
                "processed_at": "now()"
            }).eq("id", request_id).execute()
            
            # Отправляем лог
            log_text = (
                f"❌ <b>ЗАЯВКА ОТКЛОНЕНА</b>\n\n"
                f"🆔 Заявка: <code>{request_id}</code>\n"
                f"🏷 Проект: <b>{request['name']}</b>\n"
                f"📂 Категория: <code>{request['category']}</code>\n"
                f"📝 Причина: <i>{message.text}</i>\n"
                f"👤 Отклонил: ID{message.from_user.id}"
            )
            
            await send_log_to_topics(log_text, topic_id=TOPIC_REQUESTS)
            
            # Уведомляем пользователя
            try:
                await bot.send_message(
                    request['user_id'],
                    f"❌ <b>Ваша заявка отклонена</b>\n\n"
                    f"Проект: <b>{request['name']}</b>\n"
                    f"Причина: <i>{message.text}</i>\n\n"
                    f"Вы можете подать новую заявку с исправлениями.",
                    parse_mode="HTML"
                )
            except:
                pass
            
            await message.answer(
                f"✅ Заявка #{request_id} отклонена.\n"
                f"Причина отправлена пользователю."
            )
        
        await state.clear()

# --- ОБРАБОТКА КАТЕГОРИЙ ---
@router.message(F.text.in_(CATEGORIES.values()))
async def show_cat(message: Message):
    cat_key = [k for k, v in CATEGORIES.items() if v == message.text][0]
    data = supabase.table("projects").select("*").eq("category", cat_key).order("score", desc=True).execute().data
    if not data: 
        await message.answer(f"📭 В разделе <b>'{message.text}'</b> пока нет проектов.", parse_mode="HTML")
        return
    
    for p in data:
        public_id = await get_or_create_public_id(p['id'])
        await show_project_card(message, p['id'])

# --- ОБНОВЛЕННЫЕ ХЕНДЛЕРЫ (без юзернеймов) ---
@router.callback_query(F.data.startswith("close_"))
async def close_panel_handler(call: CallbackQuery):
    """Закрытие панели - удаление сообщения"""
    await call.message.delete()
    await call.answer()

@router.callback_query(F.data.startswith("panel_"))
async def open_panel(call: CallbackQuery):
    """Открывает панель управления проектом"""
    p_id = call.data.split("_")[1]
    
    project = supabase.table("projects").select("*").eq("id", p_id).single().execute().data
    if not project:
        await call.answer("Проект не найден.", show_alert=True)
        return
    
    public_id = await get_or_create_public_id(p_id)
    
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
            actor = "👤 Админ" if change['is_admin_action'] else "👤 Пользователь"
            text += f"{symbol} <code>{change['change_amount']:+d}</code> — {change['reason'][:50]}... ({actor}, {date})\n"
        text += f"\n"
    
    text += f"<i>Выберите действие:</i>"
    
    if call.message.photo:
        await safe_edit_media(call, text, reply_markup=project_panel_kb(p_id, public_id))
    else:
        await safe_edit_message(call, text, reply_markup=project_panel_kb(p_id, public_id))
    
    await call.answer()

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
    
    if call.message.photo:
        await safe_edit_media(call, txt, reply_markup=back_to_panel_kb(p_id))
    else:
        await safe_edit_message(call, txt, reply_markup=back_to_panel_kb(p_id))
    
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
    rate = int(call.data.split("_")[1])
    data = await state.get_data()
    p_id = data['p_id']
    
    old_rev = supabase.table("user_logs").select("*")\
        .eq("user_id", call.from_user.id)\
        .eq("project_id", p_id)\
        .eq("action_type", "review")\
        .execute()
    
    p = supabase.table("projects").select("*").eq("id", p_id).single().execute().data
    
    old_score = p['score']
    rating_change = RATING_MAP[rate]
    
    if old_rev.data:
        old_rating_change = RATING_MAP[old_rev.data[0]['rating_val']]
        rating_change = RATING_MAP[rate] - old_rating_change
        new_score = old_score + rating_change
        
        supabase.table("user_logs").update({
            "review_text": data['txt'], 
            "rating_val": rate
        }).eq("id", old_rev.data[0]['id']).execute()
        
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
    
    # Добавляем запись в историю (без юзернейма)
    supabase.table("rating_history").insert({
        "project_id": p_id,
        "user_id": call.from_user.id,
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
    
    # Лог (без юзернейма)
    admin_text = (f"📢 <b>Отзыв {res_txt}:</b> {p['name']}\n"
                  f"Пользователь: ID{call.from_user.id}\n"
                  f"Текст: <i>{data['txt']}</i>\n"
                  f"Оценка: {rate}/5\n"
                  f"📊 Изменение: {rating_change:+d}\n"
                  f"🔢 Новый рейтинг: {new_score}\n"
                  f"Удалить: <code>/delrev {log_id}</code>")
    
    await send_log_to_topics(admin_text, p['category'])
    
    await state.clear()
    await call.answer()

@router.callback_query(F.data.startswith("viewrev_"))
async def view_reviews(call: CallbackQuery):
    p_id = call.data.split("_")[1]
    
    revs = supabase.table("user_logs").select("*")\
        .eq("project_id", p_id)\
        .eq("action_type", "review")\
        .order("created_at", desc=True)\
        .limit(5)\
        .execute().data
    
    project = supabase.table("projects").select("name").eq("id", p_id).single().execute().data
    project_name = project['name'] if project else "Проект"
    
    if not revs: 
        text = f"<b>💬 ОТЗЫВЫ ПРОЕКТА</b>\n<b>{project_name}</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
        text += "📭 Отзывов еще нет\n"
        
        if call.message.photo:
            await safe_edit_media(call, text, reply_markup=back_to_panel_kb(p_id))
        else:
            await safe_edit_message(call, text, reply_markup=back_to_panel_kb(p_id))
        
        await call.answer()
        return
    
    text = f"<b>💬 ПОСЛЕДНИЕ ОТЗЫВЫ</b>\n<b>{project_name}</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    for r in revs: 
        date = r['created_at'][:10] if r['created_at'] else ""
        stars = '⭐' * r['rating_val']
        text += f"{stars}\n<i>{r['review_text']}</i>\n📅 {date}\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    
    if call.message.photo:
        await safe_edit_media(call, text, reply_markup=back_to_panel_kb(p_id))
    else:
        await safe_edit_message(call, text, reply_markup=back_to_panel_kb(p_id))
    
    await call.answer()

@router.callback_query(F.data.startswith("history_"))
async def view_history(call: CallbackQuery):
    """Показать историю изменений рейтинга проекта"""
    p_id = call.data.split("_")[1]
    
    project = supabase.table("projects").select("*").eq("id", p_id).single().execute().data
    if not project:
        await call.answer("Проект не найден.", show_alert=True)
        return
    
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
            
            # Анонимизация
            if change['is_admin_action']:
                actor = "👤 Админ"
            else:
                actor = "👤 Пользователь"
            
            symbol = "📈" if change['change_amount'] > 0 else "📉" if change['change_amount'] < 0 else "➡️"
            
            text += f"{i}. {symbol} <b>{change['score_before']} → {change['score_after']}</b> ({change['change_amount']:+d})\n"
            text += f"   📝 {change['reason'][:50]}{'...' if len(change['reason']) > 50 else ''}\n"
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
    
    check = supabase.table("user_logs").select("id")\
        .eq("user_id", call.from_user.id)\
        .eq("project_id", p_id)\
        .eq("action_type", "like")\
        .execute()
    
    if check.data: 
        await call.answer("Вы уже поддержали этот проект!", show_alert=True)
        return
    
    project = supabase.table("projects").select("*").eq("id", p_id).single().execute().data
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
    
    supabase.table("rating_history").insert({
        "project_id": p_id,
        "user_id": call.from_user.id,
        "change_type": "like",
        "score_before": old_score,
        "score_after": new_score,
        "change_amount": 1,
        "reason": "Лайк от пользователя",
        "is_admin_action": False
    }).execute()
    
    await open_panel(call)
    await call.answer("❤️ Голос учтен!")

# --- ОБНОВЛЕННЫЕ АДМИН КОМАНДЫ (без юзернеймов) ---
@router.message(Command("add"))
async def admin_add(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id): 
        return
        
    await state.clear()
    
    try:
        if len(message.text.split()) < 2:
            await message.reply(
                "❌ Неверный формат. Используйте:\n"
                "<code>/add категория | Название | Описание</code>",
                parse_mode="HTML"
            )
            return
        
        raw = message.text.split(maxsplit=1)[1]
        parts = raw.split("|")
        
        if len(parts) < 3:
            await message.reply("❌ Нужно три параметра через '|'")
            return
        
        cat, name, desc = [p.strip() for p in parts[:3]]
        
        if cat not in CATEGORIES:
            categories_list = "\n".join([f"- <code>{k}</code> ({v})" for k, v in CATEGORIES.items()])
            await message.reply(f"❌ Неверная категория:\n{categories_list}", parse_mode="HTML")
            return
        
        existing = supabase.table("projects").select("*").eq("name", name).execute()
        if existing.data:
            await message.reply(f"⚠️ Проект <b>{name}</b> уже существует!", parse_mode="HTML")
            return
        
        result = supabase.table("projects").insert({
            "name": name, 
            "category": cat, 
            "description": desc,
            "score": 0
        }).execute()
        
        if result.data:
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
            
            log_text = (f"📋 <b>Добавлен новый проект:</b>\n\n"
                       f"🏷 Название: <b>{name}</b>\n"
                       f"📂 Категория: <code>{cat}</code>\n"
                       f"📝 Описание: {desc}\n"
                       f"👤 Админ: ID{message.from_user.id}")
            
            await send_log_to_topics(log_text, cat)
            
            await message.reply(
                f"✅ Проект <b>{name}</b> успешно добавлен!\n"
                f"🆔 ID проекта: <code>{result.data[0]['id']}</code>",
                parse_mode="HTML"
            )
            
    except Exception as e:
        logging.error(f"Ошибка в /add: {e}")
        await message.reply("❌ Ошибка при добавлении проекта.")

# --- ОСТАЛЬНЫЕ АДМИН КОМАНДЫ (без юзернеймов) ---

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
        
        # Удаление проекта и связанных данных
        supabase.table("projects").delete().eq("id", project_id).execute()
        supabase.table("user_logs").delete().eq("project_id", project_id).execute()
        supabase.table("rating_history").delete().eq("project_id", project_id).execute()
        supabase.table("project_photos").delete().eq("project_id", project_id).execute()
        supabase.table("project_requests").delete().eq("name", project['name']).execute()
        
        # Отправляем лог
        log_text = (f"🗑 <b>Проект удален:</b>\n\n"
                   f"🏷 Название: <b>{project['name']}</b>\n"
                   f"📂 Категория: <code>{category}</code>\n"
                   f"📊 Удалено отзывов: {reviews_num}\n"
                   f"🔢 Финальный рейтинг: {score}\n"
                   f"👤 Админ: ID{message.from_user.id}")
        
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
            f"📝 <b>Укажите причину изменения рейтинга для проекта <i>{project['name']}</i>:</b>\n\n"
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
        log_text = (f"⚖️ <b>Изменен рейтинг проекта:</b>\n\n"
                   f"🏷 Название: <b>{project_name}</b>\n"
                   f"📂 Категория: <code>{category}</code>\n"
                   f"🔢 Было: <b>{old_score}</b>\n"
                   f"🔢 Стало: <b>{new_score}</b>\n"
                   f"📊 Изменение: <code>{change_amount:+d}</code>\n"
                   f"📝 Причина: <i>{reason}</i>\n"
                   f"👤 Админ: ID{message.from_user.id}")
        
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
                   f"👤 Удалил: ID{message.from_user.id}")
        
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

@router.message(Command("editdesc"))
async def admin_edit_desc(message: Message, state: FSMContext):
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
            await message.reply(
                f"❌ Проект <b>{name}</b> не найден!",
                parse_mode="HTML"
            )
            return
        
        old_desc = project['description']
        
        # Обновляем описание
        supabase.table("projects").update({"description": new_desc}).eq("id", project['id']).execute()
        
        # Добавляем запись в историю
        supabase.table("rating_history").insert({
            "project_id": project['id'],
            "admin_id": message.from_user.id,
            "change_type": "edit_description",
            "score_before": project['score'],
            "score_after": project['score'],
            "change_amount": 0,
            "reason": f"Изменение описания проекта",
            "is_admin_action": True
        }).execute()
        
        # Отправляем лог
        log_text = (f"📝 <b>Изменено описание проекта:</b>\n\n"
                   f"🏷 Проект: <b>{project['name']}</b> (ID: {project['id']})\n"
                   f"📂 Категория: <code>{project['category']}</code>\n"
                   f"📝 <b>Было:</b> <i>{old_desc[:200]}...</i>\n"
                   f"📝 <b>Стало:</b> <i>{new_desc[:200]}...</i>\n"
                   f"👤 Админ: ID{message.from_user.id}")
        
        await send_log_to_topics(log_text, project['category'])
        
        await message.reply(
            f"✅ Описание проекта <b>{project['name']}</b> успешно обновлено!",
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
            await message.reply(
                f"❌ Проект <b>{name}</b> не найден!",
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
        
        await message.reply(
            f"📸 <b>Отправьте фотографию для проекта:</b>\n\n"
            f"🏷 Проект: <b>{project['name']}</b>\n"
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
        # Добавляем запись в историю
        supabase.table("rating_history").insert({
            "project_id": project_id,
            "admin_id": message.from_user.id,
            "change_type": "add_photo",
            "score_before": 0,
            "score_after": 0,
            "change_amount": 0,
            "reason": f"Добавлено фото к проекту",
            "is_admin_action": True
        }).execute()
        
        # Отправляем лог
        log_text = (f"🖼️ <b>Добавлено фото проекта:</b>\n\n"
                   f"🏷 Проект: <b>{project_name}</b> (ID: {project_id})\n"
                   f"📂 Категория: <code>{category}</code>\n"
                   f"👤 Админ: ID{message.from_user.id}")
        
        await send_log_to_topics(log_text, category)
        
        # Показываем превью фото
        await message.reply_photo(
            photo=photo_file_id,
            caption=f"✅ Фото для проекта <b>{project_name}</b> успешно сохранено!",
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
        "Используйте /cancel для отмены."
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
            await message.reply(
                f"❌ Проект <b>{name}</b> не найден!",
                parse_mode="HTML"
            )
            return
        
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
        
        # Считаем количество уникальных пользователей
        unique_users = len(set([r['user_id'] for r in reviews] + [l['user_id'] for l in likes]))
        
        text = f"<b>📊 СТАТИСТИКА ПРОЕКТА</b>\n\n"
        text += f"🏷 <b>{project['name']}</b>\n"
        text += f"🆔 ID: <code>{project['id']}</code>\n"
        text += f"📂 Категория: <code>{project['category']}</code>\n"
        text += f"🔗 Публичный ID: <code>{project.get('public_id', 'не создан')}</code>\n"
        text += f"🔢 Текущий рейтинг: <b>{project['score']}</b>\n"
        text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        text += f"📈 <b>Общая статистика:</b>\n"
        text += f"• 👥 Уникальных пользователей: {unique_users}\n"
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
        projects = supabase.table("projects").select("*").order("score", desc=True).execute().data
        
        if not projects:
            await message.reply("📭 Список проектов пуст.")
            return
        
        text = "<b>📋 СПИСОК ВСЕХ ПРОЕКТОВ</b>\n\n"
        
        for i, p in enumerate(projects, 1):
            # Получаем количество отзывов и лайков
            reviews_count = supabase.table("user_logs")\
                .select("*")\
                .eq("project_id", p['id'])\
                .eq("action_type", "review")\
                .execute()
            
            likes_count = supabase.table("user_logs")\
                .select("*")\
                .eq("project_id", p['id'])\
                .eq("action_type", "like")\
                .execute()
            
            reviews_num = len(reviews_count.data) if reviews_count.data else 0
            likes_num = len(likes_count.data) if likes_count.data else 0
            
            text += f"<b>{i}. {p['name']}</b>\n"
            text += f"   🆔 ID: <code>{p['id']}</code>\n"
            text += f"   🔗 Public ID: <code>{p.get('public_id', 'нет')}</code>\n"
            text += f"   📂 Категория: <code>{p['category']}</code>\n"
            text += f"   🔢 Рейтинг: <b>{p['score']}</b>\n"
            text += f"   💬 Отзывов: {reviews_num}\n"
            text += f"   ❤️ Лайков: {likes_num}\n"
            text += f"   ⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        
        text += f"\n📊 Всего проектов: <b>{len(projects)}</b>"
        
        # Разбиваем на части если слишком длинное
        if len(text) > 4000:
            parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
            for part in parts:
                await message.answer(part, parse_mode="HTML")
        else:
            await message.reply(text, parse_mode="HTML")
        
    except Exception as e:
        logging.error(f"Ошибка в /list: {e}")
        await message.reply(
            "❌ Ошибка при получении списка проектов."
        )

@router.message(Command("pending"))
async def admin_pending_requests(message: Message):
    """Список заявок на модерацию"""
    if not await is_user_admin(message.from_user.id): 
        return
        
    try:
        requests = supabase.table("project_requests")\
            .select("*")\
            .eq("status", "pending")\
            .order("created_at", desc=True)\
            .execute().data
        
        if not requests:
            await message.reply("✅ Нет заявок на модерацию.")
            return
        
        text = f"<b>📋 ЗАЯВКИ НА МОДЕРАЦИЮ: {len(requests)}</b>\n\n"
        
        for i, req in enumerate(requests, 1):
            date = req['created_at'][:10] if req['created_at'] else ""
            
            text += f"<b>{i}. {req['name']}</b>\n"
            text += f"   🆔 ID заявки: <code>{req['id']}</code>\n"
            text += f"   📂 Категория: <code>{req['category']}</code>\n"
            text += f"   👤 Пользователь: ID{req['user_id']}\n"
            text += f"   📅 Дата: {date}\n"
            text += f"   📝 Описание: {req['description'][:50]}...\n"
            
            # Кнопки для модерации
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{req['id']}"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{req['id']}")
                ]
            ])
            
            await message.answer(text, reply_markup=kb, parse_mode="HTML")
            text = ""  # Сбрасываем для следующего сообщения
        
    except Exception as e:
        logging.error(f"Ошибка в /pending: {e}")
        await message.reply(
            "❌ Ошибка при получении списка заявок."
        )

@router.message(Command("ban"))
async def admin_ban_user(message: Message):
    """Забанить пользователя"""
    if not await is_user_admin(message.from_user.id): 
        return
        
    try:
        if len(message.text.split()) < 2:
            await message.reply(
                "❌ Неверный формат. Используйте:\n"
                "<code>/ban user_id | причина</code>\n\n"
                "Пример: <code>/ban 123456789 | Нарушение правил</code>",
                parse_mode="HTML"
            )
            return
        
        raw = message.text.split(maxsplit=1)[1]
        parts = raw.split("|")
        
        if len(parts) < 2:
            await message.reply(
                "❌ Неверный формат. Нужно два параметра через '|'"
            )
            return
        
        user_id_str, reason = [p.strip() for p in parts[:2]]
        
        try:
            user_id = int(user_id_str)
        except ValueError:
            await message.reply(
                f"❌ <b>{user_id_str}</b> не является числовым ID!",
                parse_mode="HTML"
            )
            return
        
        # Проверяем, не забанен ли уже пользователь
        existing = supabase.table("banned_users").select("*").eq("user_id", user_id).execute()
        if existing.data:
            await message.reply(
                f"⚠️ Пользователь <code>{user_id}</code> уже забанен!",
                parse_mode="HTML"
            )
            return
        
        # Добавляем в бан
        supabase.table("banned_users").insert({
            "user_id": user_id,
            "admin_id": message.from_user.id,
            "reason": reason,
            "banned_at": "now()"
        }).execute()
        
        # Отправляем лог
        log_text = (f"🚫 <b>Пользователь забанен:</b>\n\n"
                   f"👤 ID пользователя: <code>{user_id}</code>\n"
                   f"📝 Причина: <i>{reason}</i>\n"
                   f"👤 Админ: ID{message.from_user.id}")
        
        await send_log_to_topics(log_text)
        
        await message.reply(
            f"✅ Пользователь <code>{user_id}</code> забанен!\n"
            f"📝 Причина: <i>{reason}</i>",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logging.error(f"Ошибка в /ban: {e}")
        await message.reply(
            "❌ Ошибка при бане пользователя."
        )

@router.message(Command("unban"))
async def admin_unban_user(message: Message):
    """Разбанить пользователя"""
    if not await is_user_admin(message.from_user.id): 
        return
        
    try:
        if len(message.text.split()) < 2:
            await message.reply(
                "❌ Укажите ID пользователя для разбана.\n"
                "<code>/unban user_id</code>\n\n"
                "Пример: <code>/unban 123456789</code>",
                parse_mode="HTML"
            )
            return
        
        user_id_str = message.text.split()[1].strip()
        
        try:
            user_id = int(user_id_str)
        except ValueError:
            await message.reply(
                f"❌ <b>{user_id_str}</b> не является числовым ID!",
                parse_mode="HTML"
            )
            return
        
        # Проверяем, забанен ли пользователь
        existing = supabase.table("banned_users").select("*").eq("user_id", user_id).execute()
        if not existing.data:
            await message.reply(
                f"ℹ️ Пользователь <code>{user_id}</code> не забанен!",
                parse_mode="HTML"
            )
            return
        
        # Удаляем из бана
        supabase.table("banned_users").delete().eq("user_id", user_id).execute()
        
        # Отправляем лог
        log_text = (f"✅ <b>Пользователь разбанен:</b>\n\n"
                   f"👤 ID пользователя: <code>{user_id}</code>\n"
                   f"👤 Админ: ID{message.from_user.id}")
        
        await send_log_to_topics(log_text)
        
        await message.reply(
            f"✅ Пользователь <code>{user_id}</code> разбанен!",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logging.error(f"Ошибка в /unban: {e}")
        await message.reply(
            "❌ Ошибка при разбане пользователя."
        )

@router.message(Command("search"))
async def admin_search(message: Message):
    """Поиск проектов"""
    if not await is_user_admin(message.from_user.id): 
        return
        
    try:
        if len(message.text.split()) < 2:
            await message.reply(
                "❌ Укажите запрос для поиска.\n"
                "<code>/search запрос</code>\n\n"
                "Пример: <code>/search бот</code>",
                parse_mode="HTML"
            )
            return
        
        query = message.text.split(maxsplit=1)[1].strip()
        
        # Ищем проекты по названию и описанию
        projects = supabase.table("projects")\
            .select("*")\
            .or_(f"name.ilike.%{query}%,description.ilike.%{query}%")\
            .order("score", desc=True)\
            .execute().data
        
        if not projects:
            await message.reply(
                f"🔍 По запросу <b>{query}</b> ничего не найдено.",
                parse_mode="HTML"
            )
            return
        
        text = f"<b>🔍 РЕЗУЛЬТАТЫ ПОИСКА: \"{query}\"</b>\n\n"
        
        for i, p in enumerate(projects, 1):
            text += f"<b>{i}. {p['name']}</b>\n"
            text += f"   🆔 ID: <code>{p['id']}</code>\n"
            text += f"   📂 Категория: <code>{p['category']}</code>\n"
            text += f"   🔢 Рейтинг: <b>{p['score']}</b>\n"
            text += f"   📝 Описание: {p['description'][:50]}...\n"
            text += f"   ⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        
        text += f"\n📊 Найдено проектов: <b>{len(projects)}</b>"
        
        if len(text) > 4000:
            parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
            for part in parts:
                await message.answer(part, parse_mode="HTML")
        else:
            await message.reply(text, parse_mode="HTML")
        
    except Exception as e:
        logging.error(f"Ошибка в /search: {e}")
        await message.reply(
            "❌ Ошибка при поиске."
        )

@router.message(Command("cleanup"))
async def admin_cleanup(message: Message):
    """Очистка старых данных"""
    if not await is_user_admin(message.from_user.id): 
        return
        
    try:
        # Удаляем заявки старше 30 дней
        result = supabase.table("project_requests")\
            .delete()\
            .lt("created_at", "now() - interval '30 days'")\
            .execute()
        
        deleted_count = len(result.data) if result.data else 0
        
        await message.reply(
            f"🧹 <b>Очистка завершена!</b>\n\n"
            f"Удалено заявок старше 30 дней: <b>{deleted_count}</b>",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logging.error(f"Ошибка в /cleanup: {e}")
        await message.reply(
            "❌ Ошибка при очистке данных."
        )

@router.message(Command("export"))
async def admin_export(message: Message):
    """Экспорт данных"""
    if not await is_user_admin(message.from_user.id): 
        return
        
    try:
        # Получаем все проекты
        projects = supabase.table("projects").select("*").order("score", desc=True).execute().data
        
        if not projects:
            await message.reply("📭 Нет данных для экспорта.")
            return
        
        # Формируем CSV
        csv_data = "ID;Название;Категория;Рейтинг;Описание;Public ID\n"
        
        for p in projects:
            csv_data += f"{p['id']};{p['name']};{p['category']};{p['score']};{p['description']};{p.get('public_id', '')}\n"
        
        # Сохраняем временный файл
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(csv_data)
            temp_file = f.name
        
        # Отправляем файл
        try:
            await message.reply_document(
                document=FSInputFile(temp_file, filename="projects_export.csv"),
                caption=f"📊 Экспорт проектов: {len(projects)} записей"
            )
        finally:
            # Удаляем временный файл
            import os
            os.unlink(temp_file)
        
    except Exception as e:
        logging.error(f"Ошибка в /export: {e}")
        await message.reply(
            "❌ Ошибка при экспорте данных."
        )

# --- ДОПОЛНИТЕЛЬНЫЕ ФУНКЦИИ ---
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

async def main():
    logging.basicConfig(level=logging.INFO)
    dp.update.outer_middleware(AccessMiddleware())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
