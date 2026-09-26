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
      className="modal-backdrop"
      onClick={onClose}
    >
      <div className="modal-card" onClick={(event) => event.stopPropagation()}>
        <div className="mb-4 flex items-start justify-between gap-6">
          <div>
            <div className="eyebrow">АУДИТ / СЛАЙД</div>
            <div className="mt-1 text-xl font-semibold">Где это на слайде</div>
            <div className="text-base text-muted-foreground">{caption}</div>
          </div>
          <button type="button" onClick={onClose} className="btn btn-sm shrink-0">
            Закрыть
          </button>
        </div>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={src}
          alt="Слайд с отмеченным местом проблемы"
          className="w-full rounded-xl border border-border"
        />
      </div>
    </div>
  );
}
