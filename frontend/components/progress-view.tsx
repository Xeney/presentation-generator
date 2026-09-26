"use client";

import { Timer } from "lucide-react";

const STAGES = ["Шаблон", "Содержание", "Вёрстка", "Качество"];

/** Экран ожидания: крупные стадии, таймер и «Отменить». */
export function ProgressView({
  elapsed,
  cancelling,
  onCancel,
}: {
  elapsed: number;
  cancelling: boolean;
  onCancel: () => void;
}) {
  const step =
    elapsed < 6 ? 0
      : elapsed < 30 ? 1
        : elapsed < 45 ? 2
          : 3;
  const stage =
    elapsed < 6 ? "Читаем шаблон…"
      : elapsed < 30 ? "Пишем содержание…"
        : elapsed < 45 ? "Верстаем…"
          : "Проверяем качество…";
  const percent = Math.min(95, Math.round((elapsed / 75) * 100));

  return (
    <section className="panel rise p-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="eyebrow">ГЕНЕРАЦИЯ / В ПРОЦЕССЕ</div>
          <h2 className="mt-2 text-3xl font-bold tracking-tight">{stage}</h2>
        </div>
        <span className="chip chip-acid mono normal-case">{percent}%</span>
      </div>

      <div
        className="progress mt-6"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <i style={{ width: `${percent}%` }} />
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-base text-muted-foreground">
        <div className="flex flex-wrap gap-2">
          {STAGES.map((name, index) => (
            <span
              key={name}
              className={`chip mono normal-case ${
                index < step ? "chip-acid" : index === step ? "chip-blue" : ""
              }`}
            >
              {name}
            </span>
          ))}
        </div>
        <span className="mono flex items-center gap-2">
          <Timer className="h-4 w-4" aria-hidden />
          Прошло {elapsed} секунд, обычно 40–90
        </span>
      </div>

      <button
        type="button"
        onClick={onCancel}
        disabled={cancelling}
        className="btn mt-7"
      >
        {cancelling ? "Останавливаем…" : "Отменить"}
      </button>
    </section>
  );
}
