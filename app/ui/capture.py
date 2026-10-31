"""Freeze each QScreen before overlays appear; crop in that screen's pixel space."""
from PySide6.QtCore import Qt, QRect, QPoint, Signal, QObject, QBuffer, QIODevice
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget, QApplication


def pixel_rect(rect: QRect, logical_size, pixel_size) -> QRect:
    sx = pixel_size.width() / logical_size.width()
    sy = pixel_size.height() / logical_size.height()
    x1, y1 = round(rect.left() * sx), round(rect.top() * sy)
    x2, y2 = round((rect.left() + rect.width()) * sx), round((rect.top() + rect.height()) * sy)
    return QRect(x1, y1, max(1, x2 - x1), max(1, y2 - y1))


class Selection(QWidget):
    selected = Signal(bytes, QPoint)
    cancelled = Signal()

    def __init__(self, screen, snapshot):
        super().__init__()
        self.screen_ref = screen
        self.snapshot = snapshot
        self.origin = None
        self.selection = QRect()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.snapshot)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 115))
        if not self.selection.isEmpty():
            source = pixel_rect(self.selection, self.size(), self.snapshot.size())
            painter.drawPixmap(self.selection, self.snapshot, source)
            painter.setPen(QPen(QColor('#b9adff'), 2))
            painter.drawRect(self.selection)
        painter.setPen(QColor('white'))
        painter.drawText(24, 34, 'Выделите текст • Esc — отмена • Область в пределах одного монитора')

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
        elif event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.position().toPoint()
            self.selection = QRect(self.origin, self.origin)

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            self.selection = QRect(self.origin, event.position().toPoint()).normalized().intersected(self.rect())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.origin is None:
            return
        if self.selection.width() < 5 or self.selection.height() < 5:
            self.cancelled.emit()
            return
        crop = self.snapshot.copy(pixel_rect(self.selection, self.size(), self.snapshot.size()))
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        crop.save(buffer, 'PNG')
        self.selected.emit(bytes(buffer.data()), self.mapToGlobal(self.selection.bottomRight()))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()


class Capture(QObject):
    selected = Signal(bytes, QPoint)
    cancelled = Signal()

    def __init__(self):
        super().__init__()
        self.overlays = []

    def begin(self):
        if self.overlays:
            return
        # All screenshots are taken before the first overlay, with no disk writes.
        shots = [(screen, screen.grabWindow(0)) for screen in QApplication.screens()]
        if any(pixmap.isNull() for _, pixmap in shots):
            raise RuntimeError('Не удалось захватить экран. Проверьте разрешения и активный рабочий стол.')
        for screen, snapshot in shots:
            overlay = Selection(screen, snapshot)
            overlay.selected.connect(self.finish)
            overlay.cancelled.connect(self.cancel)
            self.overlays.append(overlay)
        for overlay in self.overlays:
            overlay.show()
        if self.overlays:
            self.overlays[0].activateWindow()
            self.overlays[0].setFocus()

    def close(self):
        for overlay in self.overlays:
            overlay.close()
            overlay.deleteLater()
        self.overlays.clear()

    def finish(self, png, point):
        self.close()
        self.selected.emit(png, point)

    def cancel(self):
        self.close()
        self.cancelled.emit()
