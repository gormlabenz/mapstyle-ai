"""Loopback-only image API test server. Never contacts an external provider."""
import argparse
import base64
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time

parser = argparse.ArgumentParser()
parser.add_argument('--port-file', required=True)
args = parser.parse_args()
lock = threading.Lock()
stats = {'active': 0, 'peak': 0, 'requests': 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.respond(200, stats)

    def respond(self, status, payload):
        body = json.dumps(payload).encode()
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        body = self.rfile.read(length)
        with lock:
            stats['active'] += 1
            stats['requests'] += 1
            stats['peak'] = max(stats['peak'], stats['active'])
        try:
            time.sleep(1.5 if 'slow' in self.path else 0.25)
            if 'fail' in self.path:
                self.respond(429, {'error': {'message': 'Test rate limit; key=dummy-test-key'}})
                return
            if 'invalid' in self.path:
                self.respond(200, {'candidates': [{'finishReason': 'SAFETY'}]})
                return
            is_openai = 'multipart' in self.headers.get('Content-Type', '')
            if is_openai:
                msg = BytesParser(policy=policy.default).parsebytes(
                    ('Content-Type: ' + self.headers['Content-Type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + body)
                images = [p.get_payload(decode=True) for p in msg.iter_parts() if p.get_filename()]
                assert len(images) == 2
                output = base64.b64encode(images[0]).decode()
            else:
                data = json.loads(body)
                parts = data['contents'][0]['parts']
                assert len(parts) == 3
                output = parts[1]['inlineData']['data']
            if 'wrong' in self.path:
                output = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
            if is_openai:
                self.respond(200, {'data': [{'b64_json': output}]})
            else:
                self.respond(200, {'candidates': [{'content': {'parts': [{'inlineData': {'mimeType': 'image/png', 'data': output}}]}, 'finishReason': 'STOP'}]})
        except Exception as error:
            self.respond(400, {'error': {'message': type(error).__name__}})
        finally:
            with lock:
                stats['active'] -= 1


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
Path(args.port_file).write_text(str(server.server_port))
print(f'Loopback image fixture on port {server.server_port}', flush=True)
server.serve_forever()
