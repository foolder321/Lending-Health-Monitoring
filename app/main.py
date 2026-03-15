"""
Application entry point.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.adapters.aave_v3_arbitrum import AaveV3ArbitrumAdapter
from app.core.config import AppSettings
from app.core.logging import init_logging
from app.services.alert_service import AlertService
from app.services.monitor_service import MonitorService
from app.services.risk_engine import RiskEngine
from app.services.telegram_service import TelegramService, build_hide_keyboard
from app.storage.db import create_engine_and_session
from app.storage.models import Base
from app.storage.repository import AlertRepository

logger = logging.getLogger(__name__)

LATEST_POSITIONS: dict[str, object] = {}
USER_STATES: dict[str, str] = {}


async def _create_tables(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _normalize_address(address: str) -> str:
    return address.strip().lower()


def _is_valid_evm_address(address: str) -> bool:
    address = address.strip()
    return address.startswith("0x") and len(address) == 42


def _format_asset_lines(assets) -> str:
    if not assets:
        return "• -"

    lines = []
    for asset in assets:
        symbol = getattr(asset, "token_symbol", None) or getattr(asset, "token_address", "")[:6]
        amount = getattr(asset, "amount", 0.0) or 0.0
        usd_value = getattr(asset, "usd_value", 0.0) or 0.0
        lines.append(f"• {symbol} — {amount:,.6f} (${usd_value:,.2f})")
    return "\n".join(lines)


def _position_is_empty(position) -> bool:
    if position is None:
        return True

    collateral = getattr(position, "collateral_value_usd", None) or 0.0
    debt = getattr(position, "debt_value_usd", None) or 0.0
    supplied = getattr(position, "supplied", None) or []
    borrowed = getattr(position, "borrowed", None) or []

    return collateral <= 0 and debt <= 0 and len(supplied) == 0 and len(borrowed) == 0


async def build_status_message_from_position(address: str, position) -> str:
    try:
        if _position_is_empty(position):
            return (
                f"📭 <b>Позиция не найдена</b>\n\n"
                f"По кошельку <code>{address}</code>\n"
                f"нет активной позиции в <b>Aave V3 Arbitrum</b>."
            )

        hf = getattr(position, "health_factor", None)
        collateral = getattr(position, "collateral_value_usd", None)
        debt = getattr(position, "debt_value_usd", None)
        ltv = getattr(position, "ltv", None)
        network = getattr(position, "network", "Unknown")
        protocol = getattr(position, "protocol", "Unknown")
        supplied = getattr(position, "supplied", [])
        borrowed = getattr(position, "borrowed", [])

        nft_id = "-"
        liquidation_distance_pct = getattr(position, "liquidation_distance_pct", None)
        estimated_liquidation_price = getattr(position, "estimated_liquidation_price", None)
        position_risk_status = getattr(position, "risk_status", None)

        if position_risk_status is None:
            if hf is None:
                risk_status = "UNKNOWN"
            elif hf > 1.5:
                risk_status = "SAFE"
            elif hf > 1.3:
                risk_status = "OK"
            elif hf > 1.2:
                risk_status = "WARNING"
            elif hf >= 1.0:
                risk_status = "DANGER"
            else:
                risk_status = "LIQUIDATION"
        else:
            risk_status = position_risk_status

        hf_text = f"{hf:.4f}" if hf is not None else "n/a"
        collateral_text = f"${collateral:,.2f}" if collateral is not None else "n/a"
        debt_text = f"${debt:,.2f}" if debt is not None else "n/a"
        ltv_text = f"{ltv:.2f}%" if ltv is not None else "n/a"

        net_worth = None
        if collateral is not None and debt is not None:
            net_worth = collateral - debt
        net_worth_text = f"${net_worth:,.2f}" if net_worth is not None else "n/a"

        liquidation_price_text = (
            f"${estimated_liquidation_price:,.2f}"
            if estimated_liquidation_price is not None
            else "-"
        )
        liquidation_distance_text = (
            f"{liquidation_distance_pct:.2f}%"
            if liquidation_distance_pct is not None
            else "n/a"
        )

        supplied_text = _format_asset_lines(supplied)
        borrowed_text = _format_asset_lines(borrowed)

        return (
            f"📊 <b>{protocol.upper()} POSITION</b>\n"
            f"⛓ <b>Chain:</b> {network}\n"
            f"🏦 <b>Address:</b> <code>{address}</code>\n"
            f"🧩 <b>NFT id:</b> {nft_id}\n\n"
            f"❤️ <b>Health Factor:</b> {hf_text}\n"
            f"🛡 <b>Collateral Total:</b> {collateral_text}\n"
            f"💸 <b>Borrowed Total:</b> {debt_text}\n"
            f"📉 <b>LTV:</b> {ltv_text}\n"
            f"⚠️ <b>Liquidation Distance:</b> {liquidation_distance_text}\n"
            f"☠️ <b>Liquidation Price:</b> {liquidation_price_text}\n"
            f"💰 <b>Net Worth:</b> {net_worth_text}\n\n"
            f"🛡 <b>Collateral Assets:</b>\n{supplied_text}\n\n"
            f"💸 <b>Borrowed Assets:</b>\n{borrowed_text}\n\n"
            f"📍 <b>Status:</b> {risk_status}"
        )
    except Exception as exc:
        logger.exception("Failed to build status message: %s", exc)
        return f"Не удалось получить текущую позицию для <code>{address}</code>."


async def get_position_with_loading_message(address: str, adapter, telegram_service) -> object | None:
    address = _normalize_address(address)
    position = LATEST_POSITIONS.get(address)
    if position is not None:
        return position

    await telegram_service.send_message(
        "⏳ <b>[░░░░░]</b> Загружаем позицию...\n"
        "Первый запрос после перезапуска может занять до минуты...\n"
        "Дальше всё будет почти мгновенно."
    )
    await asyncio.sleep(0.3)

    try:
        position = await adapter.get_position(address)
    except Exception as exc:
        logger.exception("Failed to fetch position for %s: %s", address, exc)
        return None

    if position is not None:
        LATEST_POSITIONS[address] = position

    return position


async def _get_user_wallets(session_factory, repository: AlertRepository, chat_id: str) -> list[dict]:
    async with session_factory() as session:
        await repository.ensure_schema(session)
        await repository.ensure_user(session, chat_id)
        return await repository.get_wallets_by_chat_id(session, chat_id)


async def _resolve_user_address(
    text: str,
    command_name: str,
    chat_id: str,
    session_factory,
    repository: AlertRepository,
    telegram_service: TelegramService,
) -> str | None:
    parts = text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip():
        address = _normalize_address(parts[1].strip())
        if not _is_valid_evm_address(address):
            await telegram_service.send_message(
                f"Некорректный адрес.\n\nПример: {command_name} 0x..."
            )
            return None
        return address

    wallets = await _get_user_wallets(session_factory, repository, chat_id)
    if not wallets:
        await telegram_service.send_message(
            "У тебя пока нет добавленных кошельков.\n"
            "Добавь первый через кнопку «➕ Добавить кошелек»\n"
            "или командой:\n/add_wallet 0x..."
        )
        return None

    if len(wallets) == 1:
        return wallets[0]["address"]

    buttons = []
    for wallet in wallets:
        short = wallet["address"][:6] + "..." + wallet["address"][-4:]
        buttons.append([short])

    if command_name == "/status":
        USER_STATES[chat_id] = "awaiting_status_wallet_button"
    elif command_name == "/risk":
        USER_STATES[chat_id] = "awaiting_risk_wallet_button"

    await telegram_service.send_message(
        "У тебя несколько кошельков. Выбери:",
        reply_markup={
            "keyboard": buttons,
            "resize_keyboard": True,
            "one_time_keyboard": True,
        },
        use_main_keyboard=False,
    )
    return None
async def _build_risk_message_from_position(address: str, position) -> str:
    if _position_is_empty(position):
        return (
            f"📭 <b>Прогноз недоступен</b>\n\n"
            f"По кошельку <code>{address}</code>\n"
            f"нет активной позиции в <b>Aave V3 Arbitrum</b>."
        )

    hf = getattr(position, "health_factor", None)
    total_collateral = getattr(position, "collateral_value_usd", None)
    liquidation_price = getattr(position, "estimated_liquidation_price", None)
    liquidation_distance = getattr(position, "liquidation_distance_pct", None)
    supplied = getattr(position, "supplied", []) or []

    if hf is None or total_collateral is None or total_collateral <= 0:
        return "Недостаточно данных для расчёта стресс-теста залога."

    collateral_assets = []
    for asset in supplied:
        symbol = (getattr(asset, "token_symbol", "") or "").upper()
        amount = getattr(asset, "amount", 0.0) or 0.0
        usd_value = getattr(asset, "usd_value", 0.0) or 0.0

        if amount <= 0 or usd_value <= 0:
            continue

        current_price = usd_value / amount if amount else None
        if current_price is None or current_price <= 0:
            continue

        collateral_assets.append(
            {
                "symbol": symbol or "UNKNOWN",
                "usd_value": usd_value,
                "current_price": current_price,
                "weight": usd_value / total_collateral if total_collateral else 0.0,
            }
        )

    if not collateral_assets:
        return "Не удалось определить активы в залоге для стресс-теста."

    collateral_assets.sort(key=lambda x: x["usd_value"], reverse=True)

    scenarios = [-0.05, -0.10, -0.15, -0.20, -0.25]
    blocks = []

    for asset in collateral_assets:
        symbol = asset["symbol"]
        current_price = asset["current_price"]
        usd_value = asset["usd_value"]
        weight_pct = asset["weight"] * 100.0

        lines = []
        for drop in scenarios:
            new_price = current_price * (1 + drop)
            new_total_collateral = total_collateral + (usd_value * drop)
            collateral_ratio = (
                new_total_collateral / total_collateral
                if total_collateral
                else 0.0
            )
            new_hf = hf * collateral_ratio if hf is not None else None
            hf_text = f"{new_hf:.2f}" if new_hf is not None else "n/a"

            lines.append(
                f"• {int(abs(drop) * 100)}% → ${new_price:,.0f} → HF {hf_text}"
            )

        block = (
            f"📉 <b>{symbol} RISK SCENARIOS</b>\n\n"
            f"Current {symbol}: ${current_price:,.0f}\n"
            f"Collateral share: {weight_pct:.1f}% (${usd_value:,.2f})\n\n"
            f"If {symbol} drops:\n\n"
            + "\n".join(lines)
        )
        blocks.append(block)

    liquidation_price_text = (
        f"${liquidation_price:,.0f}"
        if liquidation_price is not None
        else "-"
    )
    liquidation_distance_text = (
        f"{liquidation_distance:.1f}%"
        if liquidation_distance is not None
        else "n/a"
    )

    return (
        f"🧪 <b>STRESS TEST ЗАЛОГА</b>\n\n"
        + "\n\n".join(blocks)
        + "\n\n"
        + f"☠ Liquidation price: {liquidation_price_text}\n"
        + f"⚠ Distance to liquidation: {liquidation_distance_text}"
    )


async def telegram_command_loop(
    settings: AppSettings,
    telegram_service: TelegramService,
    adapter,
    session_factory,
    repository: AlertRepository,
) -> None:
    logger.info("Starting Telegram command loop")

    while True:
        try:
            updates = await telegram_service.get_updates()

            for update in updates:
                message = update.get("message", {})
                chat = message.get("chat", {})
                chat_id = str(chat.get("id"))
                text = (message.get("text", "") or "").strip()

                if str(chat.get("id")) != str(settings.telegram_chat_id):
                    continue

                state = USER_STATES.get(chat_id)

                if text == "Показать текущую позицию":
                    text = "/status"
                elif text == "Прогноз риска залога":
                    text = "/risk"
                elif text == "➕ Добавить кошелек":
                    USER_STATES[chat_id] = "awaiting_add_wallet"
                    await telegram_service.send_message(
                        "Введи адрес кошелька одним сообщением.\n\n"
                        "Пример:\n0x...",
                        reply_markup=build_hide_keyboard(),
                    )
                    continue
                elif text == "➖ Удалить кошелек":
                    wallets = await _get_user_wallets(session_factory, repository, chat_id)
                    if not wallets:
                        await telegram_service.send_message("У тебя нет добавленных кошельков.")
                        continue

                    USER_STATES[chat_id] = "awaiting_remove_wallet"
                    lines = ["Отправь адрес кошелька, который нужно удалить:"]
                    for wallet in wallets:
                        lines.append(f"• <code>{wallet['address']}</code>")

                    await telegram_service.send_message(
                        "\n".join(lines),
                        reply_markup=build_hide_keyboard(),
                    )
                    continue
                elif text == "👛 Мои кошельки":
                    text = "/my_wallets"

                if state == "awaiting_add_wallet" and not text.startswith("/"):
                    address = _normalize_address(text)

                    if not _is_valid_evm_address(address):
                        await telegram_service.send_message(
                            "Некорректный адрес.\n\n"
                            "Отправь адрес в формате:\n0x..."
                        )
                        continue

                    try:
                        await telegram_service.send_message(
                            "⏳ Добавляю кошелек...",
                            use_main_keyboard=False,
                        )

                        async with session_factory() as session:
                            await repository.ensure_schema(session)
                            added = await repository.add_wallet(session, chat_id, address)

                        USER_STATES.pop(chat_id, None)

                        if added:
                            await telegram_service.send_message(
                                f"✅ Кошелёк добавлен:\n<code>{address}</code>",
                                use_main_keyboard=True,
                            )
                        else:
                            await telegram_service.send_message(
                                f"ℹ️ Этот кошелёк уже есть:\n<code>{address}</code>",
                                use_main_keyboard=True,
                            )

                    except Exception as exc:
                        logger.exception("Failed to add wallet for chat %s: %s", chat_id, exc)
                        USER_STATES.pop(chat_id, None)
                        await telegram_service.send_message(
                            "Не удалось добавить кошелёк. Попробуй ещё раз.",
                            use_main_keyboard=True,
                        )

                    continue

                if state == "awaiting_remove_wallet" and not text.startswith("/"):
                    address = _normalize_address(text)

                    if not _is_valid_evm_address(address):
                        await telegram_service.send_message(
                            "Некорректный адрес.\n\n"
                            "Отправь адрес в формате:\n0x..."
                        )
                        continue

                    try:
                        await telegram_service.send_message(
                            "⏳ Удаляю кошелек...",
                            use_main_keyboard=False,
                        )

                        async with session_factory() as session:
                            await repository.ensure_schema(session)
                            removed = await repository.remove_wallet(session, chat_id, address)

                        USER_STATES.pop(chat_id, None)
                        LATEST_POSITIONS.pop(address, None)

                        if removed:
                            await telegram_service.send_message(
                                f"🗑 Кошелёк удалён:\n<code>{address}</code>",
                                use_main_keyboard=True,
                            )
                        else:
                            await telegram_service.send_message(
                                f"Кошелёк не найден:\n<code>{address}</code>",
                                use_main_keyboard=True,
                            )

                    except Exception as exc:
                        logger.exception("Failed to remove wallet for chat %s: %s", chat_id, exc)
                        USER_STATES.pop(chat_id, None)
                        await telegram_service.send_message(
                            "Не удалось удалить кошелёк. Попробуй ещё раз.",
                            use_main_keyboard=True,
                        )

                    continue

                if state == "awaiting_status_wallet" and not text.startswith("/"):
                    address = _normalize_address(text)

                    if not _is_valid_evm_address(address):
                        await telegram_service.send_message(
                            "Некорректный адрес.\n\n"
                            "Отправь адрес в формате:\n0x..."
                        )
                        continue

                if state == "awaiting_status_wallet_button":
                    wallets = await _get_user_wallets(session_factory, repository, chat_id)

                    selected_address = None
                    for wallet in wallets:
                        short = wallet["address"][:6] + "..." + wallet["address"][-4:]
                        if text == short:
                            selected_address = wallet["address"]
                            break

                    if selected_address:
                        USER_STATES.pop(chat_id, None)

                        position = await get_position_with_loading_message(
                            selected_address,
                            adapter,
                            telegram_service,
                        )
                        if position is None:
                            await telegram_service.send_message(
                                "Не удалось получить позицию. Попробуй ещё раз через несколько секунд."
                            )
                            continue

                        status_message = await build_status_message_from_position(
                            selected_address,
                            position,
                        )
                        await telegram_service.send_message(status_message)
                        continue


                    USER_STATES.pop(chat_id, None)

                    position = await get_position_with_loading_message(
                        address,
                        adapter,
                        telegram_service,
                    )
                    if position is None:
                        await telegram_service.send_message(
                            "Не удалось получить позицию. Попробуй ещё раз через несколько секунд."
                        )
                        continue

                    status_message = await build_status_message_from_position(address, position)
                    await telegram_service.send_message(status_message)
                    continue

                if state == "awaiting_risk_wallet" and not text.startswith("/"):
                    address = _normalize_address(text)

                    if not _is_valid_evm_address(address):
                        await telegram_service.send_message(
                            "Некорректный адрес.\n\n"
                            "Отправь адрес в формате:\n0x..."
                        )
                        continue

                if state == "awaiting_risk_wallet_button":
                    wallets = await _get_user_wallets(session_factory, repository, chat_id)

                    selected_address = None
                    for wallet in wallets:
                        short = wallet["address"][:6] + "..." + wallet["address"][-4:]
                        if text == short:
                            selected_address = wallet["address"]
                            break

                    if selected_address:
                        USER_STATES.pop(chat_id, None)

                        position = await get_position_with_loading_message(
                            selected_address,
                            adapter,
                            telegram_service,
                        )
                        if position is None:
                            await telegram_service.send_message(
                                "Не удалось получить позицию. Попробуй ещё раз через несколько секунд."
                            )
                            continue

                        risk_message = await _build_risk_message_from_position(
                            selected_address,
                            position,
                        )
                        await telegram_service.send_message(risk_message)
                        continue

                    USER_STATES.pop(chat_id, None)

                    position = await get_position_with_loading_message(
                        address,
                        adapter,
                        telegram_service,
                    )
                    if position is None:
                        await telegram_service.send_message(
                            "Не удалось получить позицию. Попробуй ещё раз через несколько секунд."
                        )
                        continue

                    risk_message = await _build_risk_message_from_position(address, position)
                    await telegram_service.send_message(risk_message)
                    continue

                if text == "/start":
                    USER_STATES.pop(chat_id, None)
                    await telegram_service.send_message(
                        "Бот запущен.\n\n"
                        "Доступные команды:\n"
                        "/add_wallet 0x... — добавить кошелёк\n"
                        "/remove_wallet 0x... — удалить кошелёк\n"
                        "/my_wallets — показать мои кошельки\n"
                        "/status [0x...] — показать текущую позицию\n"
                        "/risk [0x...] — прогноз риска залога\n"
                        "/clear_cache — очистить кэш позиций"
                    )

                elif text.startswith("/add_wallet"):
                    parts = text.split(maxsplit=1)
                    if len(parts) < 2 or not parts[1].strip():
                        USER_STATES[chat_id] = "awaiting_add_wallet"
                        await telegram_service.send_message(
                            "Введи адрес кошелька одним сообщением.\n\n"
                            "Пример:\n0x...",
                            reply_markup=build_hide_keyboard(),
                        )
                        continue

                    address = _normalize_address(parts[1].strip())
                    if not _is_valid_evm_address(address):
                        await telegram_service.send_message(
                            "Некорректный адрес. Использование: /add_wallet 0x..."
                        )
                        continue

                    async with session_factory() as session:
                        await repository.ensure_schema(session)
                        added = await repository.add_wallet(session, chat_id, address)

                    USER_STATES.pop(chat_id, None)

                    if added:
                        await telegram_service.send_message(
                            f"✅ Кошелёк добавлен:\n<code>{address}</code>"
                        )
                    else:
                        await telegram_service.send_message(
                            f"ℹ️ Этот кошелёк уже есть:\n<code>{address}</code>"
                        )

                elif text.startswith("/remove_wallet"):
                    parts = text.split(maxsplit=1)
                    if len(parts) < 2 or not parts[1].strip():
                        wallets = await _get_user_wallets(session_factory, repository, chat_id)
                        if not wallets:
                            await telegram_service.send_message("У тебя нет добавленных кошельков.")
                            continue

                        USER_STATES[chat_id] = "awaiting_remove_wallet"
                        lines = ["Отправь адрес кошелька, который нужно удалить:"]
                        for wallet in wallets:
                            lines.append(f"• <code>{wallet['address']}</code>")

                        await telegram_service.send_message(
                            "\n".join(lines),
                            reply_markup=build_hide_keyboard(),
                        )
                        continue

                    address = _normalize_address(parts[1].strip())
                    if not _is_valid_evm_address(address):
                        await telegram_service.send_message(
                            "Некорректный адрес. Использование: /remove_wallet 0x..."
                        )
                        continue

                    async with session_factory() as session:
                        await repository.ensure_schema(session)
                        removed = await repository.remove_wallet(session, chat_id, address)

                    USER_STATES.pop(chat_id, None)
                    LATEST_POSITIONS.pop(address, None)

                    if removed:
                        await telegram_service.send_message(
                            f"🗑 Кошелёк удалён:\n<code>{address}</code>"
                        )
                    else:
                        await telegram_service.send_message(
                            f"Кошелёк не найден:\n<code>{address}</code>"
                        )

                elif text == "/my_wallets":
                    wallets = await _get_user_wallets(session_factory, repository, chat_id)
                    if not wallets:
                        await telegram_service.send_message(
                            "У тебя пока нет добавленных кошельков.\n"
                            "Добавь первый через кнопку «➕ Добавить кошелек»\n"
                            "или командой:\n/add_wallet 0x..."
                        )
                        continue

                    lines = ["👛 <b>Мои кошельки</b>\n"]
                    for idx, wallet in enumerate(wallets, start=1):
                        lines.append(f"{idx}. <code>{wallet['address']}</code>")
                    await telegram_service.send_message("\n".join(lines))

                elif text == "/clear_cache":
                    LATEST_POSITIONS.clear()
                    await telegram_service.send_message(
                        "🧹 Кэш позиций очищен.\n"
                        "Следующий запрос загрузит данные заново."
                    )

                elif text.startswith("/status"):
                    address = await _resolve_user_address(
                        text=text,
                        command_name="/status",
                        chat_id=chat_id,
                        session_factory=session_factory,
                        repository=repository,
                        telegram_service=telegram_service,
                    )
                    if not address:
                        continue

                    position = await get_position_with_loading_message(
                        address,
                        adapter,
                        telegram_service,
                    )
                    if position is None:
                        await telegram_service.send_message(
                            "Не удалось получить позицию. Попробуй ещё раз через несколько секунд."
                        )
                        continue

                    status_message = await build_status_message_from_position(address, position)
                    await telegram_service.send_message(status_message)

                elif text.startswith("/risk"):
                    address = await _resolve_user_address(
                        text=text,
                        command_name="/risk",
                        chat_id=chat_id,
                        session_factory=session_factory,
                        repository=repository,
                        telegram_service=telegram_service,
                    )
                    if not address:
                        continue

                    position = await get_position_with_loading_message(
                        address,
                        adapter,
                        telegram_service,
                    )
                    if position is None:
                        await telegram_service.send_message(
                            "Не удалось получить позицию. Попробуй ещё раз через несколько секунд."
                        )
                        continue

                    risk_message = await _build_risk_message_from_position(address, position)
                    await telegram_service.send_message(risk_message)

            await asyncio.sleep(0.2)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Telegram command loop failed: %s", exc)
            await asyncio.sleep(2)


async def run() -> None:
    settings = AppSettings()
    init_logging(settings.log_level)

    logger.info("Starting monitoring application")

    engine, session_factory = create_engine_and_session(settings)
    await _create_tables(engine)

    adapter = AaveV3ArbitrumAdapter(settings)
    risk_engine = RiskEngine(
        warning_threshold=1.20,
        critical_threshold=1.10,
        liquidation_threshold=1.00,
    )
    repository = AlertRepository()
    alert_service = AlertService(repository, repeat_minutes=settings.alert_repeat_minutes)
    telegram_service = TelegramService(settings)

    monitor_service = MonitorService(
        addresses=settings.addresses,
        adapter=adapter,
        risk_engine=risk_engine,
        alert_service=alert_service,
        telegram_service=telegram_service,
        session_factory=session_factory,
        position_cache=LATEST_POSITIONS,
    )

    scheduler = AsyncIOScheduler()
    poll_seconds = getattr(settings, "poll_interval_seconds", 30)

    scheduler.add_job(
        monitor_service.poll_addresses,
        trigger=IntervalTrigger(seconds=poll_seconds),
        id="monitor_addresses",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()

    stop_event = asyncio.Event()

    def _handle_stop_signal(*_args) -> None:
        logger.info("Received shutdown signal")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_stop_signal)
        except NotImplementedError:
            pass

    telegram_task = asyncio.create_task(
        telegram_command_loop(
            settings,
            telegram_service,
            adapter,
            session_factory,
            repository,
        ),
        name="telegram_command_loop",
    )

    try:
        await stop_event.wait()
    finally:
        logger.info("Shutting down...")
        telegram_task.cancel()
        await asyncio.gather(telegram_task, return_exceptions=True)

        try:
            scheduler.shutdown(wait=False)
            logger.info("Scheduler has been shut down")
        except Exception as exc:
            logger.exception("Failed to shutdown scheduler: %s", exc)

        try:
            await adapter.close()
        except Exception as exc:
            logger.exception("Failed to close adapter: %s", exc)

        try:
            await engine.dispose()
        except Exception as exc:
            logger.exception("Failed to dispose engine: %s", exc)

        logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(run())
