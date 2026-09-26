import type { Metadata, Viewport } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Генератор презентаций",
  description:
    "Загрузите шаблон и опишите презентацию — получите три готовых варианта " +
    "в PPTX и PDF.",
};

export const viewport: Viewport = {
  themeColor: "#050607",
};

/**
 * Шрифт — системный стек (Inter, если установлен): сборка не зависит от
 * загрузки внешних шрифтов — важно для офлайн-демо и закрытого контура.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body className="font-sans">{children}</body>
    </html>
  );
}
