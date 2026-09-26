"use client";

import { Eye, EyeOff } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/field";
import { api } from "@/lib/api";
import type { ProviderStatus } from "@/lib/types";

const COMMON_MODELS = [
  "qwen3.5-9b",
  "qwen3.5-27b",
  "qwen3.5-72b",
  "gpt-4o-mini",
  "gpt-4.1-mini",
  "deepseek-chat",
  "llama3.1-70b",
];

/**
 * Подключение внешней нейросети: адрес, ключ, модель, проверка и применение.
 * Используется в «Для опытных» (с выбором источника) и в простом режиме
 * «Я не знаю, что делать» (сразу внешний сервис, `forceExternal`).
 */
export function ProviderSetup({
  provider,
  onProviderChange,
  idPrefix = "provider",
  forceExternal = false,
}: {
  provider: ProviderStatus | null;
  onProviderChange: (provider: ProviderStatus | null, label?: string) => void;
  idPrefix?: string;
  forceExternal?: boolean;
}) {
  const [external, setExternal] = React.useState(forceExternal || provider?.source === "external");
  const [baseUrl, setBaseUrl] = React.useState(
    provider?.base_url || "https://api.aitunnel.ru/v1",
  );
  const [apiKey, setApiKey] = React.useState("");
  const [model, setModel] = React.useState(provider?.model || "qwen3.5-9b");
  const [showKey, setShowKey] = React.useState(false);
  const [busy, setBusy] = React.useState<"" | "test" | "set" | "reset">("");
  const [message, setMessage] = React.useState<{ ok: boolean; text: string } | null>(
    provider?.status_message
      ? { ok: provider.status === "ok", text: provider.status_message }
      : null,
  );

  React.useEffect(() => {
    setExternal(forceExternal || provider?.source === "external");
    if (provider?.base_url) setBaseUrl(provider.base_url);
    if (provider?.model) setModel(provider.model);
  }, [provider, forceExternal]);

  React.useEffect(() => {
    api
      .providerStatus()
      .then((state) => onProviderChange(state.provider, state.label))
      .catch(() => {});
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

  const radioRow = (active: boolean) =>
    `flex cursor-pointer items-center gap-3 rounded-xl border px-4 py-3 text-base transition-colors ${
      active ? "border-[rgba(204,255,88,.45)] bg-[rgba(204,255,88,.05)]" : "border-border bg-card"
    }`;

  return (
    <div>
      {!forceExternal && (
        <div className="space-y-2">
          <label className={radioRow(!external)}>
            <input
              type="radio"
              name={`${idPrefix}-source`}
              checked={!external}
              onChange={() => setExternal(false)}
              className="h-5 w-5"
            />
            Локально (по умолчанию) — бесплатно, без ключа
          </label>
          <label className={radioRow(external)}>
            <input
              type="radio"
              name={`${idPrefix}-source`}
              checked={external}
              onChange={() => setExternal(true)}
              className="h-5 w-5"
            />
            Внешний сервис — нужен свой API-ключ
          </label>
        </div>
      )}

      {external && (
        <div className={forceExternal ? "space-y-4" : "mt-4 space-y-4 rounded-2xl border border-border bg-secondary p-4"}>
          <div>
            <Label htmlFor={`${idPrefix}-url`}>Адрес сервиса</Label>
            <Input
              id={`${idPrefix}-url`}
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="https://api.aitunnel.ru/v1"
              autoComplete="off"
            />
          </div>
          <div>
            <Label htmlFor={`${idPrefix}-key`}>API-ключ</Label>
            <div className="flex gap-2">
              <Input
                id={`${idPrefix}-key`}
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
            <Label htmlFor={`${idPrefix}-model`}>Модель</Label>
            <Select
              id={`${idPrefix}-model`}
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

      {!forceExternal && (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button type="button" variant="outline" onClick={reset} disabled={busy !== ""}>
            {busy === "reset" ? "Сбрасываем…" : "Удалить ключ"}
          </Button>
          <Button type="button" variant="ghost" onClick={reset} disabled={busy !== ""}>
            Вернуться к локальному
          </Button>
        </div>
      )}

      <div className="mono mt-3 text-[13px] text-muted-foreground">
        {forceExternal ? (
          connected ? (
            <span>
              Ключ подключён: {provider?.model}. Маска: {provider?.masked_key || "—"}
            </span>
          ) : (
            <span>Ключ пока не подключён — соберём офлайн-планировщиком: быстрее, но текст проще.</span>
          )
        ) : connected ? (
          <span>
            Работает внешний сервис {provider?.model}. Ключ: {provider?.masked_key || "не сохранён"}
          </span>
        ) : (
          <span>Работает локальная нейросеть.</span>
        )}
      </div>
      {message && (
        <div className={`notice mt-3 ${message.ok ? "notice-success" : "notice-error"}`}>
          <span
            className={`notice-icon ${
              message.ok ? "text-[hsl(var(--status-green))]" : "text-[hsl(var(--status-red))]"
            }`}
            aria-hidden
          >
            {message.ok ? "✓" : "✕"}
          </span>
          <div className="text-base">{message.text}</div>
        </div>
      )}
    </div>
  );
}
