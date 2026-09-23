"use client";

import { ChevronLeft, ChevronRight, Frame, Loader2 } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { VariantName } from "@/lib/types";

export function DeckViewer({
  jobId,
  variant,
  version,
  slidesCount,
  index,
  onIndexChange,
}: {
  jobId: string;
  variant: VariantName;
  version: number;
  slidesCount: number;
  index: number;
  onIndexChange: (index: number) => void;
}) {
  const [showBoxes, setShowBoxes] = React.useState(true);
  const [loading, setLoading] = React.useState(true);
  const setIndex = onIndexChange;

  React.useEffect(() => {
    setLoading(true);
  }, [index, variant, version]);

  const pages = Array.from({ length: slidesCount });

  return (
    <div className="flex min-h-0 flex-1 gap-4">
      <div className="flex w-[168px] shrink-0 flex-col gap-2 overflow-y-auto pr-1">
        {pages.map((_, page) => (
          <button
            key={page}
            onClick={() => setIndex(page)}
            className={cn(
              "overflow-hidden rounded-md border transition-colors",
              page === index ? "border-primary" : "border-border hover:border-muted-foreground/50",
            )}
            title={`слайд ${page + 1}`}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={api.thumbUrl(jobId, variant, page, false, version)}
              alt={`слайд ${page + 1}`}
              className="w-full"
              loading="lazy"
            />
          </button>
        ))}
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <div className="relative flex flex-1 items-center justify-center overflow-hidden rounded-lg border border-border bg-black/30">
          {loading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> рендер миниатюры…
            </div>
          )}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            key={`${variant}-${version}-${index}-${showBoxes}`}
            src={api.thumbUrl(jobId, variant, index, showBoxes, version)}
            alt={`слайд ${index + 1}`}
            className="max-h-full max-w-full object-contain"
            onLoad={() => setLoading(false)}
            onError={() => setLoading(false)}
          />
        </div>

        <div className="flex items-center gap-3 text-xs">
          <Button
            variant="secondary"
            size="icon"
            disabled={index === 0}
            onClick={() => setIndex(Math.max(0, index - 1))}
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="text-muted-foreground">
            слайд {index + 1} из {slidesCount}
          </span>
          <Button
            variant="secondary"
            size="icon"
            disabled={index >= slidesCount - 1}
            onClick={() => setIndex(Math.min(slidesCount - 1, index + 1))}
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
          <Button
            variant={showBoxes ? "outline" : "ghost"}
            size="sm"
            className="ml-auto"
            onClick={() => setShowBoxes((value) => !value)}
          >
            <Frame className="h-3 w-3" /> рамки проблем
          </Button>
        </div>
      </div>
    </div>
  );
}
