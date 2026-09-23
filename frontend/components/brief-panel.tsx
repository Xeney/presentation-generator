"use client";

import { FileUp, Loader2, Sparkles } from "lucide-react";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select, Textarea } from "@/components/ui/field";
import { Separator } from "@/components/ui/separator";
import { api } from "@/lib/api";
import type { Corpus, GenerateInput } from "@/lib/types";

const PURPOSES = [
  { value: "project", label: "Проект: цели, объём, команда, сроки" },
  { value: "feature", label: "Новый продукт / фича" },
  { value: "product", label: "Продукт и его рынок" },
  { value: "initiative", label: "Инициатива: цели, аудитория, этапы" },
];

const DEFAULT_BRIEF =
  "Компания запускает мобильное приложение для доставки еды. За полгода охват — 3 города, " +
  "ежемесячная аудитория 150 000 человек, средний чек 870 руб. Снижение времени доставки на 18%. " +
  "Цель — выйти на 10 городов к концу года и повысить LTV на 25%.";

export function BriefPanel({
  onGenerate,
  running,
}: {
  onGenerate: (input: GenerateInput) => void;
  running: boolean;
}) {
  const [template, setTemplate] = React.useState<File | null>(null);
  const [brief, setBrief] = React.useState(DEFAULT_BRIEF);
  const [source, setSource] = React.useState("");
  const [purpose, setPurpose] = React.useState("project");
  const [corpus, setCorpus] = React.useState<Corpus | null>(null);
  const [corpusError, setCorpusError] = React.useState("");
  const [importing, setImporting] = React.useState(false);

  const importCorpus = async (file: File | undefined) => {
    if (!file) {
      setCorpusError("Выбери файл контент-пакета (PPTX, DOCX, TXT или MD)");
      return;
    }
    setImporting(true);
    setCorpusError("");
    try {
      setCorpus(await api.importCorpus(file));
    } catch (error) {
      setCorpus(null);
      setCorpusError(error instanceof Error ? error.message : String(error));
    } finally {
      setImporting(false);
    }
  };

  const submit = () => {
    if (!template) return;
    onGenerate({ template, brief, source, purpose, corpusId: corpus?.id });
  };

  return (
    <Card className="border-0 border-r border-border bg-card/60">
      <CardHeader className="gap-2">
        <CardTitle className="text-base">Цифровой дизайнер презентаций</CardTitle>
        <p className="text-xs text-muted-foreground">
          VK Tech · генерация по брифу на произвольном PPTX-шаблоне
        </p>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor="template">Шаблон PPTX *</Label>
          <Input
            id="template"
            type="file"
            accept=".pptx"
            className="h-auto py-2 text-xs file:mr-3 file:rounded file:border-0 file:bg-secondary file:px-3 file:py-1 file:text-secondary-foreground"
            onChange={(event) => setTemplate(event.target.files?.[0] ?? null)}
          />
          <p className="truncate text-xs text-muted-foreground">
            {template?.name ?? "файл не выбран — подойдёт любой PPTX"}
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="corpus">Контент-пакет (необязательно)</Label>
          <Input
            id="corpus"
            type="file"
            accept=".pptx,.docx,.txt,.md"
            className="h-auto py-2 text-xs file:mr-3 file:rounded file:border-0 file:bg-secondary file:px-3 file:py-1 file:text-secondary-foreground"
            onChange={(event) => importCorpus(event.target.files?.[0])}
          />
          {importing && (
            <p className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" /> разбираю пакет…
            </p>
          )}
          {corpusError && <p className="text-xs text-destructive">{corpusError}</p>}
          {corpus && (
            <div className="rounded-md border border-border bg-background/50 p-3 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium">{corpus.source_file}</span>
                <Badge variant="secondary">{corpus.kind.toUpperCase()}</Badge>
              </div>
              <p className="mt-1 text-muted-foreground">
                слайдов {corpus.stats.non_empty} · картинок {corpus.stats.images} · цифр{" "}
                {corpus.stats.numbers}
              </p>
              <div className="mt-2 max-h-32 space-y-1 overflow-y-auto pr-1">
                {(corpus.preview ?? []).slice(0, 8).map((slide) => (
                  <div key={slide.index} className="flex gap-2 text-muted-foreground">
                    <span className="text-foreground/70">{slide.index + 1}.</span>
                    <span className="truncate">{slide.heading}</span>
                    {slide.images.length > 0 && <span>· фото {slide.images.length}</span>}
                    {slide.tables > 0 && <span>· таблиц {slide.tables}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <Separator />

        <div className="flex flex-col gap-2">
          <Label htmlFor="brief">Бриф</Label>
          <Textarea
            id="brief"
            rows={6}
            value={brief}
            onChange={(event) => setBrief(event.target.value)}
          />
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="source">Дополнительные материалы (необязательно)</Label>
          <Textarea
            id="source"
            rows={3}
            value={source}
            placeholder="Вставь текст, если фактов нет в контент-пакете"
            onChange={(event) => setSource(event.target.value)}
          />
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="purpose">Назначение</Label>
          <Select id="purpose" value={purpose} onChange={(event) => setPurpose(event.target.value)}>
            {PURPOSES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </Select>
        </div>

        <Button onClick={submit} disabled={running || !template} className="w-full">
          {running ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" /> Генерация…
            </>
          ) : (
            <>
              <Sparkles className="h-4 w-4" /> Сгенерировать 3 варианта
            </>
          )}
        </Button>
        {!template && (
          <p className="flex items-center gap-1 text-xs text-muted-foreground">
            <FileUp className="h-3 w-3" /> выбери шаблон, чтобы запустить
          </p>
        )}
      </CardContent>
    </Card>
  );
}
