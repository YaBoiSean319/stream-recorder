"""Opt-in upload configuration and admission-time metadata snapshots."""
import datetime as dt
import json
import string
from aiohttp import web

FIELDS = {'recording_title', 'source_name', 'start_date', 'start_time'}
DEFAULTS = {'enabled': False, 'source_name': '', 'title_template': '{recording_title}',
            'description_template': '', 'privacy_status': 'unlisted', 'tags': [],
            'category_id': '22', 'made_for_kids': None, 'notify_subscribers': False}
META = {'title', 'description', 'privacy_status', 'tags', 'category_id', 'made_for_kids', 'notify_subscribers'}


def render(template, values):
    if not isinstance(template, str):
        raise ValueError('Template must be text')
    try:
        for _, field, format_spec, conversion in string.Formatter().parse(template):
            if field is not None and (field not in FIELDS or format_spec or conversion):
                raise ValueError('Unknown or unsupported template field')
        return template.format_map(values)
    except (ValueError, KeyError, IndexError) as exc:
        raise ValueError('Invalid upload template: use only {recording_title}, {source_name}, {start_date}, {start_time}; double braces escape braces') from exc


class AutomaticUpload:
    def __init__(self, service, youtube):
        self.service, self.youtube = service, youtube
        service.auto_upload = self

    def config(self):
        return {**DEFAULTS, **self.service.settings.get('automatic_upload', {})}

    def validate(self, data):
        if not isinstance(data, dict) or set(data) - DEFAULTS.keys():
            raise ValueError('Invalid automatic upload settings')
        merged = {**self.config(), **data}
        if not isinstance(merged['enabled'], bool) or not isinstance(merged['source_name'], str) or len(merged['source_name']) > 100:
            raise ValueError('Invalid automatic upload enablement or source name')
        for field, limit in [('title_template', 500), ('description_template', 5000)]:
            value = merged[field]
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError('Upload template is too long')
            render(value, {key: 'Sample' for key in FIELDS})
        if not merged['title_template'].strip():
            raise ValueError('A title template is required')
        if merged['enabled']:
            if merged['made_for_kids'] is None:
                raise ValueError('Choose whether videos are made for kids before enabling automatic uploads')
            channel = self.youtube.data
            if not channel.get('channel') or not channel.get('token') or channel.get('reconnect_required'):
                raise ValueError('Connect a YouTube channel before enabling automatic uploads')
        sample = {'title': 'Sample', 'description': 'Sample', **{key: merged[key] for key in META if key not in {'title', 'description'}}}
        if sample['made_for_kids'] is None:
            sample['made_for_kids'] = False
        self.service.uploads.metadata(sample, {'filename': 'sample.mp4'})
        return merged

    def prepare(self, record, overrides=None, enabled=None):
        if overrides is None:
            overrides = {}
        if not isinstance(overrides, dict) or set(overrides) - META:
            raise ValueError('Invalid automatic upload metadata overrides')
        if enabled is not None and not isinstance(enabled, bool):
            raise ValueError('auto_upload must be a Boolean')
        config = self.config()
        chosen = config['enabled'] if enabled is None else enabled
        if chosen and (not config['enabled'] or config['made_for_kids'] is None):
            raise ValueError('Enable automatic uploads and choose audience in Settings first')
        if not chosen and overrides:
            raise ValueError('Upload overrides require automatic upload enabled for this recording')
        if not chosen:
            record['automatic_upload'] = None
            return
        channel = self.youtube.data.get('channel')
        if not channel or not self.youtube.data.get('token') or self.youtube.data.get('reconnect_required'):
            raise ValueError('Connect a YouTube channel before starting an automatic recording')
        start = dt.datetime.fromisoformat(record['started_at']).astimezone(dt.timezone.utc)
        values = {'recording_title': record.get('title') or record['filename'].removesuffix('.mp4'),
                  'source_name': config['source_name'].strip() or 'Stream',
                  'start_date': start.strftime('%Y-%m-%d'), 'start_time': start.strftime('%H:%M UTC')}
        metadata = {'title': render(config['title_template'], values),
                    'description': render(config['description_template'], values),
                    **{key: config[key] for key in META if key not in {'title', 'description'}}, **overrides}
        record['automatic_upload'] = {'metadata': self.service.uploads.metadata(metadata, record),
                                      'channel_id': channel['id'], 'channel_name': channel['name']}

    async def route(self, request):
        if request.method == 'GET':
            return web.json_response(self.config())
        try:
            body = await request.json()
            config = self.validate(body)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise web.HTTPBadRequest(text=str(exc)) from None
        async with self.service.gate:
            self.service.settings['automatic_upload'] = config
            with self.service.db:
                self.service.db.execute('INSERT OR REPLACE INTO settings VALUES(1,?)', (json.dumps(self.service.settings),))
        return web.json_response(config)


def install(app, service, youtube):
    automation = AutomaticUpload(service, youtube)
    app.router.add_get('/api/v1/youtube/automation', automation.route)
    app.router.add_put('/api/v1/youtube/automation', automation.route)
    return automation
