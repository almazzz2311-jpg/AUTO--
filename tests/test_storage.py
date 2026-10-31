import base64
import json

import pytest

from app.core.storage import Settings, SettingsStore, History, CredentialStore
from app.core.windows import parse_hotkey


def test_settings_round_trip_and_defaults(tmp_path):
    store = SettingsStore(tmp_path)
    assert store.load().auto_send is False
    settings = Settings(theme='dark', save_history=False, temperature=0.7)
    store.save(settings)
    assert store.load() == settings
    assert 'Authorization' not in store.path.read_text()
    assert not store.path.with_suffix('.tmp').exists()


def test_settings_ignore_unknown_fields_and_validate(tmp_path):
    store = SettingsStore(tmp_path)
    store.path.write_text('{"old_unused_field": 1, "theme": "light"}')
    assert store.load().theme == 'light'
    with pytest.raises(ValueError):
        store.save(Settings(temperature=99))
    assert store.load().theme == 'light'


def test_history_persists_searches_and_deletes(tmp_path):
    history = History(tmp_path)
    history.add('Русский вопрос x²', 'Ответ 2')
    history.add('English 100%_test', 'Answer')
    assert len(history.search('Русский')) == 1
    assert len(history.search('%')) == 1
    assert len(history.search("' OR 1=1 --")) == 0
    history.close()
    history = History(tmp_path)
    assert len(history.search()) == 2
    history.delete(history.search('English')[0][0])
    assert len(history.search()) == 1
    history.clear()
    assert history.search() == []
    history.close()


@pytest.mark.parametrize('text, expected', [('Ctrl+Shift+A', (0x4006, 65)), ('Alt+F12', (0x4001, 0x7b)),
                                           ('Ctrl+1', (0x4002, 49))])
def test_parse_hotkeys(text, expected):
    assert parse_hotkey(text) == expected


@pytest.mark.parametrize('text', ['A', 'Ctrl+Ж', 'Ctrl+F25', 'Hyper+A', 'Ctrl+Shift+', 'Ctrl+A,B'])
def test_invalid_hotkey(text):
    with pytest.raises(ValueError):
        parse_hotkey(text)


def test_credential_validation_with_fake_vault():
    class Vault:
        value = ''
        def set_password(self, service, user, value):
            self.value = value
        def get_password(self, service, user):
            return self.value
        def delete_password(self, service, user):
            self.value = ''
    store = object.__new__(CredentialStore)
    store.backend = Vault()
    value = base64.b64encode(b'fake-client:fake-secret').decode()
    store.set('Basic ' + value)
    assert store.get() == value
    with pytest.raises(ValueError):
        store.set('not-a-key')
    assert store.get() == value
    store.delete()
    assert store.get() == ''
