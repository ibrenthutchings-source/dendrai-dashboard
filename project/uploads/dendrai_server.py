#!/usr/bin/env python3
"""
Dendrai Local Server
--------------------
Serves dendrai_ra_loop.html and proxies EDGAR + FRED API calls
to bypass browser CORS restrictions.

Usage:
  python dendrai_server.py

Then open: http://localhost:8000/dendrai_ra_loop.html
"""
import sys
import os
import urllib.request
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = 8000

PROXY_ROUTES = {
    '/proxy/edgar/': 'https://data.sec.gov/api/xbrl/',
    '/proxy/sec/':   'https://data.sec.gov/',
    '/proxy/fred/':  'https://api.stlouisfed.org/fred/',
}

class DendraiHandler(SimpleHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Cleaner log output
        path = args[0] if args else ''
        code = args[1] if len(args) > 1 else ''
        if any(p in str(path) for p in ['/proxy/', '.html', '.js', '.css']):
            print(f'  {code}  {path}')

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        # Check proxy routes
        for prefix, target in PROXY_ROUTES.items():
            if self.path.startswith(prefix):
                rest = self.path[len(prefix):]
                self._proxy(target + rest)
                return
        # Default: serve files from current directory
        super().do_GET()

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, x-api-key, anthropic-version, anthropic-dangerous-direct-browser-access')

    def _proxy(self, url):
        # Decode any double-encoding
        url = urllib.parse.unquote(url)
        try:
            req = urllib.request.Request(
                url,
                headers={
                    'User-Agent': 'Dendrai/1.0 research@dendrai.com',
                    'Accept': 'application/json',
                }
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data  = resp.read()
                ctype = resp.headers.get('Content-Type', 'application/json')
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)
            print(f'  200  [PROXY] {url[:80]}')
        except urllib.error.HTTPError as e:
            body = e.read()
            self.send_response(e.code)
            self.send_header('Content-Type', 'application/json')
            self._cors()
            self.end_headers()
            self.wfile.write(body)
            print(f'  {e.code}  [PROXY ERROR] {url[:80]}')
        except Exception as e:
            msg = f'{{"error": "{str(e)}"}}'.encode()
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self._cors()
            self.end_headers()
            self.wfile.write(msg)
            print(f'  500  [PROXY FAIL] {str(e)[:80]}')


if __name__ == '__main__':
    # Change to script directory so HTML file is served correctly
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print()
    print('  ██████╗ ███████╗███╗   ██╗██████╗ ██████╗  █████╗ ██╗')
    print('  ██╔══██╗██╔════╝████╗  ██║██╔══██╗██╔══██╗██╔══██╗██║')
    print('  ██║  ██║█████╗  ██╔██╗ ██║██║  ██║██████╔╝███████║██║')
    print('  ██║  ██║██╔══╝  ██║╚██╗██║██║  ██║██╔══██╗██╔══██║██║')
    print('  ██████╔╝███████╗██║ ╚████║██████╔╝██║  ██║██║  ██║██║')
    print('  ╚═════╝ ╚══════╝╚═╝  ╚═══╝╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝')
    print()
    print(f'  Server running at  http://localhost:{PORT}')
    print(f'  Serving from:      {os.getcwd()}')
    print()
    print('  Proxying:')
    for prefix, target in PROXY_ROUTES.items():
        print(f'    /proxy{prefix[7:]}  ->  {target}')
    print()
    print('  Open:  http://localhost:{}/dendrai_ra_loop.html'.format(PORT))
    print()
    print('  Press Ctrl+C to stop.')
    print()

    try:
        HTTPServer(('', PORT), DendraiHandler).serve_forever()
    except KeyboardInterrupt:
        print('\n  Server stopped.')
        sys.exit(0)
