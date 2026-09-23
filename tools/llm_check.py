"""Проверка провайдера LLM: доступность, список моделей, тестовый JSON-ответ.

Запуск:
    python tools/llm_check.py                 # текущий провайдер из .env
    python tools/llm_check.py --json-call     # плюс крошечный запрос к модели
    python tools/llm_check.py --provider ollama

Скрипт никогда не печатает ключ: только признак его наличия и длину.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.planner.llm import OllamaClient, OpenAICompatClient, get_llm_client  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Проверка провайдера LLM")
    parser.add_argument("--provider", choices=["ollama", "openai_compat", "aitunnel"],
                        help="переопределить провайдера из .env")
    parser.add_argument("--vlm", action="store_true",
                        help="проверить провайдера VLM-аудита (VLM_PROVIDER)")
    parser.add_argument("--json-call", action="store_true",
                        help="сделать крошечный запрос и проверить JSON-режим")
    args = parser.parse_args(argv)

    if args.provider:
        import os

        os.environ["LLM_PROVIDER"] = args.provider
        get_settings.cache_clear()

    settings = get_settings()
    if args.vlm:
        from app.planner.llm import get_vlm_client

        client = get_vlm_client()
        if client is None:
            print("VLM_PROVIDER=off — VLM-аудит выключен")
            return 0
    else:
        client = get_llm_client()
    kind = "openai_compat" if isinstance(client, OpenAICompatClient) else "ollama"

    print(f"провайдер:      {settings.active_vlm_provider if args.vlm else settings.active_llm_provider}")
    print(f"режим запроса:  {'VLM' if args.vlm else 'LLM'}")
    if kind == "openai_compat":
        print(f"шлюз:           {client.base_url or '(не задан)'}")
        print(f"имя в отчёте:   {client.provider_name}")
        print(f"ключ:           {'есть' if client.api_key else 'НЕТ'} "
              f"({client.masked_key()}, значение не печатается)")
        print(f"таймаут/повтор: {settings.aitunnel_timeout_sec} c / "
              f"{client.max_retries} (актуально для AITUNNEL)")
        print(f"модель LLM:     {settings.active_llm_model or '(не задана)'}")
        print(f"модель VLM:     {settings.active_vlm_model or '(не задана)'}")
        print(f"эмбеддинги:     {settings.active_embedding_model or '(не заданы)'}")
    else:
        print(f"адрес Ollama:   {client.base_url}")
        print(f"модель LLM:     {settings.active_llm_model}")
        print(f"модель VLM:     {settings.active_vlm_model}")
        print(f"эмбеддинги:     {settings.active_embedding_model}")
    print(f"DEMO_MODE:      {settings.demo_mode}")

    if not client.health():
        print("\nдоступность:    НЕТ — проверьте адрес, ключ и сеть")
        return 1
    print("доступность:    да")

    try:
        models = client.list_models()
    except Exception as exc:  # noqa: BLE001
        print(f"список моделей: ошибка ({exc})")
        return 1
    print(f"моделей:        {len(models)}")
    if kind == "openai_compat":
        wanted = {settings.active_llm_model, settings.active_vlm_model}
        for name in sorted(wanted - set(models)):
            if not name:
                continue
            print(f"  ! модель «{name}» не найдена в списке шлюза")
            similar = [m for m in models if name.split("-")[0] in m][:5]
            if similar:
                print(f"    похожие из списка: {', '.join(similar)}")
        preview = ", ".join(models[:8])
        print(f"первые:         {preview}{' …' if len(models) > 8 else ''}")

    if args.json_call:
        print("\nтестовый запрос…")
        try:
            text = client.generate_text(
                'Ответь строго JSON: {"status": "ok", "provider": "'
                + kind + '"}', system="Ты отвечаешь только валидным JSON.",
                temperature=0.0, json_mode=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  ошибка: {exc}")
            return 1
        data = client._parse_json(text)
        print(f"  ответ: {json.dumps(data, ensure_ascii=False) if data else text[:200]}")
        if not data:
            print("  ! модель не вернула JSON — планировщик уйдёт в офлайн-fallback")
            return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
