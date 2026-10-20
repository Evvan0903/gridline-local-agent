"""Start a single loopback-only adapter. Use the root start-web.sh for both services."""
import uvicorn
from .app import create_app

if __name__ == '__main__':
    uvicorn.run(create_app(), host='127.0.0.1', port=3001, access_log=False, log_level='warning')
