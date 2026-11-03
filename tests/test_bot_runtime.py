"""Test runtime del bot: singola istanza (bot.pid), uscita pulita con Ctrl-C,
interfaccia (/portfolio in solo lettura) e filtro del Conflict di Telegram."""

import asyncio
import logging
import os
from types import SimpleNamespace

import pytest
from telegram.error import Conflict

from logitrade import telegram_bot as tb


class TestInstanceLock:
    def test_first_start_writes_pid(self, tmp_path):
        path = tmp_path / "bot.pid"
        tb.acquire_instance_lock(path)
        assert path.read_text(encoding="utf-8") == str(os.getpid())
        tb.release_instance_lock(path)
        assert not path.exists()

    def test_live_other_instance_blocks_start(self, tmp_path, monkeypatch):
        path = tmp_path / "bot.pid"
        path.write_text("4242", encoding="utf-8")
        monkeypatch.setattr(tb, "_pid_running", lambda pid: pid == 4242)
        with pytest.raises(SystemExit, match="già in esecuzione"):
            tb.acquire_instance_lock(path)
        assert path.read_text(encoding="utf-8") == "4242"  # lock altrui intatto

    def test_message_tells_how_to_stop_it(self, tmp_path, monkeypatch):
        path = tmp_path / "bot.pid"
        path.write_text("4242", encoding="utf-8")
        monkeypatch.setattr(tb, "_pid_running", lambda pid: True)
        with pytest.raises(SystemExit) as exc:
            tb.acquire_instance_lock(path)
        assert "kill 4242" in str(exc.value)
        assert "Conflict" in str(exc.value)

    def test_stale_lock_is_replaced(self, tmp_path, monkeypatch):
        path = tmp_path / "bot.pid"
        path.write_text("4242", encoding="utf-8")
        monkeypatch.setattr(tb, "_pid_running", lambda pid: False)
        tb.acquire_instance_lock(path)
        assert path.read_text(encoding="utf-8") == str(os.getpid())

    def test_garbage_lock_is_replaced(self, tmp_path):
        path = tmp_path / "bot.pid"
        path.write_text("not-a-pid", encoding="utf-8")
        tb.acquire_instance_lock(path)  # non deve sollevare
        assert path.read_text(encoding="utf-8") == str(os.getpid())

    def test_release_does_not_touch_foreign_lock(self, tmp_path):
        path = tmp_path / "bot.pid"
        path.write_text("4242", encoding="utf-8")
        tb.release_instance_lock(path)
        assert path.read_text(encoding="utf-8") == "4242"

    @pytest.mark.parametrize("pid", [-1, 0, 4_194_305, 10**30])
    def test_pid_running_rejects_invalid_pids(self, pid):
        assert tb._pid_running(pid) is False

    def test_pid_running_false_for_missing_pid(self):
        assert tb._pid_running(4_194_300) is False  # nessun processo a questo pid


class TestRunUntilInterrupted:
    def test_keyboard_interrupt_becomes_clean_log(self, monkeypatch, caplog):
        def boom(_):
            raise KeyboardInterrupt

        monkeypatch.setattr(tb.asyncio, "run", boom)
        with caplog.at_level(logging.INFO):
            tb._run_until_interrupted(lambda: None)  # non deve sollevare
        assert "interrotto" in caplog.text

    def test_runs_the_given_factory(self, monkeypatch):
        ran = []
        monkeypatch.setattr(tb.asyncio, "run", ran.append)
        tb._run_until_interrupted(lambda: "coro")
        assert ran == ["coro"]


class _FakeStatus:
    async def delete(self):
        pass

    async def edit_text(self, *args, **kwargs):
        pass


class _FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return _FakeStatus()


class TestPortfolioReadonly:
    """/portfolio deve solo mostrare i dati: nessun bottone BUY/SELL (richiesta
    dell'utente) — messaggio più leggero e risposta più rapida."""

    def _run(self, monkeypatch, positions=None):
        from helpers import make_settings

        pos_map = positions or {}

        class StubPortfolio:
            cash = 10_000.0
            history: list = []

            @classmethod
            def load(cls, **kwargs):
                return cls()

        StubPortfolio.positions = pos_map

        monkeypatch.setattr(tb, "Portfolio", StubPortfolio)
        update = SimpleNamespace(message=_FakeMessage())
        ctx = SimpleNamespace(bot_data={"settings": make_settings()})
        asyncio.run(tb.cmd_portfolio(update, ctx))
        return update.message.calls

    def test_no_trade_keyboard(self, monkeypatch):
        text, kwargs = self._run(monkeypatch)[-1]
        assert kwargs.get("reply_markup") is None  # niente bottoni BUY/SELL
        assert "Nessuna posizione aperta" in text

    def test_no_trade_keyboard_with_positions(self, monkeypatch):
        # anche con posizioni aperte resta solo lettura
        from logitrade.paper import Position
        pos = {"PEPE/USDT": Position(symbol="PEPE/USDT", qty=10.0,
                                      entry_price=0.001, entry_time="2026-11-03T00:00:00+00:00")}
        monkeypatch.setattr(tb, "fetch_last_price",
                            lambda symbol, exchange_id: 0.002)  # niente rete nel test
        text, kwargs = self._run(monkeypatch, positions=pos)[-1]
        assert kwargs.get("reply_markup") is None
        assert "PEPE/USDT" in text


class TestHelpHeader:
    def test_shows_active_strategy_from_config(self, monkeypatch):
        from helpers import make_settings
        update = SimpleNamespace(message=_FakeMessage())
        ctx = SimpleNamespace(bot_data={
            "settings": make_settings(strategy_family="breakout", timeframe="1d")})
        asyncio.run(tb.cmd_help(update, ctx))
        text, kwargs = update.message.calls[-1]
        assert "Strategia attiva: <b>breakout</b>" in text  # niente piu' "RSI + MACD" fisso
        assert "timeframe 1d" in text
        assert "/buy" in text and "/sell" in text
        assert "portafoglio simulato (solo lettura)" in text
        assert kwargs.get("parse_mode") == tb.ParseMode.HTML


class TestConflictHintFilter:
    def _record(self, exc_info=None):
        return logging.LogRecord("telegram.ext.Updater", logging.ERROR, __file__, 42,
                                 "Exception happened while polling for updates.", None, exc_info)

    def test_hint_added_for_conflict_exception(self):
        exc = Conflict("Conflict: terminated by other getUpdates request; "
                       "make sure that only one bot instance is running")
        rec = self._record(exc_info=(type(exc), exc, None))
        assert tb._ConflictHintFilter().filter(rec) is True
        msg = rec.getMessage()
        assert "bot.pid" in msg
        assert "polling" in msg  # il messaggio originale resta

    def test_no_hint_for_other_errors(self):
        rec = self._record(exc_info=(ValueError, ValueError("x"), None))
        original = rec.getMessage()
        tb._ConflictHintFilter().filter(rec)
        assert rec.getMessage() == original

    def test_hint_is_not_duplicated(self):
        exc = Conflict("only one bot instance is running")
        rec = self._record(exc_info=(type(exc), exc, None))
        f = tb._ConflictHintFilter()
        f.filter(rec)
        f.filter(rec)  # doppia applicazione: nessun doppio hint
        assert rec.getMessage().count("bot.pid") == 1
