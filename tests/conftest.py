import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('MPLCONFIGDIR', '/tmp/screen-assistant-mpl' if os.name != 'nt' else os.path.join(os.environ['TEMP'], 'screen-assistant-mpl'))

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])
