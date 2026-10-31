"""Preflight for a Windows build; no network or credentials required."""
import sys


def main():
    if sys.platform != 'win32':
        raise SystemExit('Windows OCR preflight requires Windows 10/11.')
    from winrt.runtime import init_apartment, ApartmentType
    init_apartment(ApartmentType.MULTI_THREADED)
    from winrt.windows.media.ocr import OcrEngine
    languages = {language.language_tag for language in OcrEngine.available_recognizer_languages}
    print('Installed Windows OCR languages:', ', '.join(sorted(languages)))
    missing = [name for name in ('ru', 'en') if not any(tag.lower().startswith(name) for tag in languages)]
    if missing:
        raise SystemExit('Install Windows OCR language capabilities first: ' + ', '.join(missing) +
                         '\nSettings > Time & language > Language & region > Language options.\n'
                         'Or elevated PowerShell: Add-WindowsCapability -Online -Name Language.OCR~~~ru-RU~0.0.1.0\n'
                         'Add-WindowsCapability -Online -Name Language.OCR~~~en-US~0.0.1.0')
    print('Windows OCR is ready.')


if __name__ == '__main__':
    main()
