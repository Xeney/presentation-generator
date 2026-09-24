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
import time
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
                        help="сделать крошечный запрос и проверить JSON-режим; "
                             "с --vlm отправляет реальную картинку слайда")
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

    if args.json_call and args.vlm:
        return _vlm_smoke(client, settings)
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


def _fake_slide_png() -> str:
    """Простая картинка слайда (заголовок-вывод + три тезиса) для VLM-проверки."""
    import base64
    import io

    from PIL import Image, ImageDraw

    image = Image.new("RGB", (960, 540), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 960, 96], fill="#1B4DFF")
    draw.text((40, 34), "Отчёты готовятся втрое быстрее", fill="white")
    for index, line in enumerate((
            "Время подготовки отчёта: 8 ч → 3 ч",
            "Автоматизировано 12 рутинных задач",
            "Охват вырос до 5 подразделений")):
        draw.text((60, 180 + index * 70), f"• {line}", fill="#1A1A1A")
    draw.text((60, 470), "Источник: бриф проекта", fill="#666666")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _vlm_smoke(client, settings) -> int:
    """Реальный VLM-запрос: картинка слайда + 11 вопросов ТЗ."""
    from app.audit.vlm import CRITERIA, PROMPTS

    print("\nVLM-запрос с реальной картинкой слайда…")
    system = (PROMPTS / "system.md").read_text(encoding="utf-8")
    prompt = ("Слайд 1 из 1. Язык колоды: ru. Предыдущий слайд: нет (первый слайд). "
              "Следующий слайд: нет (последний слайд).\n"
              "Оцени слайд по 11 критериям и верни JSON.")
    started = time.perf_counter()
    try:
        text = client.generate_text(prompt, system, model=settings.active_vlm_model,
                                    temperature=0.0, json_mode=True,
                                    images=[_fake_slide_png()])
    except Exception as exc:  # noqa: BLE001
        print(f"  ошибка: {exc}")
        return 1
    elapsed = time.perf_counter() - started
    data = client._parse_json(text)
    if not data:
        print(f"  ! модель не вернула JSON ({elapsed:.1f} c): {text[:200]}")
        return 1
    yes = [int(x) for x in data.get("answers_yes", []) if str(x).isdigit()]
    no = [int(x) for x in data.get("answers_no", []) if str(x).isdigit()]
    print(f"  модель: {settings.vlm_label}, время {elapsed:.1f} c")
    print(f"  ответов «да»: {len(yes)} из {len(CRITERIA)}, «нет»: {len(no)}")
    for number in no:
        print(f"    критерий {number}: {CRITERIA.get(number, '?')}")
    if data.get("summary"):
        print(f"  комментарий модели: {data['summary'][:200]}")
    if not yes and not no:
        print("  ! модель не отметила ни одного критерия")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
