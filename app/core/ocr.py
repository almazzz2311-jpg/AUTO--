"""Windows OCR; screenshots stay in memory. WinRT runs in a worker thread."""
import asyncio
import io
import sys

from PIL import Image, ImageEnhance, ImageFilter, ImageOps


class OCRError(Exception):
    pass


def prepare(png: bytes, max_dimension=2600) -> bytes:
    image = Image.open(io.BytesIO(png)).convert('RGB')
    # Only enlarge small captures. Do not always sharpen/threshold good originals.
    if image.height < 160 and max(image.size) * 2 <= max_dimension:
        image = image.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
    if max(image.size) > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


def enhanced(png: bytes) -> bytes:
    image = Image.open(io.BytesIO(png)).convert('L')
    image = ImageOps.autocontrast(image).filter(ImageFilter.MedianFilter(3))
    image = ImageEnhance.Sharpness(image).enhance(1.3)
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


async def _recognize(png: bytes, language: str) -> str:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder, SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import InMemoryRandomAccessStream, DataWriter

    engine = (OcrEngine.try_create_from_user_profile_languages() if language == 'auto'
              else OcrEngine.try_create_from_language(Language(language)))
    if engine is None:
        raise OCRError('Язык OCR не установлен. Добавьте русский/английский OCR в компонентах Windows.')
    stream = InMemoryRandomAccessStream()
    try:
        writer = DataWriter(stream)
        writer.write_bytes(png)
        await writer.store_async()
        await writer.flush_async()
        writer.detach_stream()
        writer.close()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        converted = SoftwareBitmap.convert_with_alpha(bitmap, BitmapPixelFormat.BGRA8, BitmapAlphaMode.IGNORE)
        try:
            result = await engine.recognize_async(converted)
            return '\n'.join(line.text for line in result.lines).strip()
        finally:
            converted.close()
            bitmap.close()
    finally:
        stream.close()


def recognize(png: bytes, language='auto', improve=False) -> str:
    if sys.platform != 'win32':
        raise OCRError('Распознавание экрана использует Windows OCR и требует Windows 10/11.')
    try:
        processed = prepare(png)
        if improve:
            processed = enhanced(processed)
        from winrt.runtime import init_apartment, uninit_apartment, ApartmentType
        init_apartment(ApartmentType.MULTI_THREADED)
        try:
            text = asyncio.run(_recognize(processed, language))
        finally:
            uninit_apartment()
        if not text:
            raise OCRError('Текст не найден. Выделите более крупный фрагмент или включите улучшение изображения.')
        return text
    except OCRError:
        raise
    except ImportError as exc:
        raise OCRError('Не установлены WinRT-компоненты. Выполните установку requirements.txt.') from exc
    except Exception as exc:
        raise OCRError('Windows OCR не смог прочитать изображение. Проверьте языковые компоненты.') from exc
