"use client";

import { Eye, EyeOff } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/field";
import { api } from "@/lib/api";
import type { Health, ProviderStatus, VariantName } from "@/lib/types";

const COMMON_MODELS = [
  "qwen3.5-9b",
  "qwen3.5-27b",
  "qwen3.5-72b",
  "gpt-4o-mini",
  "gpt-4.1-mini",
  "deepseek-chat",
  "llama3.1-70b",
];

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
  provider: ProviderStatus | null;
  onProviderChange: (provider: ProviderStatus | null, label?: string) => void;
  jobId: string | null;
  version: number;
}) {
  const [external, setExternal] = React.useState(provider?.source === "external");
  const [baseUrl, setBaseUrl] = React.useState(
    provider?.base_url || "https://api.aitunnel.ru/v1",
  );
  const [apiKey, setApiKey] = React.useState("");
  const [model, setModel] = React.useState(provider?.model || "qwen3.5-9b");
  const [showKey, setShowKey] = React.useState(false);
  const [busy, setBusy] = React.useState<"" | "test" | "set" | "reset">("");
  const [message, setMessage] = React.useState<{ ok: boolean; text: string } | null>(
    provider?.status_message ? { ok: provider.status === "ok", text: provider.status_message } : null,
  );

  React.useEffect(() => {
    setExternal(provider?.source === "external");
    if (provider?.base_url) setBaseUrl(provider.base_url);
    if (provider?.model) setModel(provider.model);
  }, [provider]);

  React.useEffect(() => {
    api.providerStatus().then((state) => onProviderChange(state.provider, state.label)).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const payload = () => ({ base_url: baseUrl.trim(), api_key: apiKey.trim(), model });

  const test = async () => {
    setBusy("test");
    setMessage(null);
    try {
      const result = await api.providerTest(payload());
      setMessage({ ok: result.ok, text: result.message });
    } catch {
      setMessage({ ok: false, text: "Не получилось проверить подключение. Попробуйте ещё раз." });
    } finally {
      setBusy("");
    }
  };

  const apply = async () => {
    setBusy("set");
    setMessage(null);
    try {
      const result = await api.providerSet(payload());
      setMessage({ ok: result.ok, text: result.message });
      if (result.ok && result.provider) onProviderChange(result.provider, result.label);
    } catch {
      setMessage({ ok: false, text: "Не получилось применить ключ. Попробуйте ещё раз." });
    } finally {
      setBusy("");
    }
  };

  const reset = async () => {
    setBusy("reset");
    setMessage(null);
    try {
      const result = await api.providerReset();
      onProviderChange(result.provider, result.label);
      setMessage({ ok: true, text: "Работает локально, без вашего ключа." });
      setApiKey("");
    } catch {
      setMessage({ ok: false, text: "Не получилось вернуться к локальному режиму." });
    } finally {
      setBusy("");
    }
  };

  const connected = provider?.source === "external";

  return (
    <details className="mt-8 rounded-2xl border border-border bg-card p-5">
      <summary className="cursor-pointer text-lg font-semibold text-muted-foreground">
        Для опытных
      </summary>

      <div className="mt-5 space-y-6">
        <div>
          <div className="text-lg font-semibold">Источник нейросети</div>
          <div className="mt-2 space-y-2">
            <label className="flex items-center gap-3 text-base">
              <input
                type="radio"
                name="provider-source"
                checked={!external}
                onChange={() => setExternal(false)}
                className="h-5 w-5"
              />
              Локально (по умолчанию) — бесплатно, без ключа
            </label>
            <label className="flex items-center gap-3 text-base">
              <input
                type="radio"
                name="provider-source"
                checked={external}
                onChange={() => setExternal(true)}
                className="h-5 w-5"
              />
              Внешний сервис — нужен свой API-ключ
            </label>
          </div>

          {external && (
            <div className="mt-4 space-y-4 rounded-xl bg-secondary p-4">
              <div>
                <Label htmlFor="provider-url">Адрес сервиса</Label>
                <Input
                  id="provider-url"
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.target.value)}
                  placeholder="https://api.aitunnel.ru/v1"
                  autoComplete="off"
                />
              </div>
              <div>
                <Label htmlFor="provider-key">API-ключ</Label>
                <div className="flex gap-2">
                  <Input
                    id="provider-key"
                    type={showKey ? "text" : "password"}
                    value={apiKey}
                    onChange={(event) => setApiKey(event.target.value)}
                    placeholder="sk-…"
                    autoComplete="off"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    aria-label={showKey ? "Скрыть ключ" : "Показать ключ"}
                    onClick={() => setShowKey((value) => !value)}
                  >
                    {showKey ? <EyeOff /> : <Eye />}
                  </Button>
                </div>
              </div>
              <div>
                <Label htmlFor="provider-model">Модель</Label>
                <Select
                  id="provider-model"
                  value={model}
                  onChange={(event) => setModel(event.target.value)}
                >
                  {COMMON_MODELS.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="flex flex-wrap gap-3">
                <Button type="button" variant="outline" onClick={test} disabled={busy !== ""}>
                  {busy === "test" ? "Проверяем…" : "Проверить подключение"}
                </Button>
                <Button type="button" onClick={apply} disabled={busy !== ""}>
                  {busy === "set" ? "Применяем…" : "Применить"}
                </Button>
              </div>
            </div>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-3">
            <Button type="button" variant="outline" onClick={reset} disabled={busy !== ""}>
              {busy === "reset" ? "Сбрасываем…" : "Удалить ключ"}
            </Button>
            <Button type="button" variant="ghost" onClick={reset} disabled={busy !== ""}>
              Вернуться к локальному
            </Button>
          </div>

          <div className="mt-2 text-base">
            {connected ? (
              <span className="text-muted-foreground">
                Работает внешний сервис {provider?.model}. Ключ: {provider?.masked_key || "не сохранён"}
              </span>
            ) : (
              <span className="text-muted-foreground">Работает локальная нейросеть.</span>
            )}
          </div>
          {message && (
            <div className={`mt-2 flex items-center gap-2 text-base font-medium ${message.ok ? "text-success" : "text-destructive"}`}>
              <span aria-hidden>{message.ok ? "✓" : "✕"}</span>
              {message.text}
            </div>
          )}
        </div>

        <div className="grid gap-5 sm:grid-cols-2">
          <div>
            <div className="text-lg font-semibold">Проверка качества нейросетью</div>
            <label className="mt-2 flex items-center gap-3 text-base">
              <input
                type="checkbox"
                checked={vlm}
                onChange={(event) => onVlmChange(event.target.checked)}
                className="h-5 w-5"
              />
              Включена (замедляет, но находит смысловые огрехи)
            </label>
            {health?.vlm?.label && (
              <div className="mt-1 text-sm text-muted-foreground">{health.vlm.label}</div>
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
          </div>
        </div>

        {jobId && (
          <details className="rounded-xl border border-dashed border-border p-4">
            <summary className="cursor-pointer text-base font-medium text-muted-foreground">
              Показать примеры оформления (для защиты)
            </summary>
            <div className="mt-3 flex flex-wrap gap-4">
              {VARIANTS.map((variant) => (
                <div key={variant.id} className="min-w-[260px] flex-1">
                  <div className="mb-2 text-base font-medium">{variant.name}</div>
                  <div className="space-y-2">
                    {[0, 1].map((page) => (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        key={page}
                        src={api.thumbUrl(jobId, variant.id, page, false, version)}
                        alt={`${variant.name}, слайд ${page + 1}`}
                        className="w-full rounded-lg border border-border"
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </details>
        )}
      </div>
    </details>
  );
}
