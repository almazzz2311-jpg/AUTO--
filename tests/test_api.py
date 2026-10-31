import asyncio
import json
import time
import uuid

import httpx
import pytest

from app.core.gigachat import GigaChat, APIError, AUTH_URL, API_URL


def token_response():
    return httpx.Response(200, json={'access_token': 'test-token', 'expires_at': (time.time() + 1800) * 1000})


def sse(content='Ответ', reason='stop'):
    return httpx.Response(200, text='data: ' + json.dumps({'choices': [
        {'delta': {'content': content}, 'finish_reason': None}]}) + '\n\ndata: ' + json.dumps({
        'choices': [{'delta': {}, 'finish_reason': reason}]}) + '\n\ndata: [DONE]\n\n',
        headers={'content-type': 'text/event-stream'})


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    async def instant(_):
        return
    monkeypatch.setattr('app.core.gigachat.asyncio.sleep', instant)


async def collect(client):
    return ''.join([part async for part in client.stream([{'role': 'user', 'content': 'Вопрос'}], 'GigaChat', 0.5, 200)])


async def test_auth_payload_uuid_token_cached_and_stream():
    requests = []
    def handler(request):
        requests.append(request)
        if str(request.url) == AUTH_URL:
            assert request.headers['Authorization'] == 'Basic test-key'
            assert uuid.UUID(request.headers['RqUID']).version == 4
            assert request.content == b'scope=GIGACHAT_API_PERS'
            return token_response()
        if request.url.path.endswith('/models'):
            assert request.headers['Authorization'] == 'Bearer test-token'
            return httpx.Response(200, json={'data': [{'id': 'GigaChat'}]})
        body = json.loads(request.content)
        assert body['stream'] is True and body['max_tokens'] == 200
        return sse()
    client = GigaChat(lambda: 'test-key', transport=httpx.MockTransport(handler))
    try:
        assert await client.models() == ['GigaChat']
        assert await collect(client) == 'Ответ'
        assert len([r for r in requests if str(r.url) == AUTH_URL]) == 1
    finally:
        await client.close()


async def test_concurrent_authorization_uses_one_token():
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        return token_response()
    client = GigaChat(lambda: 'test-key', transport=httpx.MockTransport(handler))
    try:
        assert await asyncio.gather(*[client._token() for _ in range(8)]) == ['test-token'] * 8
        assert calls == 1
    finally:
        await client.close()


@pytest.mark.parametrize('status', [401, 403, 400])
async def test_auth_error_never_exposes_response_or_key(status):
    client = GigaChat(lambda: 'private-key', transport=httpx.MockTransport(
        lambda r: httpx.Response(status, text='private-key secret server body')))
    try:
        with pytest.raises(APIError) as error:
            await collect(client)
        assert 'private-key' not in str(error.value)
        assert error.value.status == status
    finally:
        await client.close()


async def test_expired_token_refreshed():
    client = GigaChat(lambda: 'key', transport=httpx.MockTransport(lambda r: token_response()))
    client.token, client.expires_at = 'expired', time.time() - 1
    try:
        assert await client._token() == 'test-token'
    finally:
        await client.close()


async def test_chat_401_refreshes_once():
    calls = {'auth': 0, 'chat': 0}
    def handler(request):
        name = 'auth' if str(request.url) == AUTH_URL else 'chat'
        calls[name] += 1
        if name == 'auth':
            return token_response()
        return httpx.Response(401) if calls[name] == 1 else sse()
    client = GigaChat(lambda: 'key', transport=httpx.MockTransport(handler))
    try:
        assert await collect(client) == 'Ответ'
        assert calls == {'auth': 2, 'chat': 2}
    finally:
        await client.close()


@pytest.mark.parametrize('status', [429, 500, 503])
async def test_transient_status_retry(status):
    calls = 0
    def handler(request):
        nonlocal calls
        if str(request.url) == AUTH_URL:
            return token_response()
        calls += 1
        return httpx.Response(status, headers={'Retry-After': '0'}) if calls < 3 else sse()
    client = GigaChat(lambda: 'key', transport=httpx.MockTransport(handler))
    try:
        assert await collect(client) == 'Ответ'
        assert calls == 3
    finally:
        await client.close()


async def test_network_failure_is_bounded():
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError('private detail')
    client = GigaChat(lambda: 'key', transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(APIError, match='сети или TLS'):
            await collect(client)
        assert calls == 3
    finally:
        await client.close()


@pytest.mark.parametrize('body', ['data: {invalid}\n\n', 'data: {"choices":[]}\n\n',
                                  'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'])
async def test_invalid_or_truncated_stream_fails(body):
    client = GigaChat(lambda: 'key', transport=httpx.MockTransport(lambda r:
        token_response() if str(r.url) == AUTH_URL else httpx.Response(200, text=body)))
    try:
        with pytest.raises(APIError):
            await collect(client)
    finally:
        await client.close()


async def test_empty_key_does_not_send_request():
    def handler(request):
        pytest.fail('No HTTP request should be sent')
    client = GigaChat(lambda: '', transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(APIError, match='Authorization Key'):
            await collect(client)
    finally:
        await client.close()


async def test_image_upload_and_delete():
    file_id = str(uuid.uuid4())
    def handler(request):
        if str(request.url) == AUTH_URL:
            return token_response()
        if request.url.path.endswith('/delete'):
            assert file_id.encode() in request.content
            return httpx.Response(200, json={'deleted': True})
        assert b'image/png' in request.content and b'general' in request.content
        return httpx.Response(200, json={'id': file_id})
    client = GigaChat(lambda: 'key', transport=httpx.MockTransport(handler))
    try:
        assert await client.upload_image(b'png-bytes') == file_id
        await client.delete_image(file_id)
    finally:
        await client.close()


async def test_missing_completion_warns_instead_of_success():
    response = httpx.Response(200, text='data: [DONE]\n\n')
    client = GigaChat(lambda: 'key', transport=httpx.MockTransport(lambda r:
        token_response() if str(r.url) == AUTH_URL else response))
    try:
        with pytest.raises(APIError, match='пустой'):
            await collect(client)
    finally:
        await client.close()
