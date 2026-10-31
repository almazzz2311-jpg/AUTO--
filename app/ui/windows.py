import copy

from PySide6.QtCore import Qt, Signal, QTimer, QPoint, QUrl, QPropertyAnimation
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QKeySequence, QImage, QTextDocument
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QPlainTextEdit, QTextBrowser, QComboBox, QLineEdit, QDialog, QFormLayout, QCheckBox,
    QSpinBox, QDoubleSpinBox, QDialogButtonBox, QKeySequenceEdit, QListWidget, QSplitter,
    QMessageBox, QFileDialog, QScrollArea)


def app_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor('#8B7CF6'))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 17, 17)
    painter.setPen(QColor('white'))
    font = painter.font()
    font.setPixelSize(34)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, '✦')
    painter.end()
    return QIcon(pixmap)


def apply_theme(app, choice):
    dark = choice == 'dark' or (choice == 'system' and app.styleHints().colorScheme() == Qt.ColorScheme.Dark)
    bg, card, text, muted, border = (('#151518', '#202024', '#F5F5F7', '#A1A1AA', '#303036') if dark
                                     else ('#f4f4f8', '#ffffff', '#191923', '#616171', '#dddde7'))
    app.setStyleSheet(f'''
        QWidget {{ color: {text}; font-family: "Segoe UI Variable", "Segoe UI", sans-serif; font-size: 14px; }}
        QDialog, QWidget#assistant, QWidget#history {{ background: {bg}; }}
        QLabel#muted {{ color: {muted}; font-size: 12px; }}
        QLabel#title {{ font-size: 19px; font-weight: 600; }}
        QPlainTextEdit, QTextBrowser, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QKeySequenceEdit, QListWidget {{
            background: {card}; border: 1px solid {border}; border-radius: 10px; padding: 8px; selection-background-color: #6556c6; }}
        QTextBrowser {{ border: none; padding: 12px; }}
        QPushButton {{ background: {card}; border: 1px solid {border}; border-radius: 9px; padding: 8px 12px; }}
        QPushButton:hover {{ border-color: #8B7CF6; }}
        QPushButton:disabled {{ color: {muted}; }}
        QPushButton#accent {{ background: #7765dd; color: white; border: none; }}
        QCheckBox {{ padding: 4px; }}
        QMenu {{ background: {card}; padding: 6px; border: 1px solid {border}; }}
        QMenu::item {{ padding: 8px 18px; }}
        QMenu::item:selected {{ background: #7765dd; color: white; }}
        QScrollBar:vertical {{ width: 8px; background: {card}; }}
    ''')


def button(text, callback=None, accent=False):
    result = QPushButton(text)
    if accent:
        result.setObjectName('accent')
    if callback:
        result.clicked.connect(callback)
    return result


class Assistant(QWidget):
    capture_requested = Signal()
    send_requested = Signal(str, bool)
    retry_requested = Signal()
    settings_requested = Signal()
    cancel_requested = Signal()
    closed = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName('assistant')
        self.setWindowTitle('AI Screen Assistant')
        self.setWindowIcon(app_icon())
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.resize(580, 720)
        self.setMinimumSize(440, 500)
        self.answer_text = ''
        self.drag_origin = None
        self.background = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel('✦  AI Assistant')
        title.setObjectName('title')
        header.addWidget(title)
        header.addStretch()
        header.addWidget(button('Настройки', self.settings_requested.emit))
        header.addWidget(button('×', self.close))
        layout.addLayout(header)
        self.status = QLabel('GigaChat • подключение не проверено')
        self.status.setObjectName('muted')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        controls = QHBoxLayout()
        controls.addWidget(button('＋ Выделить экран', self.capture_requested.emit))
        controls.addStretch()
        self.mode = QComboBox()
        self.mode.addItems(['Кратко', 'Подробно', 'Пошагово', 'Перевод'])
        self.mode.setCurrentText('Подробно')
        controls.addWidget(self.mode)
        layout.addLayout(controls)
        self.source_toggle = button('▾ Распознанный текст — можно редактировать', self.toggle_source)
        layout.addWidget(self.source_toggle)
        self.source = QPlainTextEdit()
        self.source.setPlaceholderText('Выделите область экрана или введите вопрос…')
        self.source.setMaximumHeight(105)
        layout.addWidget(self.source)
        privacy = QLabel('В GigaChat отправляется текст после нажатия «Отправить».\nOCR может ошибаться, особенно в формулах. Проверьте условие.')
        privacy.setWordWrap(True)
        privacy.setObjectName('muted')
        layout.addWidget(privacy)
        self.attach = QCheckBox('Прикрепить выделенное изображение к запросу')
        self.attach.setToolTip('Изображение загружается в облако GigaChat. Нужна модель с поддержкой изображений. По умолчанию передаётся только текст.')
        self.attach.setEnabled(False)
        layout.addWidget(self.attach)
        self.answer = QTextBrowser()
        self.answer.setMinimumHeight(190)
        self.answer.setOpenExternalLinks(False)
        self.answer.setOpenLinks(False)
        self.answer.anchorClicked.connect(self.open_link)
        self.answer.document().setDefaultStyleSheet('pre { white-space: pre-wrap; } code { font-family: Consolas; }')
        self.answer.setMarkdown('## Текст с экрана → понятный ответ\n\nВыделите задачу, проверьте распознанный текст и отправьте его в GigaChat.')
        layout.addWidget(self.answer, 1)
        self.question = QLineEdit()
        self.question.setPlaceholderText('Задать уточняющий вопрос…')
        self.question.returnPressed.connect(self.send_followup)
        layout.addWidget(self.question)
        actions = QHBoxLayout()
        self.copy_button = button('Копировать', self.copy)
        self.retry_button = button('Повторить', self.retry_requested.emit)
        self.stop_button = button('Стоп', self.cancel_requested.emit)
        self.send_button = button('Отправить', self.send, True)
        for item in (self.copy_button, self.retry_button, self.stop_button, self.send_button):
            actions.addWidget(item)
        self.stop_button.setEnabled(False)
        layout.addLayout(actions)
        self.shortcut_hint = QLabel('Ctrl+Shift+A — выделить • Ctrl+Shift+Q — показать/скрыть')
        self.shortcut_hint.setObjectName('muted')
        self.shortcut_hint.setWordWrap(True)
        layout.addWidget(self.shortcut_hint)
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(60)
        self.render_timer.timeout.connect(self.render_answer)

    def toggle_source(self):
        self.source.setVisible(not self.source.isVisible())

    def open_link(self, url):
        from PySide6.QtGui import QDesktopServices
        if url.scheme() in ('https', 'http'):
            QDesktopServices.openUrl(url)

    def send(self):
        if self.question.text().strip():
            self.send_followup()
        else:
            self.send_requested.emit(self.source.toPlainText().strip(), False)

    def send_followup(self):
        text = self.question.text().strip()
        if text:
            self.send_requested.emit(text, True)

    def copy(self):
        if self.answer_text:
            QApplication.clipboard().setText(self.answer_text)

    def append_answer(self, chunk):
        self.answer_text += chunk
        if not self.render_timer.isActive():
            self.render_timer.start()

    def render_answer(self):
        from app.ui.markdown import safe_markdown
        self.answer.setHtml(safe_markdown(self.answer_text))
        scroll = self.answer.verticalScrollBar()
        scroll.setValue(scroll.maximum())

    def render_formulas(self):
        from app.ui.markdown import render
        try:
            html, resources = render(self.answer_text)
        except Exception:
            self.render_answer()
            return
        document = self.answer.document()
        for name, png in resources.items():
            document.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), QImage.fromData(png))
        self.answer.setHtml(html)
        scroll = self.answer.verticalScrollBar()
        scroll.setValue(scroll.maximum())

    def set_busy(self, busy):
        self.send_button.setEnabled(not busy)
        self.retry_button.setEnabled(not busy)
        self.question.setEnabled(not busy)
        self.source.setReadOnly(busy)
        self.mode.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.attach.setEnabled(not busy and bool(self.attach.property("has_image")))

    def showEvent(self, event):
        super().showEvent(event)
        if __import__('sys').platform == 'win32':
            import ctypes
            # Native rounded corners on Windows 11; unsupported versions ignore it.
            value = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(int(self.winId())), 33, ctypes.byref(value), 4)
        self.fade = QPropertyAnimation(self, b'windowOpacity', self)
        self.fade.setDuration(120)
        self.fade.setStartValue(0.7)
        self.fade.setEndValue(1.0)
        self.fade.start()

    def show_near(self, point):
        screen = QApplication.screenAt(point) or QApplication.primaryScreen()
        bounds = screen.availableGeometry()
        self.resize(min(self.width(), bounds.width()), min(self.height(), bounds.height()))
        x = max(bounds.left(), min(point.x() + 12, bounds.right() - self.width() + 1))
        y = max(bounds.top(), min(point.y() + 12, bounds.bottom() - self.height() + 1))
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self.drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_origin)

    def mouseReleaseEvent(self, event):
        self.drag_origin = None

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.closed.emit()


class SettingsDialog(QDialog):
    test_requested = Signal()

    def __init__(self, settings, credentials, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Настройки • AI Screen Assistant')
        self.resize(540, 650)
        self.settings = copy.deepcopy(settings)
        self.credentials = credentials
        self.result_settings = None
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText('Новый Authorization Key (пусто — сохранить текущий)')
        form.addRow('GigaChat API', self.key)
        self.forget = QCheckBox('Удалить сохранённый ключ')
        form.addRow('', self.forget)
        self.scope = QComboBox()
        self.scope.addItems(['GIGACHAT_API_PERS', 'GIGACHAT_API_B2B', 'GIGACHAT_API_CORP'])
        self.scope.setCurrentText(settings.scope)
        form.addRow('Scope', self.scope)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItem(settings.model)
        form.addRow('Модель', self.model)
        self.test_button = button('Проверить подключение и модели', self.test_requested.emit)
        form.addRow('', self.test_button)
        self.connection = QLabel('Ключ хранится только в Windows Credential Manager.')
        self.connection.setWordWrap(True)
        form.addRow('', self.connection)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0, 2)
        self.temperature.setSingleStep(0.1)
        self.temperature.setValue(settings.temperature)
        form.addRow('Температура', self.temperature)
        self.tokens = QSpinBox()
        self.tokens.setRange(1, 32768)
        self.tokens.setValue(settings.max_tokens)
        form.addRow('Максимум токенов', self.tokens)
        self.theme = QComboBox()
        for label, value in [('Как в системе', 'system'), ('Тёмная', 'dark'), ('Светлая', 'light')]:
            self.theme.addItem(label, value)
        self.theme.setCurrentIndex(self.theme.findData(settings.theme))
        form.addRow('Тема', self.theme)
        self.language = QComboBox()
        for label, value in [('Языки профиля Windows', 'auto'), ('Русский', 'ru-RU'), ('English', 'en-US')]:
            self.language.addItem(label, value)
        self.language.setCurrentIndex(max(0, self.language.findData(settings.ocr_language)))
        form.addRow('Язык OCR', self.language)
        self.auto = QCheckBox('Отправлять текст автоматически после OCR')
        self.auto.setChecked(settings.auto_send)
        form.addRow('', self.auto)
        self.history = QCheckBox('Сохранять историю локально (содержит текст запросов)')
        self.history.setChecked(settings.save_history)
        form.addRow('', self.history)
        self.background = QCheckBox('Продолжать работу в трее при закрытии окна')
        self.background.setChecked(settings.background)
        form.addRow('', self.background)
        self.startup = QCheckBox('Запускать вместе с Windows')
        self.startup.setChecked(settings.startup)
        form.addRow('', self.startup)
        self.ca = QLineEdit(settings.ca_file)
        form.addRow('Дополнительный CA (PEM)', self.ca)
        form.addRow('', button('Выбрать доверенный сертификат…', self.choose_ca))
        self.hotkeys = {}
        for action, label in [('capture', 'Выделить экран'), ('toggle', 'Показать окно'), ('copy', 'Копировать ответ')]:
            editor = QKeySequenceEdit(QKeySequence(settings.hotkeys[action]))
            editor.setMaximumSequenceLength(1)
            self.hotkeys[action] = editor
            form.addRow(label, editor)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self.error = QLabel('')
        self.error.setWordWrap(True)
        outer.addWidget(self.error)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        outer.addWidget(box)

    def choose_ca(self):
        path, _ = QFileDialog.getOpenFileName(self, 'CA сертификат', '', 'PEM (*.pem *.crt);;Все файлы (*)')
        if path:
            self.ca.setText(path)

    def values(self):
        result = copy.deepcopy(self.settings)
        result.scope = self.scope.currentText()
        result.model = self.model.currentText().strip()
        result.temperature = self.temperature.value()
        result.max_tokens = self.tokens.value()
        result.theme = self.theme.currentData()
        result.ocr_language = self.language.currentData()
        result.auto_send = self.auto.isChecked()
        result.save_history = self.history.isChecked()
        result.background = self.background.isChecked()
        result.startup = self.startup.isChecked()
        result.ca_file = self.ca.text().strip()
        result.hotkeys = {key: editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
                          for key, editor in self.hotkeys.items()}
        result.validate()
        return result


class HistoryDialog(QDialog):
    restore = Signal(str, str)

    def __init__(self, history, parent=None):
        super().__init__(parent)
        self.history = history
        self.setWindowTitle('История запросов • локально на этом компьютере')
        self.resize(820, 580)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Поиск по тексту или ответу… (до 500 записей)')
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)
        split = QSplitter()
        self.rows = QListWidget()
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        self.rows.currentRowChanged.connect(self.select)
        split.addWidget(self.rows)
        split.addWidget(self.preview)
        layout.addWidget(split)
        actions = QHBoxLayout()
        actions.addWidget(button('Открыть', self.open_selected))
        actions.addWidget(button('Удалить', self.delete_selected))
        actions.addWidget(button('Очистить всё', self.clear))
        layout.addLayout(actions)
        self.refresh()

    def refresh(self):
        self.records = self.history.search(self.search.text())
        self.rows.clear()
        self.preview.clear()
        for _, created, prompt, _ in self.records:
            self.rows.addItem(f'{created}\n{prompt[:85]}')

    def select(self, index):
        if 0 <= index < len(self.records):
            from app.ui.markdown import safe_markdown
            self.preview.setHtml(safe_markdown(self.records[index][2] + '\n\n---\n\n' + self.records[index][3]))

    def open_selected(self):
        index = self.rows.currentRow()
        if index >= 0:
            self.restore.emit(self.records[index][2], self.records[index][3])
            self.accept()

    def delete_selected(self):
        index = self.rows.currentRow()
        if index >= 0:
            self.history.delete(self.records[index][0])
            self.refresh()

    def clear(self):
        if QMessageBox.question(self, 'Удалить историю', 'Удалить все сохранённые запросы и ответы?') == QMessageBox.StandardButton.Yes:
            self.history.clear()
            self.refresh()
