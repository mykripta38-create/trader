"""Обработчики команд Telegram."""
from dataclasses import dataclass

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

import config

router = Router()


@dataclass
class BotState:
    last_candle: str = "—"
    last_signal_text: str = ""
    signals_sent: int = 0


state = BotState()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    pairs = ", ".join(config.SYMBOLS)
    await message.answer(
        f"Бот SMC-сигналов запущен (24/7).\n"
        f"Пары: {pairs} ({len(config.SYMBOLS)} шт), таймфрейм: {config.TIMEFRAME}.\n"
        f"Команды: /status — состояние, /last — последний сигнал."
    )


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    pairs = ", ".join(config.SYMBOLS)
    await message.answer(
        f"Пары: {pairs} ({len(config.SYMBOLS)} шт, {config.TIMEFRAME})\n"
        f"Последняя проверенная свеча (UTC): {state.last_candle}\n"
        f"Отправлено сигналов: {state.signals_sent}"
    )


@router.message(Command("last"))
async def cmd_last(message: Message) -> None:
    await message.answer(state.last_signal_text or "Сигналов пока не было.")
