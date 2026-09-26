import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Генератор презентаций",
  description:
    "Загрузите шаблон и опишите презентацию — получите три готовых варианта " +
    "в PPTX и PDF.",
};

/**
 * Шрифт — системный стек: сборка не зависит от загрузки внешних шрифтов
 * (важно для офлайн-демо и сборки в закрытом контуре).
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body className="font-sans">{children}</body>
    </html>
  );
}
