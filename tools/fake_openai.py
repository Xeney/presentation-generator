"""Мини-заглушка OpenAI-совместимого сервиса для проверки ключей в интерфейсе.

Поднимает /v1/models, /v1/chat/completions и /v1/embeddings. Нужна, чтобы
проверить кнопку «Проверить подключение» без настоящего внешнего ключа.

    python tools/fake_openai.py 8111
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # тишина в консоли
        pass

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 — контракт BaseHTTPRequestHandler
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            return self._send({"object": "list", "data": [
                {"id": "qwen3.5-9b", "object": "model"},
                {"id": "gpt-4o-mini", "object": "model"},
            ]})
        return self._send({"error": "not found"}, status=404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path.rstrip("/") in ("/v1/chat/completions", "/chat/completions"):
            return self._send({"choices": [
                {"message": {"role": "assistant", "content": '{"status": "ok"}'}}]})
        if self.path.rstrip("/") in ("/v1/embeddings", "/embeddings"):
            return self._send({"data": [{"embedding": [0.1, 0.2, 0.3], "index": 0}]})
        return self._send({"error": "not found"}, status=404)


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8111
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"fake openai on http://127.0.0.1:{port}/v1", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
