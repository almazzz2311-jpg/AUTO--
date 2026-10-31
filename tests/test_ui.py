import io

import pytest
from PIL import Image
from PySide6.QtCore import QRect, QSize, QPoint, Qt
from PySide6.QtGui import QPixmap, QColor
from PySide6.QtTest import QTest

from app.ui.capture import Selection, pixel_rect, Capture
from app.ui.windows import Assistant, SettingsDialog, HistoryDialog, apply_theme
from app.core.storage import Settings, History
from app.core.ocr import prepare, enhanced, recognize, OCRError
from app.ui.markdown import render


@pytest.mark.parametrize('scale', [1, 1.25, 1.5, 2])
def test_pixel_coordinates_at_dpi_scales(scale):
    result = pixel_rect(QRect(20, 30, 100, 80), QSize(800, 600), QSize(round(800*scale), round(600*scale)))
    assert result == QRect(round(20*scale), round(30*scale), round(100*scale), round(80*scale))


class FakeScreen:
    def geometry(self):
        return QRect(-800, 0, 800, 600)


def test_selection_crop_and_escape(qapp):
    snapshot = QPixmap(1600, 1200)
    snapshot.fill(QColor('white'))
    overlay = Selection(FakeScreen(), snapshot)
    results, cancelled = [], []
    overlay.selected.connect(lambda png, point: results.append((png, point)))
    overlay.cancelled.connect(lambda: cancelled.append(True))
    overlay.show()
    QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(20, 30))
    QTest.mouseMove(overlay, QPoint(120, 110))
    QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(120, 110))
    image = Image.open(io.BytesIO(results[0][0]))
    # QRect includes both endpoints of the drag.
    assert image.size == (202, 162)
    assert image.getpixel((10, 10))[:3] == (255, 255, 255)
    assert results[0][1].x() < 0
    QTest.keyClick(overlay, Qt.Key.Key_Escape)
    assert cancelled == [True]
    overlay.close()


def test_window_send_copy_busy_and_hide(qapp):
    window = Assistant()
    sent, closed = [], []
    window.send_requested.connect(lambda text, followup: sent.append((text, followup)))
    window.closed.connect(lambda: closed.append(True))
    window.source.setPlainText('Тест')
    window.send()
    window.question.setText('Почему?')
    window.send_followup()
    assert sent == [('Тест', False), ('Почему?', True)]
    window.answer_text = '**Ответ**'
    window.render_formulas()
    window.copy()
    assert qapp.clipboard().text() == '**Ответ**'
    window.set_busy(True)
    assert not window.send_button.isEnabled()
    assert window.stop_button.isEnabled()
    window.show()
    window.close()
    assert not window.isVisible() and closed


def test_settings_dialog_values(qapp):
    dialog = SettingsDialog(Settings(), None)
    dialog.temperature.setValue(0.9)
    dialog.theme.setCurrentIndex(1)
    dialog.auto.setChecked(True)
    values = dialog.values()
    assert values.temperature == 0.9 and values.theme == 'dark' and values.auto_send
    dialog.close()


def test_history_ui_restores_record(qapp, tmp_path):
    history = History(tmp_path)
    history.add('question', 'answer')
    dialog = HistoryDialog(history)
    restored = []
    dialog.restore.connect(lambda p, a: restored.append((p, a)))
    dialog.rows.setCurrentRow(0)
    dialog.open_selected()
    assert restored == [('question', 'answer')]
    history.close()


def test_preprocessing_stays_in_memory_and_bounds_size():
    image = Image.new('RGB', (600, 80), 'white')
    stream = io.BytesIO()
    image.save(stream, 'PNG')
    png = prepare(stream.getvalue())
    assert Image.open(io.BytesIO(png)).size == (1200, 160)
    assert Image.open(io.BytesIO(enhanced(png))).mode == 'L'


def test_markdown_formulas_and_remote_image_protection():
    html, images = render(r'**Ответ**: \[x_1=\frac{5-1}{2}=2\] ![x](https://untrusted.example/a.png) <script>bad()</script>')
    assert '<strong>Ответ</strong>' in html
    assert len(images) == 1
    assert next(iter(images.values())).startswith(b'\x89PNG')
    assert '<script>' not in html
    assert 'src="https:' not in html


def test_code_formula_is_not_rendered():
    html, images = render('```python\ns = "$x$"\n```\n`$y$`')
    assert not images
    assert '$x$' in html
