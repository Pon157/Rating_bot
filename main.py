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
TOPIC_LOGS_ALL = 46 # Общий топик для ВСЕХ логов/отзывов

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

def project_inline_kb(p_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Оценить/Изменить", callback_data=f"rev_{p_id}"),
         InlineKeyboardButton(text="❤️ Поддержать", callback_data=f"like_{p_id}")],
        [InlineKeyboardButton(text="💬 Посмотреть отзывы", callback_data=f"viewrev_{p_id}"),
         InlineKeyboardButton(text="📊 История изменений", callback_data=f"history_{p_id}")]
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
                "Неверный формат команды.\n"
                "Используйте: /add категория | Название | Описание\n\n"
                "Пример: /add support_bots | Бот Помощи | Отвечает на вопросы",
                parse_mode="HTML"
            )
            return
        
        raw = message.text.split(maxsplit=1)[1]
        parts = raw.split("|")
        
        if len(parts) < 3:
            await message.reply(
                "Неверный формат. Необходимо три параметра разделенных символом |\n\n"
                "1. Категория проекта\n"
                "2. Название проекта\n"
                "3. Описание проекта",
                parse_mode="HTML"
            )
            return
        
        cat, name, desc = [p.strip() for p in parts[:3]]
        
        if cat not in CATEGORIES:
            categories_list = "\n".join([f"- {k} ({v})" for k, v in CATEGORIES.items()])
            await message.reply(
                f"Неверная категория проекта.\n\nДоступные категории:\n{categories_list}",
                parse_mode="HTML"
            )
            return
        
        existing = supabase.table("projects").select("*").eq("name", name).execute()
        if existing.data:
            await message.reply(
                f"Проект с названием '{name}' уже существует в системе.",
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
                "reason": "Создание нового проекта",
                "is_admin_action": True
            }).execute()
            
            # Отправляем лог
            log_text = (
                f"Добавлен новый проект:\n\n"
                f"Название: {name}\n"
                f"Категория: {cat}\n"
                f"Описание: {desc}\n"
                f"Администратор: @{message.from_user.username or message.from_user.id}"
            )
            
            await send_log_to_topics(log_text, cat)
            
            await message.reply(
                f"Проект '{name}' успешно добавлен в систему.",
                parse_mode="HTML"
            )
        else:
            await message.reply(
                "Ошибка при добавлении проекта в базу данных.",
            )
            
    except Exception as e:
        logging.error(f"Ошибка в команде /add: {e}")
        await message.reply(
            "Произошла ошибка при обработке команды.",
        )

@router.message(Command("del"))
async def admin_delete(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id): 
        return
        
    await state.clear()
    
    try:
        if len(message.text.split()) < 2:
            await message.reply(
                "Укажите название проекта для удаления.\n"
                "Формат: /del Название_проекта"
            )
            return
        
        name = message.text.split(maxsplit=1)[1].strip()
        
        existing = supabase.table("projects").select("*").eq("name", name).execute()
        if not existing.data:
            await message.reply(
                f"Проект '{name}' не найден в системе.",
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
            "reason": "Удаление проекта из системы",
            "is_admin_action": True
        }).execute()
        
        # Удаление проекта и связанных данных
        supabase.table("projects").delete().eq("id", project_id).execute()
        supabase.table("user_logs").delete().eq("project_id", project_id).execute()
        supabase.table("rating_history").delete().eq("project_id", project_id).execute()
        
        # Отправляем лог
        log_text = (
            f"Проект удален из системы:\n\n"
            f"Название: {name}\n"
            f"Категория: {category}\n"
            f"Количество удаленных отзывов: {reviews_num}\n"
            f"Финальный рейтинг: {score}\n"
            f"Администратор: @{message.from_user.username or message.from_user.id}"
        )
        
        await send_log_to_topics(log_text, category)
        
        await message.reply(
            f"Проект '{name}' удален из системы.\n"
            f"Удалено отзывов: {reviews_num}\n"
            f"Финальный рейтинг проекта: {score}",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logging.error(f"Ошибка в команде /del: {e}")
        await message.reply(
            "Ошибка при удалении проекта.",
        )

@router.message(Command("score"))
async def admin_score(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id): 
        return
        
    try:
        if len(message.text.split()) < 2:
            await message.reply(
                "Неверный формат команды.\n"
                "Используйте: /score Название_проекта | изменение_рейтинга | причина\n\n"
                "Пример: /score Бот Помощи | 10 | Добавление новых функций",
                parse_mode="HTML"
            )
            return
        
        raw = message.text.split(maxsplit=1)[1]
        parts = raw.split("|")
        
        if len(parts) < 3:
            await message.reply(
                "Неверный формат. Необходимо три параметра.\n\n"
                "1. Название проекта\n"
                "2. Изменение рейтинга (число)\n"
                "3. Причина изменения",
            )
            return
        
        name = parts[0].strip()
        val_str = parts[1].strip()
        reason = parts[2].strip()
        
        if not reason or len(reason) < 3:
            await message.reply(
                "Причина изменения должна содержать минимум 3 символа.",
            )
            return
        
        try:
            val = int(val_str)
        except ValueError:
            await message.reply(
                f"'{val_str}' не является числом.",
                parse_mode="HTML"
            )
            return
        
        existing = supabase.table("projects").select("*").eq("name", name).execute()
        if not existing.data:
            await message.reply(
                f"Проект '{name}' не найден в системе.",
                parse_mode="HTML"
            )
            return
        
        project = existing.data[0]
        project_id = project['id']
        category = project['category']
        old_score = project['score']
        new_score = old_score + val
        
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
            "change_amount": val,
            "reason": reason,
            "is_admin_action": True
        }).execute()
        
        # Отправляем лог
        change_direction = "увеличен" if val > 0 else "уменьшен" if val < 0 else "не изменился"
        log_text = (
            f"Изменен рейтинг проекта:\n\n"
            f"Название: {name}\n"
            f"Категория: {category}\n"
            f"Предыдущий рейтинг: {old_score}\n"
            f"Новый рейтинг: {new_score}\n"
            f"Изменение: {val:+d}\n"
            f"Статус: рейтинг {change_direction}\n"
            f"Причина: {reason}\n"
            f"Администратор: @{message.from_user.username or message.from_user.id}"
        )
        
        await send_log_to_topics(log_text, category)
        
        await message.reply(
            f"Рейтинг проекта изменен.\n\n"
            f"Проект: {name}\n"
            f"Рейтинг: {old_score} → {new_score} ({val:+d})\n"
            f"Причина: {reason}",
            parse_mode="HTML"
        )
            
    except Exception as e:
        logging.error(f"Ошибка в команде /score: {e}")
        await message.reply(
            "Произошла ошибка при обработке команды.\n\n"
            "Правильный формат: /score Название | число | причина",
        )

@router.message(Command("delrev"))
async def admin_delrev(message: Message, state: FSMContext):
    if not await is_user_admin(message.from_user.id): 
        return
        
    await state.clear()
    
    try:
        if len(message.text.split()) < 2:
            await message.reply(
                "Укажите ID отзыва для удаления.\n"
                "Формат: /delrev ID_отзыва"
            )
            return
        
        log_id_str = message.text.split()[1]
        
        try:
            log_id = int(log_id_str)
        except ValueError:
            await message.reply(
                f"'{log_id_str}' не является числовым идентификатором.",
                parse_mode="HTML"
            )
            return
        
        rev_result = supabase.table("user_logs").select("*").eq("id", log_id).execute()
        if not rev_result.data:
            await message.reply(
                f"Отзыв с ID #{log_id} не найден.",
                parse_mode="HTML"
            )
            return
        
        rev = rev_result.data[0]
        
        project_result = supabase.table("projects").select("*").eq("id", rev['project_id']).execute()
        if not project_result.data:
            await message.reply(
                f"Проект отзыва #{log_id} не найден."
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
            "reason": f"Удаление отзыва #{log_id} (оценка пользователя: {rev['rating_val']}/5)",
            "is_admin_action": True,
            "related_review_id": log_id
        }).execute()
        
        # Обновляем рейтинг проекта
        supabase.table("projects").update({"score": new_score}).eq("id", rev['project_id']).execute()
        
        # Удаляем отзыв
        supabase.table("user_logs").delete().eq("id", log_id).execute()
        
        # Отправляем лог
        log_text = (
            f"Удален отзыв пользователя:\n\n"
            f"Проект: {project['name']}\n"
            f"Категория: {project['category']}\n"
            f"ID отзыва: {log_id}\n"
            f"Оценка пользователя: {rev['rating_val']}/5\n"
            f"Изменение рейтинга проекта: {rating_change:+d}\n"
            f"Новый рейтинг проекта: {new_score}\n"
            f"Текст отзыва: {rev['review_text'][:150]}...\n"
            f"Администратор: @{message.from_user.username or message.from_user.id}"
        )
        
        await send_log_to_topics(log_text, project['category'])
        
        await message.reply(
            f"Отзыв #{log_id} удален.\n"
            f"Проект: {project['name']}\n"
            f"Рейтинг: {old_score} → {new_score} ({rating_change:+d})",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logging.error(f"Ошибка в команде /delrev: {e}")
        await message.reply(
            "Ошибка при удалении отзыва.",
        )

# --- ЛОГИКА ПОЛЬЗОВАТЕЛЯ ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    top = supabase.table("projects").select("*").order("score", desc=True).limit(5).execute().data
    text = "ТОП-5 ПРОЕКТОВ\n"
    if top:
        for i, p in enumerate(top, 1):
            text += f"{i}. {p['name']} — {p['score']}\n"
    else: 
        text += "Список проектов пуст.\n"
    
    text += "\nВыберите категорию ниже для просмотра проектов"
    
    await message.answer(text, reply_markup=main_kb(), parse_mode="HTML")

@router.message(F.text.in_(CATEGORIES.values()))
async def show_cat(message: Message):
    cat_key = [k for k, v in CATEGORIES.items() if v == message.text][0]
    data = supabase.table("projects").select("*").eq("category", cat_key).order("score", desc=True).execute().data
    if not data: 
        await message.answer(f"В категории '{message.text}' нет проектов.")
        return
    
    for p in data:
        # Получаем последние изменения рейтинга
        recent_changes = supabase.table("rating_history").select("*")\
            .eq("project_id", p['id'])\
            .order("created_at", desc=True)\
            .limit(3)\
            .execute().data
        
        card = f"{p['name']}\n\n{p['description']}\n\n"
        card += f"Текущий рейтинг: {p['score']}\n"
        
        if recent_changes:
            card += f"\nПоследние изменения рейтинга:\n"
            for change in recent_changes[:2]:
                date = change['created_at'][:10] if change['created_at'] else ""
                if change['is_admin_action']:
                    card += f"+{change['change_amount']:+d} — {change['reason']} ({date})\n"
                else:
                    card += f"{change['change_amount']:+d} — {change['reason']} ({date})\n"
        
        await message.answer(card, reply_markup=project_inline_kb(p['id']), parse_mode="HTML")

@router.callback_query(F.data.startswith("rev_"))
async def rev_start(call: CallbackQuery, state: FSMContext):
    p_id = call.data.split("_")[1]
    check = supabase.table("user_logs").select("*").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "review").execute()
    await state.update_data(p_id=p_id)
    await state.set_state(ReviewState.waiting_for_text)
    txt = "Введите новый текст отзыва:" if check.data else "Введите текст отзыва:"
    await call.message.answer(txt, parse_mode="HTML"); await call.answer()

@router.message(ReviewState.waiting_for_text)
async def rev_text(message: Message, state: FSMContext):
    if message.text and message.text.startswith("/"): return 
    await state.update_data(txt=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⭐"*i, callback_data=f"st_{i}")] for i in range(5, 0, -1)])
    await state.set_state(ReviewState.waiting_for_rate)
    await message.answer("Выберите оценку:", reply_markup=kb, parse_mode="HTML")

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
        reason = f"Изменение оценки: {old_rev.data[0]['rating_val']}/5 → {rate}/5"
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
        reason = f"Новая оценка: {rate}/5"

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
    
    await call.message.edit_text(f"Отзыв {res_txt}.", parse_mode="HTML")
    
    # ФОРМИРУЕМ ЛОГ
    admin_text = (
        f"Отзыв {res_txt}:\n"
        f"Проект: {p['name']}\n"
        f"Пользователь: @{call.from_user.username or call.from_user.id}\n"
        f"Текст: {data['txt']}\n"
        f"Оценка: {rate}/5\n"
        f"Изменение рейтинга: {rating_change:+d}\n"
        f"Новый рейтинг: {new_score}\n"
        f"Удалить отзыв: /delrev {log_id}"
    )
    
    await send_log_to_topics(admin_text, p['category'])

    await state.clear(); await call.answer()

@router.callback_query(F.data.startswith("viewrev_"))
async def view_reviews(call: CallbackQuery):
    p_id = call.data.split("_")[1]
    revs = supabase.table("user_logs").select("*").eq("project_id", p_id).eq("action_type", "review").order("created_at", desc=True).limit(5).execute().data
    if not revs: 
        await call.answer("Нет отзывов для этого проекта.", show_alert=True)
        return
    
    text = "ПОСЛЕДНИЕ ОТЗЫВЫ:\n\n"
    for r in revs: 
        date = r['created_at'][:10] if r['created_at'] else ""
        text += f"{'⭐' * r['rating_val']}\n{r['review_text']}\nДата: {date}\n\n"
    
    await call.message.answer(text, parse_mode="HTML"); await call.answer()

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
    
    if not history:
        await call.answer("Нет истории изменений.", show_alert=True)
        return
    
    text = f"ИСТОРИЯ ИЗМЕНЕНИЙ\n{project['name']}\n\n"
    
    for i, change in enumerate(history, 1):
        date_time = change['created_at'][:16] if change['created_at'] else ""
        
        if change['is_admin_action']:
            actor = f"Администратор: {change['admin_username'] or change['admin_id']}"
        else:
            actor = f"Пользователь: {change['username'] or change['user_id']}"
        
        text += f"{i}. {change['score_before']} → {change['score_after']} ({change['change_amount']:+d})\n"
        text += f"   Причина: {change['reason']}\n"
        text += f"   {actor}\n"
        text += f"   Дата: {date_time}\n\n"
    
    await call.message.answer(text, parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("like_"))
async def handle_like(call: CallbackQuery):
    p_id = call.data.split("_")[1]
    check = supabase.table("user_logs").select("id").eq("user_id", call.from_user.id).eq("project_id", p_id).eq("action_type", "like").execute()
    if check.data: 
        await call.answer("Вы уже поддерживали этот проект.", show_alert=True)
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
        "reason": "Поддержка пользователя",
        "is_admin_action": False
    }).execute()
    
    await call.answer("Ваш голос учтен.")

async def main():
    logging.basicConfig(level=logging.INFO)
    dp.update.outer_middleware(AccessMiddleware())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
