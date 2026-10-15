"""Cross-feature upgrade and process-replacement acceptance."""
import asyncio
import io
import json
import sqlite3
import unittest

from PIL import Image

from tests import test_acceptance as capture_fixture


class UpgradeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = capture_fixture.RecorderAcceptance.asyncSetUp
    asyncTearDown = capture_fixture.RecorderAcceptance.asyncTearDown
    start_server = capture_fixture.RecorderAcceptance.start_server
    kill_server = capture_fixture.RecorderAcceptance.kill_server
    request = capture_fixture.RecorderAcceptance.request
    wait_terminal = capture_fixture.RecorderAcceptance.wait_terminal
    playable = capture_fixture.RecorderAcceptance.playable

    async def test_branding_auth_automatic_queue_and_webhook_survive_replacement(self):
        # Seed the existing YouTube connection as persistent pre-replacement state.
        await self.kill_server()
        with sqlite3.connect(self.root / 'data' / 'state.sqlite') as db:
            db.execute('INSERT OR REPLACE INTO youtube_connection VALUES(1,?)', (json.dumps({
                'client_id': 'upgrade-fixture-client',
                'client_secret': 'upgrade-fixture-secret',
                'public_url': self.base,
                'channel': {'id': 'UCupgrade-fixture', 'name': 'Upgrade Fixture'},
                'token': {'access_token': 'upgrade-fixture-token', 'refresh_token': 'refresh',
                          'expires_at': 9999999999},
                'reconnect_required': False,
            }),))
        await self.start_server()

        # Shared brand assets and automatic upload defaults are persisted alongside
        # a protected source. The webhook receiver is configured before capture so
        # the finalized recording generates a durable delivery.
        self.required_headers = {
            'Authorization': 'Bearer upgrade-source-token',
            'X-Stream-Access': 'upgrade-source-header',
        }
        await self.request('PUT', '/api/settings', {
            'source_bearer_token': 'upgrade-source-token',
            'source_headers': {'X-Stream-Access': 'upgrade-source-header'},
            'storage_cap_bytes': 100_000_000,
        })
        await self.request('PUT', '/api/v1/branding', {
            'name': 'Upgrade Newsroom',
            'sso_button': {'label': 'Continue to Newsroom'},
        })
        image = io.BytesIO()
        Image.new('RGB', (24, 24), '#374151').save(image, format='PNG')
        async with self.client.put(self.base + '/api/v1/branding/assets/dark_logo',
                                   data=image.getvalue(), headers={'Content-Type': 'image/png'}) as response:
            self.assertEqual(response.status, 200, await response.text())

        self.webhook_fail = True
        _, destination = await self.request('POST', '/api/v1/webhooks', {
            'url': f'http://127.0.0.1:{self.fixture_port}/webhook',
            'events': ['recording.completed'],
            'secret': 'upgrade-webhook-secret-32-bytes',
        })
        _, automation = await self.request('PUT', '/api/v1/youtube/automation', {
            'enabled': True,
            'made_for_kids': False,
            'title_template': '{recording_title} — {source_name}',
            'description_template': 'Captured {start_date} at {start_time}',
            'source_name': 'Upgrade Fixture',
        })
        self.assertTrue(automation['enabled'])

        # Include legacy auth/OIDC state and role-specific keys before replacing
        # the process. The request idempotency key must remain bound to this start.
        await self.request('POST', '/api/auth/oidc/config', {
            'issuer': 'https://id.example.org', 'client_id': 'recorder',
            'client_secret': 'upgrade-oidc-secret', 'public_url': self.base,
            'group_claim': 'groups', 'operator_groups': ['upgrade-operators'],
            'admin_groups': ['upgrade-admins'], 'enabled': True,
        })
        _, operator = await self.request('POST', '/api/auth/keys', {'label': 'upgrade operator'})
        _, administrator = await self.request('POST', '/api/auth/keys', {
            'label': 'upgrade administrator', 'role': 'admin',
        })

        admission = {'title': 'Release Review', 'duration_seconds': 3}
        _, started = await self.request('POST', '/api/v1/recordings', admission,
                                        headers={'Idempotency-Key': 'upgrade-recording-start'})
        self.assertEqual((await self.request('DELETE', '/api/v1/youtube/connection'))[1]['connected'], False)
        ready = await self.wait_terminal(started['id'])
        self.assertEqual(ready['state'], 'ready', ready)
        _, replay = await self.request('POST', '/api/v1/recordings', admission,
                                       headers={'Idempotency-Key': 'upgrade-recording-start'})
        self.assertEqual(replay['id'], started['id'])
        self.assertEqual((await self.request('POST', '/api/v1/recordings',
            {'title': 'Different payload', 'duration_seconds': 3}, check=False,
            headers={'Idempotency-Key': 'upgrade-recording-start'}))[0], 409)
        await self.request('PATCH', f"/api/v1/recordings/{ready['id']}", {'pinned': True})

        # Automatic completion must produce one channel-bound queued job. Since
        # the source channel was disconnected while capture ran, it is paused and
        # protects the playable file instead of attempting a real provider call.
        uploads = {'items': []}
        for _ in range(100):
            _, uploads = await self.request('GET', f"/api/v1/recordings/{ready['id']}/uploads")
            if uploads['items'] and uploads['items'][0]['state'] == 'paused':
                break
            await asyncio.sleep(.1)
        self.assertEqual(len(uploads['items']), 1, uploads)
        job = uploads['items'][0]
        self.assertEqual(job['state'], 'paused', job)
        self.assertEqual(job['channel_id'], 'UCupgrade-fixture')
        self.assertEqual(job['metadata']['privacy_status'], 'unlisted')
        self.assertEqual(job['metadata']['notify_subscribers'], False)
        self.assertEqual((await self.request('DELETE', f"/api/v1/recordings/{ready['id']}", check=False))[0], 409)
        await self.playable(ready)

        # Wait for the first webhook attempt to be recorded as pending, then
        # replace the process with the same data directory and recover delivery.
        before = {'items': []}
        for _ in range(100):
            _, before = await self.request('GET', '/api/v1/webhooks/deliveries')
            if before['items'] and before['items'][0]['attempts']:
                break
            await asyncio.sleep(.1)
        self.assertEqual(len(before['items']), 1, before)
        event_id = before['items'][0]['event_id']
        self.assertEqual(before['items'][0]['status'], 'pending')

        await self.request('POST', '/api/auth/config', {
            'username': 'upgrade-admin', 'password': 'upgrade-fixture-password', 'enabled': True,
        })

        await self.kill_server()
        self.webhook_fail = False
        await self.start_server()

        admin_headers = {'Authorization': 'Bearer ' + administrator['key']}
        operator_headers = {'Authorization': 'Bearer ' + operator['key']}
        self.client.headers.update(admin_headers)
        _, settings = await self.request('GET', '/api/v1/settings', headers=admin_headers)
        self.assertEqual(settings['source_url'], self.source)
        self.assertEqual(settings['storage_cap_bytes'], 100_000_000)
        self.assertNotIn('upgrade-source-token', str(settings))
        self.assertNotIn('upgrade-source-header', str(settings))
        _, branding = await self.request('GET', '/api/v1/branding')
        self.assertEqual(branding['name'], 'Upgrade Newsroom')
        self.assertEqual(branding['sso_button']['label'], 'Continue to Newsroom')
        self.assertTrue(branding['assets']['dark_logo'])
        async with self.client.get(self.base + branding['assets']['dark_logo']) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue((await response.read()).startswith(b'\x89PNG'))

        _, connection = await self.request('GET', '/api/v1/youtube/config', headers=admin_headers)
        self.assertFalse(connection['connected'])
        self.assertEqual(connection['client_id'], 'upgrade-fixture-client')
        self.assertTrue(connection['client_secret_saved'])
        _, automation_after = await self.request('GET', '/api/v1/youtube/automation', headers=admin_headers)
        self.assertTrue(automation_after['enabled'])
        _, after = await self.request('GET', f"/api/v1/recordings/{ready['id']}/uploads", headers=operator_headers)
        self.assertEqual(len(after['items']), 1, after)
        self.assertEqual(after['items'][0]['id'], job['id'])
        self.assertEqual(after['items'][0]['channel_id'], 'UCupgrade-fixture')
        self.assertEqual(after['items'][0]['metadata'], job['metadata'])
        self.assertEqual((await self.request('DELETE', f"/api/v1/recordings/{ready['id']}", check=False,
                                              headers=admin_headers))[0], 409)
        _, saved = await self.request('GET', f"/api/v1/recordings/{ready['id']}", headers=operator_headers)
        self.assertTrue(saved['pinned'])
        await self.playable(saved)

        _, oidc = await self.request('GET', '/api/auth/oidc/config', headers=admin_headers)
        self.assertEqual(oidc['operator_groups'], ['upgrade-operators'])
        self.assertEqual(oidc['admin_groups'], ['upgrade-admins'])
        self.assertTrue(oidc['client_secret_saved'])
        self.assertNotIn('upgrade-oidc-secret', str(oidc))

        deliveries = {'items': []}
        for _ in range(120):
            _, deliveries = await self.request('GET', '/api/v1/webhooks/deliveries', headers=admin_headers)
            if deliveries['items'] and deliveries['items'][0]['status'] == 'delivered':
                break
            await asyncio.sleep(.1)
        self.assertEqual(len(deliveries['items']), 1, deliveries)
        self.assertEqual(deliveries['items'][0]['status'], 'delivered')
        self.assertEqual(deliveries['items'][0]['event_id'], event_id)
        self.assertEqual(len(self.webhook_requests), 2)  # one failed attempt, one recovered retry
        self.assertEqual(self.webhook_requests[0][0]['X-Stream-Recorder-Event-Id'], event_id)
        self.assertEqual(self.webhook_requests[1][0]['X-Stream-Recorder-Event-Id'], event_id)
        self.assertEqual((await self.request('GET', '/api/v1/webhooks', headers=admin_headers))[1]['items'][0]['id'], destination['id'])
