"use client";

import * as React from "react";

import { ProviderSetup } from "@/components/provider-setup";
import { Input, Label, Select } from "@/components/ui/field";
import { api } from "@/lib/api";
import type { Health, ProviderStatus, VariantName } from "@/lib/types";

const VARIANTS: { id: VariantName; name: string }[] = [
  { id: "compact", name: "Компактный" },
  { id: "cards", name: "Карточками" },
  { id: "split", name: "С данными отдельно" },
];

/** Блок «Для опытных»: провайдер/ключ, VLM, число слайдов, язык, галерея. */
export function AdvancedSettings({
  health,
  vlm,
  onVlmChange,
  slides,
  onSlidesChange,
  language,
  onLanguageChange,
  renderMode,
  onRenderMode,
  provider,
  onProviderChange,
  jobId,
  version,
}: {
  health: Health | null;
  vlm: boolean;
  onVlmChange: (value: boolean) => void;
  slides: number;
  onSlidesChange: (value: number) => void;
  language: string;
  onLanguageChange: (value: string) => void;
  renderMode: "native" | "html" | "both";
  onRenderMode: (value: "native" | "html" | "both") => void;
  provider: ProviderStatus | null;
  onProviderChange: (provider: ProviderStatus | null, label?: string) => void;
  jobId: string | null;
  version: number;
}) {
  return (
    <section className="panel p-5">
      <div className="flex flex-wrap items-center gap-3">
        <span className="eyebrow">ДЛЯ ОПЫТНЫХ / НАСТРОЙКИ</span>
        <span className="text-lg font-semibold">Для опытных</span>
        <span className="chip ml-auto">Источник, модель, язык</span>
      </div>

      <div className="mt-6 space-y-6">
        <div>
          <div className="eyebrow">ИСТОЧНИК НЕЙРОСЕТИ</div>
          <div className="mt-3">
            <ProviderSetup provider={provider} onProviderChange={onProviderChange} />
          </div>
        </div>

        <div className="divider" />

        <div className="grid gap-6 sm:grid-cols-2">
          <div>
            <div className="eyebrow">ПРОВЕРКА КАЧЕСТВА НЕЙРОСЕТЬЮ</div>
            <label className="mt-3 flex items-center gap-3 text-base">
              <input
                type="checkbox"
                checked={vlm}
                onChange={(event) => onVlmChange(event.target.checked)}
                className="h-5 w-5"
              />
              Включена (замедляет, но находит смысловые огрехи)
            </label>
            {health?.vlm?.label && (
              <div className="mono mt-2 text-[12px] text-muted-foreground">{health.vlm.label}</div>
            )}
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label htmlFor="slides-count">Число слайдов</Label>
              <Input
                id="slides-count"
                type="number"
                min={5}
                max={15}
                value={slides}
                onChange={(event) => onSlidesChange(Number(event.target.value) || 12)}
              />
            </div>
            <div>
              <Label htmlFor="deck-language">Язык</Label>
              <Select
                id="deck-language"
                value={language}
                onChange={(event) => onLanguageChange(event.target.value)}
              >
                <option value="ru">Русский</option>
                <option value="en">English</option>
              </Select>
            </div>
            <div className="col-span-2">
              <Label htmlFor="render-mode">Режим сборки по умолчанию</Label>
              <Select
                id="render-mode"
                value={renderMode}
                onChange={(event) =>
                  onRenderMode(event.target.value as "native" | "html" | "both")
                }
              >
                <option value="native">Классический (python-pptx)</option>
                <option value="html">Через HTML+CSS</option>
                <option value="both">Оба (сравнение)</option>
              </Select>
            </div>
          </div>
        </div>

        {jobId && (
          <details className="rounded-2xl border border-dashed border-border p-4">
            <summary className="cursor-pointer text-base font-medium text-muted-foreground">
              Показать примеры оформления (для защиты)
            </summary>
            <div className="mt-3 flex flex-wrap gap-4">
              {VARIANTS.map((variant) => (
                <div key={variant.id} className="min-w-[260px] flex-1">
                  <div className="eyebrow mb-2">{variant.name}</div>
                  <div className="space-y-2">
                    {[0, 1].map((page) => (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        key={page}
                        src={api.thumbUrl(jobId, variant.id, page, false, version)}
                        alt={`${variant.name}, слайд ${page + 1}`}
                        className="w-full rounded-xl border border-border"
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </details>
        )}
      </div>
    </section>
  );
}
