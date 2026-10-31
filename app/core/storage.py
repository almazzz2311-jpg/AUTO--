"""Non-secret settings and optional local conversation history."""
import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


def data_directory() -> Path:
    root = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share'))
    path = root / 'AI Screen Assistant'
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Settings:
    scope: str = 'GIGACHAT_API_PERS'
    model: str = 'GigaChat'
    temperature: float = 0.5
    max_tokens: int = 2048
    theme: str = 'system'
    mode: str = 'Подробно'
    ocr_language: str = 'auto'
    auto_send: bool = False
    save_history: bool = True
    background: bool = True
    startup: bool = False
    ca_file: str = ''
    hotkeys: dict = field(default_factory=lambda: {
        'capture': 'Ctrl+Shift+A', 'toggle': 'Ctrl+Shift+Q', 'copy': 'Ctrl+Shift+C'})

    def validate(self):
        if self.scope not in ('GIGACHAT_API_PERS', 'GIGACHAT_API_B2B', 'GIGACHAT_API_CORP'):
            raise ValueError('Недопустимый scope')
        if not 0 <= self.temperature <= 2 or not 1 <= self.max_tokens <= 32768:
            raise ValueError('Проверьте температуру и число токенов')
        if self.theme not in ('system', 'dark', 'light'):
            raise ValueError('Неизвестная тема')
        if self.mode not in ('Кратко', 'Подробно', 'Пошагово', 'Перевод'):
            raise ValueError('Неизвестный режим ответа')
        if set(self.hotkeys) != {'capture', 'toggle', 'copy'}:
            raise ValueError('Неполные настройки горячих клавиш')
        if not self.model.strip():
            raise ValueError('Укажите модель')


class SettingsStore:
    def __init__(self, directory: Path):
        self.path = directory / 'settings.json'

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        data = json.loads(self.path.read_text(encoding='utf-8'))
        result = Settings(**{k: v for k, v in data.items() if k in {f.name for f in fields(Settings)}})
        result.validate()
        return result

    def save(self, settings: Settings):
        settings.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)


class CredentialStore:
    """Force the Windows backend: never silently fall back to a plaintext keyring."""
    def __init__(self):
        if sys.platform != 'win32':
            raise RuntimeError('Хранилище ключей доступно только в Windows')
        from keyring.backends.Windows import WinVaultKeyring
        self.backend = WinVaultKeyring()

    def get(self) -> str:
        return self.backend.get_password('AI Screen Assistant', 'GigaChat') or ''

    def set(self, value: str):
        import base64
        value = value.strip().removeprefix('Basic ').strip()
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError('Введите Authorization Key в формате Base64') from exc
        if b':' not in decoded:
            raise ValueError('Нужен Authorization Key, а не отдельный Client Secret')
        self.backend.set_password('AI Screen Assistant', 'GigaChat', value)

    def delete(self):
        if self.get():
            self.backend.delete_password('AI Screen Assistant', 'GigaChat')


class History:
    def __init__(self, directory: Path):
        self.db = sqlite3.connect(directory / 'history.sqlite3')
        self.db.execute('PRAGMA secure_delete=ON')
        self.db.execute('CREATE TABLE IF NOT EXISTS history '
                        '(id INTEGER PRIMARY KEY, created TEXT NOT NULL DEFAULT '
                        "(strftime('%Y-%m-%dT%H:%M:%SZ','now')), prompt TEXT NOT NULL, answer TEXT NOT NULL)")
        self.db.commit()

    def add(self, prompt: str, answer: str):
        with self.db:
            self.db.execute('INSERT INTO history(prompt,answer) VALUES (?,?)', (prompt, answer))

    def search(self, query=''):
        escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        return self.db.execute("SELECT id,created,prompt,answer FROM history WHERE "
                               "prompt LIKE ? ESCAPE '\\' OR answer LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT 500",
                               (f'%{escaped}%', f'%{escaped}%')).fetchall()

    def delete(self, row_id):
        with self.db:
            self.db.execute('DELETE FROM history WHERE id=?', (row_id,))

    def clear(self):
        with self.db:
            self.db.execute('DELETE FROM history')
        self.db.execute('VACUUM')

    def close(self):
        self.db.close()
