"""Windows entry point; --smoke-test validates GUI startup without credentials."""
import argparse
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description='AI Screen Assistant')
    parser.add_argument('--tray', action='store_true')
    parser.add_argument('--smoke-test', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'win32' and not args.smoke_test:
        print('Приложение предназначено для Windows 10/11. Для проверки GUI: --smoke-test', file=sys.stderr)
        return 1
    if sys.platform == 'win32':
        import ctypes
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
    from PySide6.QtCore import QTimer, QLockFile, QStandardPaths
    from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon
    from qasync import QEventLoop
    from app.core.storage import CredentialStore, data_directory
    from app.controller import Controller
    app = QApplication(sys.argv[:1])
    app.setApplicationName('AI Screen Assistant')
    app.setOrganizationName('AI Screen Assistant')
    app.setQuitOnLastWindowClosed(False)
    temporary = tempfile.TemporaryDirectory() if args.smoke_test else None
    directory = Path(temporary.name) if temporary else data_directory()
    lock = QLockFile(str(directory / 'application.lock'))
    if not lock.tryLock(100):
        QMessageBox.information(None, 'AI Screen Assistant', 'Приложение уже запущено. Откройте его из системного трея.')
        return 0
    handler = RotatingFileHandler(directory / 'errors.log', maxBytes=256000, backupCount=2, encoding='utf-8')
    logger = logging.getLogger('assistant')
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    # Smoke mode does not read, store, or send credentials.
    class EmptyCredentials:
        def get(self):
            return ''
    try:
        controller = Controller(app, directory, EmptyCredentials() if args.smoke_test else CredentialStore(), smoke=args.smoke_test)
        if args.smoke_test:
            controller.show()
            QTimer.singleShot(400, controller.quit)
        elif not args.tray and not controller.credentials.get():
            controller.show()
            QTimer.singleShot(0, controller.show_settings)
        elif not QSystemTrayIcon.isSystemTrayAvailable():
            controller.show()
        with loop:
            loop.run_forever()
    except Exception:
        logger.error('startup_failed')
        QMessageBox.critical(None, 'AI Screen Assistant', 'Не удалось запустить приложение. Проверьте зависимости и доступ к локальному хранилищу.')
        return 1
    finally:
        lock.unlock()
        handler.close()
        logger.removeHandler(handler)
        if temporary:
            temporary.cleanup()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
