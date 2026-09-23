import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Цифровой дизайнер презентаций",
  description: "Генерация презентаций VK Tech по брифу на произвольном шаблоне",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body style={{ margin: 0, background: "#0e0f13", color: "#e8eaed",
        fontFamily: "Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif" }}>
        {children}
      </body>
    </html>
  );
}