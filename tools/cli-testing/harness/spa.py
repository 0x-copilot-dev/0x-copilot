#!/usr/bin/env python3
"""Static server for the demo:
 - /v1/* and /api/* -> empty JSON {} (never HTML), so a stray/unmocked API call
   can't return an HTML page and break the app's JSON parsing.
 - other paths with no file extension -> index.html (SPA fallback).
 - real files -> served; missing assets -> 404.

Usage: python3 spa.py <dist_dir> <port>
"""
import http.server
import os
import socketserver
import sys
import urllib.parse

ROOT = sys.argv[1] if len(sys.argv) > 1 else "."
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 4400
os.chdir(ROOT)


class Handler(http.server.SimpleHTTPRequestHandler):
    def _json_empty(self):
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _is_api(self):
        route = urllib.parse.urlparse(self.path).path
        return route.startswith("/v1/") or route.startswith("/api/")

    def do_POST(self):
        self._json_empty()

    def do_GET(self):
        if self._is_api():
            self._json_empty()
            return
        super().do_GET()

    def send_head(self):
        route = urllib.parse.urlparse(self.path).path
        fs_path = self.translate_path(self.path)
        base = os.path.basename(route)
        if not os.path.exists(fs_path) and "." not in base:
            self.path = "/index.html"
        return super().send_head()

    def log_message(self, *args):
        pass


socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
    httpd.serve_forever()
