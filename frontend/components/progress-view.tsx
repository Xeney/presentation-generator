"use client";

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
  const stage =
    elapsed < 6 ? "Читаем шаблон…"
      : elapsed < 30 ? "Пишем содержание…"
        : elapsed < 45 ? "Верстаем…"
          : "Проверяем качество…";
  const percent = Math.min(95, Math.round((elapsed / 75) * 100));

  return (
    <section className="rounded-2xl border border-border bg-card p-8">
      <h2 className="text-3xl font-bold">{stage}</h2>
      <div
        className="mt-6 h-6 w-full overflow-hidden rounded-full bg-secondary"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className="h-full rounded-full bg-primary transition-all duration-700"
          style={{ width: `${percent}%` }}
        />
      </div>
      <p className="mt-4 text-xl">
        Прошло {elapsed} секунд, обычно 40–90
      </p>
      <button
        type="button"
        onClick={onCancel}
        disabled={cancelling}
        className="mt-6 min-h-[48px] rounded-xl border border-border px-8 text-lg font-semibold hover:bg-secondary disabled:opacity-50"
      >
        {cancelling ? "Останавливаем…" : "Отменить"}
      </button>
    </section>
  );
}
