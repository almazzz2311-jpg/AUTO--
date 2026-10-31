import asyncio

from app.controller import Controller
from app.core.gigachat import APIError


class EmptyCredentials:
    def get(self):
        return ''


class FakeClient:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
    async def stream(self, messages, *args):
        self.calls.append(messages)
        yield 'Тестовый '
        if self.fail:
            raise APIError('Соединение прервано.')
        yield 'ответ'
    async def close(self):
        return


def cleanup(controller):
    controller.window.render_timer.stop()
    controller.hotkeys.clear()
    controller.tray.hide()
    controller.history.close()
    controller.window.hide()


async def test_end_to_end_text_followup_retry_and_history(qapp, tmp_path):
    controller = Controller(qapp, tmp_path, EmptyCredentials(), smoke=True)
    client = FakeClient()
    controller.client = client
    try:
        await controller.generate('Первый вопрос')
        assert controller.window.answer_text == 'Тестовый ответ'
        assert len(controller.history.search()) == 1
        await controller.generate('Уточнение', followup=True)
        assert [m['role'] for m in client.calls[-1]] == ['system', 'user', 'assistant', 'user']
        text, base = controller.last_request
        await controller.generate(text, base=base)
        assert len(client.calls[-1]) == 4  # Retry does not duplicate previous answer.
        assert not controller.window.stop_button.isEnabled()
        assert controller.window.send_button.isEnabled()
    finally:
        cleanup(controller)


async def test_failed_stream_keeps_partial_visible_and_context_clean(qapp, tmp_path):
    controller = Controller(qapp, tmp_path, EmptyCredentials(), smoke=True)
    controller.client = FakeClient(fail=True)
    try:
        await controller.generate('Вопрос')
        assert controller.window.answer_text == 'Тестовый '
        assert not controller.messages
        assert not controller.history.search()
        assert 'прервано' in controller.window.status.text()
    finally:
        cleanup(controller)


async def test_history_can_be_disabled(qapp, tmp_path):
    controller = Controller(qapp, tmp_path, EmptyCredentials(), smoke=True)
    controller.settings.save_history = False
    controller.client = FakeClient()
    try:
        await controller.generate('Не сохранять')
        assert not controller.history.search()
        assert controller.messages[-1]['role'] == 'assistant'
    finally:
        cleanup(controller)


async def test_cancellation_does_not_commit_partial_answer(qapp, tmp_path):
    class WaitingClient:
        async def stream(self, *args):
            yield 'Часть'
            await asyncio.Event().wait()
    controller = Controller(qapp, tmp_path, EmptyCredentials(), smoke=True)
    controller.client = WaitingClient()
    try:
        task = asyncio.create_task(controller.generate('Вопрос'))
        await asyncio.sleep(0)
        task.cancel()
        await task
        assert not controller.messages and not controller.history.search()
        assert 'остановлена' in controller.window.status.text()
    finally:
        cleanup(controller)
