"""Test dei comandi /buy e /sell: parsing argomenti, tastiera importi, vendita parziale,
conferme mai silenziose (_answer/_deliver) e global error handler."""

import asyncio

import pytest

from logitrade.telegram_bot import (_answer, _amount_keyboard, _deliver, normalize_symbol,
                                    on_error, parse_amount)

FEE = 0.1   # %
SLIP = 0.05  # %


class TestNormalizeSymbol:
    def test_adds_default_quote(self):
        assert normalize_symbol("pepe") == "PEPE/USDT"

    def test_keeps_explicit_pair_uppercased(self):
        assert normalize_symbol("pepe/usdt") == "PEPE/USDT"
        assert normalize_symbol("DOGE/USDT") == "DOGE/USDT"

    def test_dash_as_separator(self):
        assert normalize_symbol("pepe-usdt") == "PEPE/USDT"

    def test_strips_whitespace(self):
        assert normalize_symbol("  wif  ") == "WIF/USDT"

    @pytest.mark.parametrize("bad", ["PEPE/", "/USDT", "PEPE/USDT/EXTRA", " ", "PEPE/<b>"])
    def test_invalid_symbol_raises(self, bad):
        with pytest.raises(ValueError, match="Simbolo non valido"):
            normalize_symbol(bad)


class TestParseAmount:
    def test_absolute_usdt(self):
        assert parse_amount("500", cash=10_000.0) == pytest.approx(500.0)

    def test_comma_decimal_separator(self):
        assert parse_amount("1,5", cash=10_000.0) == pytest.approx(1.5)

    def test_percent_of_cash(self):
        assert parse_amount("25%", cash=8_000.0) == pytest.approx(2_000.0)

    def test_percent_uses_pct_base_when_given(self):
        # /sell PEPE 50% -> metà della posizione (valore netto), non del cash
        assert parse_amount("50%", cash=10_000.0, pct_base=1_000.0) == pytest.approx(500.0)

    @pytest.mark.parametrize("bad", ["abc", "10 USDT", "%"])
    def test_non_numeric_raises(self, bad):
        with pytest.raises(ValueError, match="Importo non valido"):
            parse_amount(bad, cash=10_000.0)

    @pytest.mark.parametrize("bad", ["0", "-5", "0%"])
    def test_non_positive_raises(self, bad):
        with pytest.raises(ValueError, match="positivo"):
            parse_amount(bad, cash=10_000.0)


class TestAmountKeyboard:
    def _datas(self, kb):
        return [b.callback_data for row in kb.inline_keyboard for b in row]

    def test_presets_within_cash_and_special_actions(self):
        kb = _amount_keyboard("PEPE/USDT", cash=5_000.0, position_pct=25.0)
        datas = self._datas(kb)
        assert "buya:PEPE/USDT:100" in datas
        assert "buya:PEPE/USDT:1000" in datas
        assert "buyp:PEPE/USDT" in datas                      # position_pct
        assert "buya:PEPE/USDT:all" in datas                  # tutto il cash
        assert "bcancel" in datas

    def test_presets_above_cash_are_hidden(self):
        datas = self._datas(_amount_keyboard("X/USDT", cash=200.0, position_pct=25.0))
        assert "buya:X/USDT:100" in datas
        assert "buya:X/USDT:250" not in datas
        assert "buya:X/USDT:1000" not in datas
        assert "buya:X/USDT:all" in datas

    def test_pct_button_hidden_when_worthless(self):
        # cash 0 -> nessun pulsante % (sarebbe uno zero)
        datas = self._datas(_amount_keyboard("X/USDT", cash=0.0, position_pct=25.0))
        assert not any(d.startswith("buyp:") for d in datas)

    def test_callback_data_fits_telegram_limit(self):
        # Telegram limita callback_data a 64 byte
        datas = self._datas(_amount_keyboard("BABYDOGE/USDT", cash=1_000.0, position_pct=25.0))
        assert all(len(d.encode()) <= 64 for d in datas)


class FakeMessage:
    def __init__(self, reply_markup=None):
        self.reply_markup = reply_markup
        self.replies = []

    async def reply_text(self, text, parse_mode=None, reply_markup=None):
        self.replies.append((text, parse_mode, reply_markup))


class FakeQuery:
    def __init__(self, edit_error=None, message=True, answer_error=None):
        self.answers = []
        self.edits = []
        self.edit_error = edit_error
        self.answer_error = answer_error
        self.message = FakeMessage() if message else None

    async def answer(self, text=None, show_alert=False):
        if self.answer_error:
            raise self.answer_error
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text, parse_mode=None, reply_markup=None):
        if self.edit_error:
            raise self.edit_error
        self.edits.append(text)


class TestAnswer:
    def test_answer_records(self):
        q = FakeQuery()
        asyncio.run(_answer(q, "ok", alert=True))
        assert q.answers == [("ok", True)]

    def test_answer_swallows_failure(self):
        # doppia risposta/query scaduta: non deve far fallire l'operazione
        q = FakeQuery(answer_error=RuntimeError("query is too old"))
        asyncio.run(_answer(q))  # non solleva
        assert q.answers == []


class TestDeliver:
    def test_edits_message_by_default(self):
        q = FakeQuery()
        asyncio.run(_deliver(q, "<b>conferma</b>"))
        assert q.edits == ["<b>conferma</b>"]
        assert q.message.replies == []

    def test_falls_back_to_new_message_when_edit_fails(self):
        # messaggio non editabile (troppo vecchio): la conferma deve arrivare lo stesso
        q = FakeQuery(edit_error=RuntimeError("message can't be edited"))
        asyncio.run(_deliver(q, "conferma"))
        assert q.edits == []
        assert [r[0] for r in q.message.replies] == ["conferma"]

    def test_not_modified_is_not_redelivered(self):
        # doppio tap: la conferma e' gia' sul messaggio, niente duplicato
        q = FakeQuery(edit_error=RuntimeError("Message is not modified"))
        asyncio.run(_deliver(q, "conferma"))
        assert q.message.replies == []

    def test_prefer_edit_false_replies_new_message(self):
        q = FakeQuery()
        asyncio.run(_deliver(q, "conferma", prefer_edit=False))
        assert q.edits == []
        assert len(q.message.replies) == 1

    def test_raises_without_message_so_on_error_notifies(self):
        q = FakeQuery(message=False)
        with pytest.raises(RuntimeError, match="Nessun messaggio"):
            asyncio.run(_deliver(q, "conferma"))


class _Ctx:
    def __init__(self, error):
        self.error = error


class _Update:
    def __init__(self, message):
        self.effective_message = message


class TestErrorHandler:
    def test_notifies_chat(self):
        upd = _Update(FakeMessage())
        asyncio.run(on_error(upd, _Ctx(RuntimeError("boom <x>"))))
        text = upd.effective_message.replies[0][0]
        assert "Errore interno" in text
        assert "boom &lt;x&gt;" in text  # HTML escaped

    def test_without_message_does_not_raise(self):
        asyncio.run(on_error(None, _Ctx(RuntimeError("boom"))))
