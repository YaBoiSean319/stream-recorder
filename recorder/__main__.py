import os
from aiohttp import web
from .server import create_app
web.run_app(create_app(),host='0.0.0.0',port=int(os.environ.get('PORT','8080')))
