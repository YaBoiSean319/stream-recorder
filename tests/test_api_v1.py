"""Versioned HTTP contract, exercised without internal database inspection."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from aiohttp.test_utils import TestClient, TestServer

from recorder.server import create_app
APP_ROOT = Path(create_app.__code__.co_filename).resolve().parent.parent


class VersionedApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        await self.open_client()

    async def open_client(self):
        self.client = TestClient(TestServer(create_app(self.tmp.name)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def test_compatibility_pagination_and_stable_errors(self):
        legacy = await (await self.client.get('/api/status')).json()
        self.assertEqual(await (await self.client.get('/api/v1/status')).json(), legacy)
        self.assertEqual((await self.client.get('/api/v1/health')).status, 200)
        page = await (await self.client.get('/api/v1/recordings?limit=1&offset=0')).json()
        self.assertEqual(page, {'items': [], 'total': 0, 'limit': 1, 'offset': 0, 'next_offset': None})
        self.assertEqual(await (await self.client.get('/api/recordings')).json(), [])
        invalid = await self.client.get('/api/v1/recordings?limit=0')
        self.assertEqual(invalid.status, 400)
        self.assertEqual((await invalid.json())['error']['code'], 'invalid_request')
        absent = await self.client.get('/api/v1/recordings/missing')
        self.assertEqual(absent.status, 404)
        self.assertEqual((await absent.json())['error']['code'], 'not_found')

    async def test_idempotent_admission_race_and_restart(self):
        await self.client.put('/api/v1/settings', json={'source_url': 'http://127.0.0.1:9/live.m3u8'})
        headers = {'Idempotency-Key': 'calendar-event-42'}
        async def start():
            response = await self.client.post('/api/v1/recordings', json={'duration_seconds': 30}, headers=headers)
            return response.status, await response.json()
        first, replay = await asyncio.gather(start(), start())
        self.assertEqual(first[0], 201)
        self.assertEqual(replay[0], 201)
        self.assertEqual(first[1]['id'], replay[1]['id'])
        changed = await self.client.post('/api/v1/recordings', json={'duration_seconds': 20}, headers=headers)
        self.assertEqual(changed.status, 409)
        self.assertEqual((await changed.json())['error']['code'], 'idempotency_conflict')
        await self.client.close()
        await self.open_client()
        repeated = await self.client.post('/api/v1/recordings', json={'duration_seconds': 30}, headers=headers)
        self.assertEqual((await repeated.json())['id'], first[1]['id'])

    async def test_scoped_key_expiry_and_versioned_auth(self):
        response = await self.client.post('/api/v1/auth/keys', json={'label': 'status', 'permissions': ['status:read']})
        self.assertEqual(response.status, 201)
        key = await response.json()
        auth = {'Authorization': 'Bearer ' + key['key']}
        self.assertEqual((await self.client.get('/api/v1/status', headers=auth)).status, 200)
        self.assertEqual((await self.client.get('/api/v1/recordings', headers=auth)).status, 403)
        listed = await (await self.client.get('/api/v1/auth/keys')).json()
        self.assertNotIn(key['key'], str(listed))
        self.assertIsNotNone(listed[0]['last_used_at'])
        expired = await self.client.post('/api/v1/auth/keys', json={'label': 'expired', 'expires_at': 1})
        self.assertEqual(expired.status, 400)

    async def test_title_and_pin_scopes_check_each_field(self):
        await self.client.put('/api/v1/settings', json={'source_url':'http://127.0.0.1:9/live.m3u8'})
        recording = await (await self.client.post('/api/v1/recordings', json={'title':'First'})).json()
        title_key = await (await self.client.post('/api/v1/auth/keys', json={
            'label':'editor','permissions':['recordings:title'],
        })).json()
        pin_key = await (await self.client.post('/api/v1/auth/keys', json={
            'label':'pin','permissions':['recordings:pin'],
        })).json()
        path = '/api/v1/recordings/' + recording['id']
        editor = {'Authorization':'Bearer '+title_key['key']}
        pin = {'Authorization':'Bearer '+pin_key['key']}
        self.assertEqual((await self.client.patch(path, json={'title':'Second'}, headers=editor)).status, 200)
        self.assertEqual((await self.client.patch(path, json={'pinned':True}, headers=editor)).status, 403)
        self.assertEqual((await self.client.patch(path, json={'pinned':True}, headers=pin)).status, 200)
        self.assertEqual((await self.client.patch(path, json={'title':'Third'}, headers=pin)).status, 403)
        self.assertEqual((await self.client.patch(path, json={'title':'Third','pinned':False}, headers=editor)).status, 403)

    async def test_protected_preview_and_download_permissions(self):
        await self.client.post('/api/auth/config', json={
            'username': 'admin', 'password': 'correct horse battery staple', 'enabled': True,
        })
        self.assertEqual((await self.client.get('/api/v1/preview')).status, 401)
        login = await self.client.post('/api/v1/auth/login', json={
            'username': 'admin', 'password': 'correct horse battery staple',
        })
        self.assertEqual(login.status, 200)
        self.assertEqual((await self.client.get('/api/v1/preview')).status, 200)

    async def test_key_requirement_preserves_browser_recovery(self):
        response = await self.client.post('/api/v1/auth/config', json={'require_api_key': True})
        self.assertEqual(response.status, 400)
        key = await (await self.client.post('/api/v1/auth/keys', json={'label':'machine'})).json()
        await self.client.post('/api/v1/auth/config', json={
            'username':'admin', 'password':'correct horse battery staple', 'enabled':True,
        })
        login = await self.client.post('/api/v1/auth/login', json={
            'username':'admin', 'password':'correct horse battery staple',
        })
        csrf = (await login.json())['csrf_token']
        response = await self.client.post('/api/v1/auth/config', json={'require_api_key':True}, headers={'X-CSRF-Token':csrf})
        self.assertEqual(response.status, 200)
        self.assertEqual((await self.client.get('/api/v1/status')).status, 200)
        await self.client.post('/api/v1/auth/logout', headers={'X-CSRF-Token':csrf})
        self.assertEqual((await self.client.get('/api/v1/status')).status, 401)
        self.assertEqual((await self.client.get('/api/v1/status', headers={'Authorization':'Bearer '+key['key']})).status, 200)

    async def test_checked_contract_and_docs_under_access(self):
        response = await self.client.get('/api/v1/openapi.json')
        self.assertEqual(response.status, 200)
        document = await response.json()
        saved = json.loads((APP_ROOT/'docs'/'openapi.json').read_text())
        self.assertEqual(document, saved)
        for path, verb in [('/api/v1/status','get'),('/api/v1/recordings','post'),
                           ('/api/v1/recordings/{id}/download','get'),('/api/v1/auth/keys','post'),
                           ('/api/v1/viewers/export.csv','get'),('/api/v1/viewers/configurations','get'),
                           ('/api/v1/viewers/diagnostics','get'),('/api/v1/viewers/key','delete')]:
            self.assertIn(verb, document['paths'][path])
            self.assertIn('x-permission', document['paths'][path][verb])
        self.assertEqual(document['paths']['/api/v1/viewers/export.csv']['get']['x-permission'],'viewers:export')
        self.assertIn('ViewerHistory',document['components']['schemas'])
        self.assertIn('ViewerConfigurationUpdate',document['components']['schemas'])
        self.assertEqual((await self.client.get('/api/v1/docs')).status, 200)
        self.assertEqual((await self.client.get('/api/v1/openapi.json')).status, 200)
        js = await (await self.client.get('/static/api-docs.js')).text()
        html = await (await self.client.get('/api/v1/docs')).text()
        self.assertIn('window.confirm', js)
        self.assertIn('credentials:\'same-origin\'', js)
        self.assertIn('Choose a PNG, JPEG, or WebP image file.', js)
        self.assertIn('never saved', html)
        self.assertIn('type=\"password\"', html)
        self.assertIn('Google consent still requires', html)
        api_guide = (APP_ROOT/'docs'/'api.md').read_text()
        for term in ['Power Automate', 'n8n', 'browser-local', 'uncertain', 'Idempotency-Key', 'viewers:export']:
            self.assertIn(term.lower(), api_guide.lower())
        self.assertIn('viewer-analytics.csv',(APP_ROOT/'docs'/'viewer-analytics.md').read_text())
        self.assertIn('YouTube Data API v3', (APP_ROOT/'docs'/'youtube.md').read_text())
        self.assertIn('HMAC-SHA256', (APP_ROOT/'docs'/'webhooks.md').read_text())
        await self.client.post('/api/v1/auth/config', json={
            'username':'admin', 'password':'correct horse battery staple', 'enabled':True,
        })
        self.assertEqual((await self.client.get('/api/v1/docs')).status, 401)
        self.assertEqual((await self.client.get('/api/v1/openapi.json')).status, 401)
        login = await self.client.post('/api/v1/auth/login', json={
            'username':'admin', 'password':'correct horse battery staple',
        })
        csrf = (await login.json())['csrf_token']
        self.assertEqual((await self.client.get('/api/v1/docs')).status, 200)
        denied = await self.client.put('/api/v1/youtube/automation', json={'enabled':False})
        self.assertEqual(denied.status, 403)
        accepted = await self.client.put('/api/v1/youtube/automation', json={'enabled':False}, headers={'X-CSRF-Token':csrf})
        self.assertEqual(accepted.status, 200)


    async def test_gui_operation_inventory_is_documented_and_contract_has_permissions(self):
        inventory = {
            '/api/v1/health': {'get'}, '/api/v1/status': {'get'}, '/api/v1/preview': {'get'},
            '/api/v1/stop': {'post'}, '/api/v1/docs': {'get'}, '/api/v1/openapi.json': {'get'},
            '/api/v1/settings': {'get', 'put'}, '/api/v1/source': {'get', 'put'},
            '/api/v1/storage': {'get', 'put'}, '/api/v1/recordings': {'get', 'post'},
            '/api/v1/recordings/{id}': {'get', 'patch', 'delete'},
            '/api/v1/recordings/{id}/stop': {'post'}, '/api/v1/recordings/{id}/download': {'get'},
            '/api/v1/branding': {'get', 'put'}, '/api/v1/branding/reset': {'post'},
            '/api/v1/branding/assets/{slot}': {'get', 'put', 'delete'},
            '/api/v1/auth/status': {'get'}, '/api/v1/auth/config': {'get', 'post'},
            '/api/v1/auth/login': {'post'}, '/api/v1/auth/logout': {'post'},
            '/api/v1/auth/permissions': {'get'}, '/api/v1/auth/keys': {'get', 'post'},
            '/api/v1/auth/keys/{id}': {'delete'}, '/api/v1/auth/oidc/config': {'get', 'post'},
            '/api/v1/auth/oidc/login': {'get'}, '/api/v1/auth/oidc/callback': {'get'},
            '/api/v1/youtube/config': {'get', 'put'}, '/api/v1/youtube/status': {'get'},
            '/api/v1/youtube/connect': {'post'}, '/api/v1/youtube/callback': {'get'},
            '/api/v1/youtube/refresh': {'post'}, '/api/v1/youtube/connection': {'delete'},
            '/api/v1/youtube/automation': {'get', 'put'}, '/api/v1/uploads': {'get'},
            '/api/v1/uploads/channel': {'get'}, '/api/v1/uploads/{job_id}': {'get'},
            '/api/v1/recordings/{recording_id}/uploads': {'get', 'post'},
            '/api/v1/uploads/{job_id}/retry': {'post'}, '/api/v1/uploads/{job_id}/cancel': {'post'},
            '/api/v1/uploads/{job_id}/requeue': {'post'}, '/api/v1/uploads/{job_id}/release': {'post'},
            '/api/v1/webhooks': {'get', 'post'}, '/api/v1/webhooks/{destination_id}': {'get', 'put', 'delete'},
            '/api/v1/webhooks/deliveries': {'get'},
        }
        root = APP_ROOT
        document = json.loads((root/'docs'/'openapi.json').read_text())
        for path, methods in inventory.items():
            for method in methods:
                with self.subTest(path=path, method=method):
                    operation = document['paths'][path][method]
                    self.assertTrue(operation.get('summary'))
                    self.assertTrue(operation.get('responses'))
                    self.assertTrue(operation.get('x-permission'), f'{method.upper()} {path}')
                    if method in {'post', 'put', 'patch', 'delete'} and operation.get('x-permission') != 'public':
                        self.assertIn({'$ref':'#/components/parameters/CsrfToken'}, operation.get('parameters', []), f'{method.upper()} {path} must describe session CSRF')
        self.assertEqual(document['paths']['/api/v1/branding']['get']['security'], [])
        self.assertIn('Idempotency-Key', str(document['paths']['/api/v1/recordings']['post']['parameters']))
        self.assertIn('Idempotency-Key', str(document['paths']['/api/v1/recordings/{recording_id}/uploads']['post']['parameters']))
        self.assertEqual({p['name'] for p in document['paths']['/api/v1/recordings']['get']['parameters']}, {'limit','offset'})
        parity = (root/'docs'/'gui-api-parity.md').read_text().lower()
        for area in ['recording', 'branding', 'authentication', 'youtube', 'automatic-upload', 'webhook']:
            self.assertIn(area, parity)
        for route in self.client.server.app.router.routes():
            actual_path = route.resource.canonical
            if not actual_path.startswith('/api/v1/') or route.method == 'HEAD':
                continue
            actual_parts = actual_path.split('/')
            covered = False
            for contract_path, verbs in document['paths'].items():
                contract_parts = contract_path.split('/')
                if route.method.lower() not in verbs or len(actual_parts) != len(contract_parts):
                    continue
                if all(a == b or (a.startswith('{') and a.endswith('}')) for a, b in zip(actual_parts, contract_parts)):
                    covered = True
                    break
            self.assertTrue(covered, f'{route.method} {actual_path} missing from OpenAPI')

    async def test_representative_gui_reads_match_the_documented_response_shapes(self):
        calls = [
            ('/api/v1/branding', 'assets'),
            ('/api/v1/youtube/config', 'client_secret_saved'),
            ('/api/v1/youtube/automation', 'title_template'),
            ('/api/v1/uploads', 'items'),
            ('/api/v1/uploads/channel', 'connected'),
            ('/api/v1/webhooks', 'items'),
            ('/api/v1/webhooks/deliveries', 'items'),
        ]
        for path, property_name in calls:
            response = await self.client.get(path)
            self.assertEqual(response.status, 200, path)
            payload = await response.json()
            self.assertIn(property_name, payload, path)


if __name__ == '__main__':
    unittest.main()
