"""GigaChat OAuth and SSE. Credentials and response bodies never enter logs."""
import asyncio
import json
import ssl
import time
import uuid
from collections.abc import AsyncIterator, Callable

import httpx
import truststore

AUTH_URL = 'https://ngw.devices.sberbank.ru:9443/api/v2/oauth'
API_URL = 'https://api.giga.chat/v1'


class APIError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def tls_context(ca_file: str = '') -> ssl.SSLContext:
    # Windows native trust (including installed Russian root CAs), never verify=False.
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if ca_file:
        context.load_verify_locations(cafile=ca_file)
    return context


def status_error(code: int) -> APIError:
    messages = {
        400: 'API отклонил запрос. Проверьте модель, длину диалога и параметры.',
        401: 'Ошибка авторизации. Проверьте Authorization Key и scope.',
        403: 'Нет доступа к API или модели. Проверьте тариф и разрешения.',
        413: 'Запрос слишком большой. Начните новый диалог.',
        422: 'API не принимает выбранные параметры запроса.',
        429: 'Лимит запросов исчерпан. Повторите позже.',
    }
    return APIError(messages.get(code, f'GigaChat недоступен (HTTP {code}).'), code)


class GigaChat:
    def __init__(self, credentials: Callable[[], str], scope='GIGACHAT_API_PERS', ca_file='', transport=None):
        self.credentials = credentials
        self.scope = scope
        self.http = httpx.AsyncClient(verify=tls_context(ca_file), transport=transport,
                                     timeout=httpx.Timeout(60, connect=15), follow_redirects=False)
        self.token = ''
        self.expires_at = 0.0
        self.lock = asyncio.Lock()

    async def close(self):
        await self.http.aclose()

    async def _token(self):
        async with self.lock:
            if self.token and time.time() < self.expires_at - 60:
                return self.token
            key = self.credentials().strip().removeprefix('Basic ').strip()
            if not key:
                raise APIError('Добавьте Authorization Key в настройках GigaChat.')
            response = await self._request('POST', AUTH_URL, headers={
                'Authorization': f'Basic {key}', 'RqUID': str(uuid.uuid4()),
                'Accept': 'application/json'}, data={'scope': self.scope})
            try:
                data = response.json()
                token = data['access_token']
                expires = float(data['expires_at']) / 1000
                if not isinstance(token, str) or not token or expires <= time.time():
                    raise ValueError('Invalid token')
                self.token, self.expires_at = token, expires
            except (ValueError, KeyError, TypeError) as exc:
                raise APIError('Некорректный ответ сервиса авторизации.') from exc
            return self.token

    async def _request(self, method, url, **kwargs):
        for attempt in range(3):
            try:
                response = await self.http.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                if attempt == 2:
                    raise APIError('Ошибка сети или TLS. Проверьте интернет и доверенные сертификаты.') from exc
                await asyncio.sleep(0.5 * 2**attempt)
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                await asyncio.sleep(self._delay(response, attempt))
                continue
            if response.is_error or response.is_redirect:
                raise status_error(response.status_code)
            return response
        raise APIError('Сервис не ответил.')

    @staticmethod
    def _delay(response, attempt):
        try:
            return max(0, min(float(response.headers.get('Retry-After', 0.5 * 2**attempt)), 10))
        except ValueError:
            return 0.5 * 2**attempt

    async def authorized_request(self, method, path, **kwargs):
        for attempt in range(2):
            token = await self._token()
            try:
                return await self._request(method, API_URL + path,
                    headers={'Authorization': f'Bearer {token}'}, **kwargs)
            except APIError as exc:
                if exc.status == 401 and attempt == 0:
                    self.token = ''
                    continue
                raise
        raise status_error(401)

    async def models(self) -> list[str]:
        response = await self.authorized_request('GET', '/models')
        try:
            models = [model['id'] for model in response.json()['data']]
            if not models or not all(isinstance(name, str) and name for name in models):
                raise ValueError('No available models')
            return models
        except (ValueError, TypeError, KeyError) as exc:
            raise APIError('Сервис не вернул доступных моделей. Проверьте тариф.') from exc

    async def upload_image(self, png: bytes) -> str:
        response = await self.authorized_request('POST', '/files', data={'purpose': 'general'},
            files={'file': ('screen.png', png, 'image/png')})
        try:
            file_id = response.json()['id']
            uuid.UUID(file_id)
            return file_id
        except (ValueError, KeyError, TypeError) as exc:
            raise APIError('Некорректный ответ загрузки изображения.') from exc

    async def delete_image(self, file_id: str):
        # Official SDK uses POST /files/{id}/delete with multipart file field.
        uuid.UUID(file_id)
        await self.authorized_request('POST', f'/files/{file_id}/delete', files={'file': (None, file_id)})

    async def stream(self, messages: list[dict], model: str, temperature: float, max_tokens: int) -> AsyncIterator[str]:
        payload = dict(model=model, messages=messages, temperature=temperature,
                       max_tokens=max_tokens, stream=True)
        refreshed = False
        for attempt in range(3):
            emitted = False
            token = await self._token()
            try:
                async with self.http.stream('POST', API_URL + '/chat/completions',
                                            json=payload, headers={'Authorization': f'Bearer {token}',
                                                                  'Accept': 'text/event-stream'}) as response:
                    if response.status_code == 401 and not refreshed:
                        self.token = ''
                        refreshed = True
                        continue
                    if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                        await asyncio.sleep(self._delay(response, attempt))
                        continue
                    if response.is_error or response.is_redirect:
                        raise status_error(response.status_code)
                    finished = False
                    async for line in response.aiter_lines():
                        if not line.startswith('data:'):
                            continue
                        text = line[5:].strip()
                        if text == '[DONE]':
                            finished = True
                            break
                        try:
                            event = json.loads(text)
                            choice = event['choices'][0]
                            delta = choice.get('delta', {}).get('content', '')
                            if delta:
                                if not isinstance(delta, str):
                                    raise ValueError('Invalid delta')
                                emitted = True
                                yield delta
                            reason = choice.get('finish_reason')
                            if reason:
                                finished = True
                                if reason == 'length':
                                    yield '\n\n*Ответ достиг ограничения токенов. Задайте уточняющий вопрос.*'
                        except (ValueError, KeyError, TypeError, IndexError) as exc:
                            raise APIError('Некорректный поток ответа GigaChat.') from exc
                    if not finished:
                        raise APIError('Поток прерван. Неполный ответ не добавлен в диалог; повторите запрос.')
                    if not emitted:
                        raise APIError('Модель вернула пустой ответ.')
                    return
            except httpx.TransportError as exc:
                if emitted or attempt == 2:
                    raise APIError('Соединение прервано. Проверьте интернет и сертификаты; повторите запрос.') from exc
                await asyncio.sleep(0.5 * 2**attempt)
        raise APIError('Не удалось получить ответ после повторных попыток.')


def system_prompt(mode: str) -> str:
    modes = {'Кратко': 'Ответь кратко.', 'Подробно': 'Объясни подробно и понятно.',
             'Пошагово': 'Реши задачу пошагово, приведи формулы и итог.',
             'Перевод': 'Переведи текст на русский, сохраняя структуру.'}
    return ('Ты — помощник по тексту с экрана. Определи тип задачи: математика, код, вопрос или текст. '
            'Отвечай по-русски, используй Markdown. Не выдавай догадки за факты. '
            'В распознанном тексте возможны ошибки, особенно в формулах; '
            'если условие неоднозначно или неполно, попроси уточнить. ' + modes[mode])
