import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Stage Copilot v4 — Recherche & Candidature IA",
  description: "Interface React Next.js ultra-rapide pour Stage Copilot (Notation v3 & v4, Kanban et Pack Claude).",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="fr">
      <body>{children}</body>
    </html>
  );
}
