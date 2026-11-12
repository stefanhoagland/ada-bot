"""Telegram front end: chat with Ada and approve her actions with buttons."""

from __future__ import annotations

import asyncio
import html
import itertools
import logging

import anthropic
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .brain import BaseBrain, BrainError, Conversation, make_brain
from .config import Config
from .tools import ToolRequest

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4000


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split long text into Telegram-sized chunks, preferring line breaks."""
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks or [""]


class AdaBot:
    def __init__(self, config: Config, brain: BaseBrain | None = None):
        self.config = config
        self.brain = brain or make_brain(config)
        self.convos: dict[int, Conversation] = {}
        self.locks: dict[int, asyncio.Lock] = {}
        self.pending: dict[str, asyncio.Future] = {}
        self._ids = itertools.count(1)

    # ---- helpers -------------------------------------------------------

    def _allowed(self, update: Update) -> bool:
        user = update.effective_user
        return user is not None and user.id in self.config.allowed_user_ids

    def _convo(self, chat_id: int) -> Conversation:
        return self.convos.setdefault(chat_id, Conversation())

    def _lock(self, chat_id: int) -> asyncio.Lock:
        return self.locks.setdefault(chat_id, asyncio.Lock())

    async def _send(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
        for chunk in split_message(text):
            await context.bot.send_message(chat_id=chat_id, text=chunk)

    # ---- handlers ------------------------------------------------------

    async def on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            user = update.effective_user
            await update.effective_message.reply_text(
                f"Sorry, I only work for my owner. (Your Telegram ID is {user.id if user else '?'}.)"
            )
            return
        await update.effective_message.reply_text(
            "Hi, I'm Ada. Ask me about your server, have me write code, or just ask a question.\n\n"
            "I'll ask before changing anything.\n"
            "/new starts a fresh conversation."
        )

    async def on_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return
        self._convo(update.effective_chat.id).reset()
        await update.effective_message.reply_text("Fresh start. What's next?")

    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            log.warning("Ignored message from user %s", update.effective_user and update.effective_user.id)
            return
        msg = update.effective_message
        if msg is None or not msg.text:
            return
        chat_id = update.effective_chat.id

        async with self._lock(chat_id):
            convo = self._convo(chat_id)
            if len(convo.messages) > self.config.max_history_messages:
                convo.reset()
                await self._send(context, chat_id, "(Our conversation got long, so I started a fresh one.)")

            typing = asyncio.create_task(self._keep_typing(context, chat_id))
            try:
                reply = await self.brain.respond(
                    convo,
                    msg.text,
                    approve=lambda req: self._ask_approval(context, chat_id, req),
                    notify=lambda note: self._send(context, chat_id, note),
                )
            except BrainError as e:
                reply = str(e)
            except anthropic.AuthenticationError:
                reply = "My Claude API key was rejected. Check ANTHROPIC_API_KEY in the container settings."
            except anthropic.RateLimitError:
                reply = "I'm being rate limited by the Claude API. Try again in a minute."
            except anthropic.APIStatusError as e:
                log.exception("Claude API error")
                reply = f"The Claude API returned an error ({e.status_code}). Try again shortly."
            except anthropic.APIConnectionError:
                reply = "I couldn't reach the Claude API. Is the server online?"
            except Exception:
                log.exception("Unexpected error while answering")
                reply = "Something went wrong on my side. Check my logs in Unraid for details."
            finally:
                typing.cancel()
            await self._send(context, chat_id, reply)

    async def _keep_typing(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
        try:
            while True:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.debug("typing indicator failed", exc_info=True)

    async def _ask_approval(
        self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, req: ToolRequest
    ) -> bool:
        key = str(next(self._ids))
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        buttons = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("✅ Approve", callback_data=f"ok:{key}"),
                InlineKeyboardButton("❌ Deny", callback_data=f"no:{key}"),
            ]]
        )
        desc = req.describe()
        if len(desc) > 3500:
            desc = desc[:3500] + "\n…"
        sent = await context.bot.send_message(
            chat_id=chat_id,
            text=f"<b>Ada wants to:</b>\n<pre>{html.escape(desc)}</pre>",
            parse_mode=ParseMode.HTML,
            reply_markup=buttons,
        )
        try:
            return await asyncio.wait_for(future, timeout=self.config.approval_timeout_s)
        except asyncio.TimeoutError:
            try:
                await sent.edit_reply_markup(reply_markup=None)
                await context.bot.send_message(chat_id=chat_id, text="No answer, so I skipped that action.")
            except Exception:
                pass
            return False
        finally:
            self.pending.pop(key, None)

    async def on_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        if not self._allowed(update):
            await query.answer("Not allowed.")
            return
        action, _, key = (query.data or "").partition(":")
        future = self.pending.get(key)
        if future is None or future.done():
            await query.answer("This request has expired.")
            await query.edit_message_reply_markup(reply_markup=None)
            return
        approved = action == "ok"
        future.set_result(approved)
        await query.answer("Approved" if approved else "Denied")
        label = "✅ Approved" if approved else "❌ Denied"
        try:
            await query.edit_message_text(
                text=f"{query.message.text_html}\n\n<b>{label}</b>", parse_mode=ParseMode.HTML
            )
        except Exception:
            await query.edit_message_reply_markup(reply_markup=None)

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        err = context.error
        if isinstance(err, (NetworkError, TimedOut)):
            log.warning("Telegram network hiccup (will retry): %s", err)
        else:
            log.error("Unhandled error", exc_info=err)

    # ---- wiring --------------------------------------------------------

    def build(self) -> Application:
        app = (
            ApplicationBuilder()
            .token(self.config.telegram_token)
            # Be patient with slow or flaky home connections.
            .connect_timeout(20)
            .read_timeout(30)
            .write_timeout(30)
            .pool_timeout(10)
            .get_updates_connect_timeout(20)
            .get_updates_read_timeout(60)
            .build()
        )
        app.add_error_handler(self.on_error)
        app.add_handler(CommandHandler("start", self.on_start))
        app.add_handler(CommandHandler("help", self.on_start))
        app.add_handler(CommandHandler("new", self.on_new))
        # block=False so button presses are handled while Ada is mid-task waiting for approval.
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_message, block=False)
        )
        app.add_handler(CallbackQueryHandler(self.on_button))
        return app
