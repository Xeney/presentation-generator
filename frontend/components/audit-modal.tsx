"use client";

import * as React from "react";

/** Модальное окно «Показать, где это»: одна картинка слайда с красной рамкой. */
export function AuditModal({
  src,
  caption,
  onClose,
}: {
  src: string | null;
  caption: string;
  onClose: () => void;
}) {
  React.useEffect(() => {
    if (!src) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [src, onClose]);

  if (!src) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Место проблемы на слайде"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6"
      onClick={onClose}
    >
      <div
        className="max-h-full max-w-5xl overflow-auto rounded-xl bg-white p-4 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between gap-6">
          <div>
            <div className="text-xl font-semibold">Где это на слайде</div>
            <div className="text-base text-muted-foreground">{caption}</div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="min-h-[44px] rounded-lg border border-border px-5 text-lg font-medium hover:bg-secondary"
          >
            Закрыть
          </button>
        </div>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={src} alt="Слайд с отмеченным местом проблемы" className="w-full rounded-lg" />
      </div>
    </div>
  );
}
