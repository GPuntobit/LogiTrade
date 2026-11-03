"""Test della singola istanza del bot (bot.pid) e dell'uscita pulita con Ctrl-C."""

import logging
import os

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
