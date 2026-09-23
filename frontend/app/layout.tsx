import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Цифровой дизайнер презентаций",
  description:
    "Сервис генерации презентаций по текстовому брифу на произвольном PPTX-шаблоне: " +
    "три варианта вёрстки, аудит и экспорт в PPTX, PDF и HTML.",
};

/**
 * Шрифт — системный стек: сборка не зависит от загрузки внешних шрифтов
 * (важно для офлайн-демо и сборки в закрытом контуре).
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" className="dark">
      <body className="font-sans">{children}</body>
    </html>
  );
}
