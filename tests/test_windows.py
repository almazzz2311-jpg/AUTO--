"""These are actual Windows checks, explicitly skipped on other platforms."""
import io
import os
import sys

import pytest
from PIL import Image, ImageDraw, ImageFont

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Requires real Windows OCR / Win32')


@pytest.mark.parametrize('language, text, expected', [('ru-RU', 'Привет мир 123', 'Привет'),
                                                     ('en-US', 'Hello world 123', 'Hello'),
                                                     ('en-US', 'x + 2 = 5', '2')])
def test_real_windows_ocr(language, text, expected):
    from app.core.ocr import recognize
    image = Image.new('RGB', (750, 120), 'white')
    font = ImageFont.truetype(os.path.join(os.environ['WINDIR'], 'Fonts', 'arial.ttf'), 44)
    ImageDraw.Draw(image).text((20, 25), text, fill='black', font=font)
    stream = io.BytesIO()
    image.save(stream, 'PNG')
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(recognize, stream.getvalue(), language).result(timeout=30)
    assert expected.casefold() in result.casefold()


def test_real_win32_hotkey_registration(qapp):
    from app.core.windows import Hotkeys
    hotkeys = Hotkeys(qapp)
    try:
        hotkeys.configure({'capture': 'Ctrl+Shift+F20', 'toggle': 'Ctrl+Shift+F21', 'copy': 'Ctrl+Shift+F22'})
        assert len(hotkeys.registered) == 3
    finally:
        hotkeys.clear()
        qapp.removeNativeEventFilter(hotkeys)
