import asyncio
import os
import re
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message

TOKEN = os.environ.get("BOT_TOKEN", "8759573544:AAEYf9K00M9wWrGTpehOJ_t5IXDzb_zM6GI")

IBAN_LABELED = re.compile(r"IBAN\s*[:\-]?\s*([A-Z]{2}\d{2}[A-Z0-9 ]{11,34})", re.I)
IBAN_BARE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
NAME_RE = re.compile(
    r"(?:account\s*name|beneficiary(?:\s*name)?|account\s*holder)\s*[:\-]\s*(.+)",
    re.I,
)
AMOUNT_LINE = re.compile(r"\s*(\d[\d\s.,]*)\s*(?:[A-Za-z]{3}|дирх\w*)?\s*")
PLAIN_NAME = re.compile(r"[A-Za-z][A-Za-z.'\-]*(?:\s+[A-Za-z][A-Za-z.'\-]*)+")
LOOKS_LIKE_REQS = re.compile(r"\b(iban|swift|account)\b", re.I)

FIELDS = ("iban", "name", "amount")
LABELS = {"iban": "IBAN", "name": "имя", "amount": "сумму"}

dp = Dispatcher()

# (chat_id, id сообщения бота с просьбой) -> найденные поля + id исходного сообщения
pending: dict[tuple[int, int], dict] = {}


def parse(text: str):
    m = IBAN_LABELED.search(text)
    iban = re.sub(r"\s", "", m.group(1)).upper() if m else None
    if not iban:
        m = IBAN_BARE.search(text.upper())
        iban = m.group(0) if m else None

    m = NAME_RE.search(text)
    name = m.group(1).strip() if m else None
    if not name:
        # Имя без подписи: строка из 2+ латинских слов (последняя подходящая)
        cands = [
            l.strip()
            for l in text.splitlines()
            if PLAIN_NAME.fullmatch(l.strip()) and "bank" not in l.lower()
        ]
        name = cands[-1] if cands else None

    amount = None
    for line in text.splitlines():
        m = AMOUNT_LINE.fullmatch(line)
        if m:
            s = re.sub(r"\s", "", m.group(1))
            if re.search(r",\d{1,2}$", s):
                s = s.replace(",", ".")
            else:
                s = s.replace(",", "")
            amount = s
            break

    return iban, name, amount


def fill_from_reply(text: str, data: dict):
    iban, name, amount = parse(text)
    # В ответе имя можно написать просто строкой без "Account name:"
    if not name:
        for line in text.splitlines():
            line = line.strip()
            if (
                line
                and re.search(r"[^\W\d_]", line)
                and not AMOUNT_LINE.fullmatch(line)
                and not IBAN_BARE.search(line.upper())
            ):
                name = line
                break
    for key, val in (("iban", iban), ("name", name), ("amount", amount)):
        if not data[key] and val:
            data[key] = val


def missing_text(data: dict) -> str:
    miss = ", ".join(LABELS[k] for k in FIELDS if not data[k])
    return f"Не нашёл: {miss}. Ответьте на это сообщение и добавьте недостающее."


async def finish(bot: Bot, chat_id: int, data: dict, extra_ids: list[int]):
    await bot.send_message(
        chat_id,
        f"<code>{escape(data['iban'])}</code>\n"
        f"<code>{escape(data['name'])}</code>\n"
        f"<code>{escape(data['amount'])}</code>",
    )
    for mid in [data["orig_id"], *extra_ids]:
        try:
            await bot.delete_message(chat_id, mid)
        except Exception as e:
            print("Cannot delete message (need admin + delete rights):", e)


@dp.message(F.text)
async def handle(message: Message, bot: Bot):
    chat_id = message.chat.id
    text = message.text

    # 1) Ответ на просьбу бота — дополняем недостающие поля
    reply = message.reply_to_message
    if reply and (chat_id, reply.message_id) in pending:
        key = (chat_id, reply.message_id)
        data = pending[key]
        fill_from_reply(text, data)
        if all(data[k] for k in FIELDS):
            del pending[key]
            await finish(bot, chat_id, data, [reply.message_id, message.message_id])
        else:
            try:
                await bot.edit_message_text(
                    missing_text(data), chat_id=chat_id, message_id=reply.message_id
                )
            except Exception:
                pass  # текст не изменился
            try:
                await message.delete()
            except Exception:
                pass
        return

    # 2) Новое сообщение с реквизитами
    iban, name, amount = parse(text)
    data = {"iban": iban, "name": name, "amount": amount, "orig_id": message.message_id}

    if all(data[k] for k in FIELDS):
        await finish(bot, chat_id, data, [])
        return

    # Обычные сообщения чата без признаков реквизитов игнорируем
    if not (iban or LOOKS_LIKE_REQS.search(text)):
        return

    prompt = await message.reply(missing_text(data))
    pending[(chat_id, prompt.message_id)] = data


async def main():
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
