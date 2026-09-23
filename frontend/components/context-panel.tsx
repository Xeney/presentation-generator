"use client";

import { Brain, Link2, ListTree } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { JobInfo } from "@/lib/types";

/**
 * Контекстуальная часть аудита: VLM, проверка опоры на источник и версии
 * промптов. Отделена от детерминированных проверок намеренно — у них разная
 * природа (Приложение 1 ТЗ) и разная стабильность результата.
 */
export function ContextPanel({ info }: { info: JobInfo | null }) {
  const vlm = info?.vlm;
  const grounding = info?.grounding;
  const prompts = info?.prompts ?? {};

  const vlmIssues = (vlm?.slides ?? []).filter((slide) => !slide.ok);
  const groundingIssues = grounding?.issues ?? [];

  return (
    <Card className="flex min-h-0 flex-col">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2">
          <Brain className="h-4 w-4 text-primary" /> Контекстуальный аудит
        </CardTitle>
      </CardHeader>
      <CardContent className="min-h-0 flex-1 overflow-y-auto">
        <Tabs defaultValue="vlm" className="flex flex-col gap-3">
          <TabsList>
            <TabsTrigger value="vlm">
              <Brain className="h-3 w-3" /> VLM
              {vlm?.available && vlmIssues.length > 0 && (
                <Badge variant="warning">{vlmIssues.length}</Badge>
              )}
            </TabsTrigger>
            <TabsTrigger value="grounding">
              <Link2 className="h-3 w-3" /> Источник
              {grounding?.available && groundingIssues.length > 0 && (
                <Badge variant="warning">{groundingIssues.length}</Badge>
              )}
            </TabsTrigger>
            <TabsTrigger value="prompts">
              <ListTree className="h-3 w-3" /> Промпты
            </TabsTrigger>
          </TabsList>

          <TabsContent value="vlm" className="space-y-2 text-xs">
            {vlm?.available ? (
              <>
                <p className="text-muted-foreground">
                  {vlm.provider ? `${vlm.provider}/` : ""}{vlm.model} · слайдов{" "}
                  {vlm.slides.length} · {vlm.elapsed_s} c
                </p>
                {vlmIssues.length === 0 && (
                  <p className="text-[hsl(var(--success))]">смысловых замечаний нет</p>
                )}
                {vlmIssues.map((slide) => (
                  <div key={slide.slide} className="rounded-md border border-border bg-muted/40 p-2">
                    <p className="font-medium text-[hsl(var(--warning))]">
                      слайд {slide.slide + 1}
                    </p>
                    {slide.violations_text.map((text) => (
                      <p key={text} className="text-foreground/85">
                        • {text}
                      </p>
                    ))}
                    {slide.summary && (
                      <p className="mt-1 text-muted-foreground">{slide.summary}</p>
                    )}
                  </div>
                ))}
              </>
            ) : (
              <p className="text-muted-foreground">
                {vlm?.reason || "недоступен: нужна Ollama с Qwen2.5-VL"}
              </p>
            )}
          </TabsContent>

          <TabsContent value="grounding" className="space-y-2 text-xs">
            {grounding?.available ? (
              <>
                <p className="text-muted-foreground">
                  модель {grounding.model} · замечаний{" "}
                  {grounding.issues_count ?? groundingIssues.length}
                </p>
                {groundingIssues.length === 0 && (
                  <p className="text-[hsl(var(--success))]">
                    все цифры и смысловые связи подтверждены источником
                  </p>
                )}
                {groundingIssues.map((issue) => (
                  <p key={issue.id}>
                    <span className="text-[hsl(var(--warning))]">
                      слайд {issue.slide + 1}:
                    </span>{" "}
                    {issue.message}
                  </p>
                ))}
              </>
            ) : (
              <p className="text-muted-foreground">
                {grounding?.reason || "нужен контент-пакет"}
              </p>
            )}
          </TabsContent>

          <TabsContent value="prompts" className="space-y-1 text-xs">
            {Object.entries(prompts).map(([id, prompt]) => (
              <p key={id} className="flex items-center justify-between gap-2">
                <span className="truncate">{id}</span>
                <span className="text-muted-foreground">
                  v{prompt.version} · {prompt.hash.slice(0, 8)} · {prompt.kind}
                </span>
              </p>
            ))}
            {Object.keys(prompts).length === 0 && (
              <p className="text-muted-foreground">манифест промптов недоступен</p>
            )}
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
