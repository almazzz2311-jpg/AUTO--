import asyncio
import logging
import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon, QDialog

from app.core.gigachat import GigaChat, APIError, system_prompt
from app.core.ocr import recognize, OCRError
from app.core.storage import SettingsStore, Settings, History
from app.core.windows import Hotkeys, set_startup, startup_enabled
from app.ui.capture import Capture
from app.ui.windows import Assistant, SettingsDialog, HistoryDialog, apply_theme, app_icon

logger = logging.getLogger('assistant')


class Controller:
    def __init__(self, app, directory, credentials, smoke=False):
        self.app = app
        self.credentials = credentials
        self.store = SettingsStore(directory)
        self.load_error = ''
        try:
            self.settings = self.store.load()
        except (ValueError, TypeError, OSError):
            self.settings = Settings()
            self.load_error = 'Настройки повреждены; используются стандартные. Файл не перезаписан.'
        if sys.platform == "win32":
            self.settings.startup = startup_enabled()
        self.history = History(directory)
        self.client = None
        self.task = None
        self.tasks = set()
        self.dialog = None
        self.messages = []
        self.last_request = None
        self.png = None
        self.image_ids = set()
        self.current_image_id = None
        self.paused = False
        self.shutting_down = False
        self.capture_was_visible = False
        self.capture_pending = False
        self.last_attachment = False
        self.window = Assistant()
        self.window.mode.setCurrentText(self.settings.mode)
        self.window.background = self.settings.background
        apply_theme(app, self.settings.theme)
        app.styleHints().colorSchemeChanged.connect(lambda _: apply_theme(app, self.settings.theme))
        self.capture = Capture()
        self.capture.selected.connect(self.captured)
        self.capture.cancelled.connect(self.capture_cancelled)
        self.hotkeys = Hotkeys(app)
        self.hotkeys.signals.activated.connect(self.hotkey)
        self.window.capture_requested.connect(self.begin_capture)
        self.window.send_requested.connect(self.send)
        self.window.retry_requested.connect(self.retry)
        self.window.cancel_requested.connect(self.cancel)
        self.window.settings_requested.connect(self.show_settings)
        self.window.closed.connect(self.window_closed)
        self.tray = QSystemTrayIcon(app_icon(), app)
        self.tray.setToolTip('AI Screen Assistant')
        menu = QMenu()
        for label, callback in [('Открыть ассистента', self.show), ('Выделить область', self.begin_capture),
                                ('История запросов', self.show_history), ('Настройки', self.show_settings)]:
            menu.addAction(label, callback)
        self.pause_action = menu.addAction('Пауза')
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(self.set_paused)
        menu.addSeparator()
        menu.addAction('Выход', self.quit)
        self.tray.setContextMenu(menu)
        self.menu = menu
        self.tray.activated.connect(lambda reason: self.show() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        if not smoke and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        if not smoke:
            try:
                self.hotkeys.configure(self.settings.hotkeys)
            except (ValueError, RuntimeError) as exc:
                self.load_error = str(exc)
        self.update_hint()
        if self.load_error:
            QTimer.singleShot(0, lambda: self.notify(self.load_error))

    def update_hint(self):
        keys = self.settings.hotkeys
        self.window.shortcut_hint.setText(f"{keys['capture']} — выделить • {keys['toggle']} — показать/скрыть")

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def notify(self, message):
        self.window.status.setText(message)
        if self.tray.isVisible() and not self.window.isVisible():
            self.tray.showMessage('AI Screen Assistant', message, QSystemTrayIcon.MessageIcon.Information, 5000)

    def show(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def hotkey(self, action):
        if action == 'toggle':
            self.window.hide() if self.window.isVisible() else self.show()
        elif action == 'copy':
            self.window.copy()
        elif action == 'capture':
            self.begin_capture()

    def set_paused(self, paused):
        self.paused = paused
        if paused:
            self.cancel()
            if self.capture.overlays:
                self.capture.cancel()
        self.notify('Пауза: захват и отправка отключены.' if paused else 'Готов к выделению текста.')

    def begin_capture(self):
        if self.paused or (self.task and not self.task.done()) or self.capture.overlays or self.capture_pending:
            self.notify('Сначала завершите текущую операцию или снимите паузу.')
            return
        self.capture_pending = True
        self.capture_was_visible = self.window.isVisible()
        self.window.hide()
        # Give DWM time to remove our window before freezing the desktop.
        QTimer.singleShot(180, self.start_capture)

    def start_capture(self):
        self.capture_pending = False
        if self.shutting_down or self.paused:
            return
        try:
            self.capture.begin()
        except RuntimeError as exc:
            self.show()
            self.notify(str(exc))

    def capture_cancelled(self):
        if self.capture_was_visible:
            self.show()

    def captured(self, png, point):
        self.png = png
        self.current_image_id = None
        self.window.attach.setProperty('has_image', True)
        self.window.attach.setEnabled(True)
        self.window.attach.setChecked(False)
        self.messages = []
        self.last_request = None
        self.window.source.clear()
        self.window.source.show()
        self.window.question.clear()
        self.window.answer_text = ''
        self.window.answer.clear()
        self.window.show_near(point)
        self.task = self.spawn(self.run_ocr())

    async def run_ocr(self, improve=False):
        self.window.set_busy(True)
        self.notify('Распознавание текста на компьютере…')
        auto = False
        try:
            text = await asyncio.to_thread(recognize, self.png, self.settings.ocr_language, improve)
            self.window.source.setPlainText(text)
            self.notify('Текст распознан. Проверьте его перед отправкой в GigaChat.')
            auto = self.settings.auto_send
        except OCRError as exc:
            self.notify(str(exc))
        except asyncio.CancelledError:
            self.notify('Распознавание отменено.')
        except Exception:
            logger.error('ocr_failed')
            self.notify('Не удалось выполнить OCR.')
        finally:
            self.window.set_busy(False)
        if auto:
            # Defer until this task has completed, avoiding a busy-state race.
            QTimer.singleShot(0, lambda: self.send(self.window.source.toPlainText(), False))

    def send(self, text, followup=False):
        if self.paused:
            self.notify('Снимите паузу перед отправкой.')
            return
        if self.task and not self.task.done():
            return
        if not text.strip():
            self.notify('Введите текст или выделите область экрана.')
            return
        if len(text) > 60000:
            self.notify('Слишком большой текст: сократите запрос до 60 000 символов.')
            return
        self.task = self.spawn(self.generate(text, followup))

    async def get_client(self):
        if self.client is None:
            self.client = GigaChat(self.credentials.get, self.settings.scope, self.settings.ca_file)
        return self.client

    async def generate(self, text, followup=False, base=None):
        self.window.set_busy(True)
        self.notify('GigaChat • генерация ответа…')
        mode = self.window.mode.currentText()
        prior = (self.messages if followup else []) if base is None else base
        prior = [dict(item) for item in prior]
        # Keep complete user/assistant pairs; bound the request's character count.
        while prior and sum(len(item['content']) for item in prior) + len(text) > 60000:
            prior = prior[2:]
        if base is None:
            self.last_attachment = self.window.attach.isChecked() and bool(self.png) and not followup
        self.last_request = (text, prior)
        messages = [{'role': 'system', 'content': system_prompt(mode)}, *prior, {'role': 'user', 'content': text}]
        self.window.answer_text = ''
        self.window.answer.clear()
        try:
            client = await self.get_client()
            if self.last_attachment and self.png:
                if not self.current_image_id:
                    self.notify('Загрузка выделенного изображения в GigaChat…')
                    self.current_image_id = await client.upload_image(self.png)
                    self.image_ids.add(self.current_image_id)
                messages[-1]['attachments'] = [self.current_image_id]
            async for chunk in client.stream(messages, self.settings.model, self.settings.temperature, self.settings.max_tokens):
                self.window.append_answer(chunk)
            self.window.render_timer.stop()
            self.messages = [*prior, messages[-1],
                             {'role': 'assistant', 'content': self.window.answer_text}]
            self.window.question.clear()
            if self.settings.save_history:
                self.history.add(text, self.window.answer_text)
            self.notify(f'GigaChat • ответ получен · {self.settings.model}')
            self.settings.mode = mode
            self.store.save(self.settings)
        except APIError as exc:
            self.notify(str(exc))
        except asyncio.CancelledError:
            self.notify('Генерация остановлена. Неполный ответ не добавлен в контекст.')
        except (OSError, ValueError):
            logger.error('configuration_or_storage_failed')
            self.notify('Ошибка настроек, сертификата или локального хранилища.')
        except Exception:
            logger.error('generation_failed')
            self.notify('Не удалось получить ответ. Проверьте настройки подключения.')
        finally:
            self.window.render_timer.stop()
            self.window.render_formulas()
            self.window.set_busy(False)

    def retry(self):
        if self.paused or (self.task and not self.task.done()):
            return
        if self.last_request:
            text, base = self.last_request
            self.task = self.spawn(self.generate(text, base=base))
        elif not self.last_request and self.png:
            self.task = self.spawn(self.run_ocr(improve=True))

    def cancel(self):
        if self.task and not self.task.done():
            self.task.cancel()

    def show_settings(self):
        if self.task and not self.task.done():
            self.notify('Остановите текущую операцию перед изменением настроек.')
            return
        if self.dialog:
            self.dialog.raise_()
            return
        dialog = SettingsDialog(self.settings, self.credentials, self.window)
        self.dialog = dialog
        dialog.test_requested.connect(lambda: self.spawn(self.test_connection(dialog)))
        dialog.accepted.connect(lambda: self.spawn(self.save_settings(dialog)))
        dialog.finished.connect(lambda _: self.dialog_finished(dialog))
        dialog.open()

    def dialog_finished(self, dialog):
        if self.dialog is dialog:
            self.dialog = None
        # Qt objects stay alive until outstanding async operations complete.

    async def test_connection(self, dialog):
        dialog.test_button.setEnabled(False)
        dialog.connection.setText('Проверка авторизации…')
        client = None
        try:
            values = dialog.values()
            key = dialog.key.text().strip()
            if dialog.forget.isChecked():
                raise APIError('Снимите флажок удаления ключа для проверки.')
            client = GigaChat(lambda: key or self.credentials.get(), values.scope, values.ca_file)
            models = await client.models()
            current = dialog.model.currentText()
            dialog.model.clear()
            dialog.model.addItems(models)
            if current in models:
                dialog.model.setCurrentText(current)
            dialog.connection.setText('GigaChat подключён. Список доступных моделей обновлён.')
        except (APIError, ValueError, OSError, RuntimeError) as exc:
            # Only application-generated exceptions are shown, never HTTP bodies.
            dialog.connection.setText(str(exc) if isinstance(exc, (APIError, ValueError, RuntimeError)) else 'Не удалось прочитать CA-файл.')
        except Exception:
            dialog.connection.setText('Ошибка проверки. Проверьте системное хранилище сертификатов и ключей.')
        finally:
            if client:
                await client.close()
            dialog.test_button.setEnabled(True)

    async def save_settings(self, dialog):
        old = self.settings
        try:
            values = dialog.values()
            from app.core.windows import parse_hotkey
            parsed = [parse_hotkey(value) for value in values.hotkeys.values()]
            if len(set(parsed)) != len(parsed):
                raise ValueError('Горячие клавиши должны отличаться')
            if values.ca_file:
                from app.core.gigachat import tls_context
                tls_context(values.ca_file)
            if sys.platform == 'win32':
                self.hotkeys.configure(values.hotkeys)
                set_startup(values.startup)
            if dialog.forget.isChecked():
                self.credentials.delete()
            elif dialog.key.text().strip():
                self.credentials.set(dialog.key.text())
            self.store.save(values)
            self.settings = values
            if self.client:
                await self.client.close()
                self.client = None
            self.window.background = values.background
            apply_theme(self.app, values.theme)
            self.update_hint()
            self.notify('Настройки сохранены. Подключение можно проверить в настройках.')
        except Exception as exc:
            if sys.platform == 'win32':
                try:
                    self.hotkeys.configure(old.hotkeys)
                    set_startup(old.startup)
                except Exception:
                    self.notify('Не удалось восстановить горячие клавиши. Перезапустите приложение.')
            dialog.error.setText(str(exc) if isinstance(exc, ValueError) else 'Не удалось сохранить настройки или ключ в Windows.')
            self.dialog = dialog
            dialog.open()
        finally:
            dialog.key.clear()

    def show_history(self):
        dialog = HistoryDialog(self.history, self.window)
        dialog.restore.connect(self.restore_history)
        dialog.open()
        self.history_dialog = dialog

    def restore_history(self, prompt, answer):
        if self.task and not self.task.done():
            self.notify('Остановите текущую операцию перед открытием истории.')
            return
        self.png = None
        self.current_image_id = None
        self.window.attach.setChecked(False)
        self.window.attach.setProperty('has_image', False)
        self.window.attach.setEnabled(False)
        self.window.source.setPlainText(prompt)
        self.window.answer_text = answer
        self.window.render_formulas()
        self.messages = [{'role': 'user', 'content': prompt}, {'role': 'assistant', 'content': answer}]
        self.last_request = (prompt, [])
        self.show()

    def window_closed(self):
        if not self.settings.background or not self.tray.isVisible():
            self.quit()

    def quit(self):
        if not self.shutting_down:
            self.shutting_down = True
            self.spawn(self.shutdown())

    async def shutdown(self):
        self.capture.close()
        self.hotkeys.clear()
        self.app.removeNativeEventFilter(self.hotkeys)
        others = [task for task in self.tasks if task is not asyncio.current_task()]
        for task in others:
            task.cancel()
        if others:
            await asyncio.gather(*others, return_exceptions=True)
        if self.client:
            for file_id in self.image_ids:
                try:
                    await asyncio.wait_for(self.client.delete_image(file_id), timeout=5)
                except Exception:
                    logger.warning('remote_image_cleanup_failed')
            await self.client.close()
        self.png = None
        self.tray.hide()
        self.history.close()
        self.app.quit()
