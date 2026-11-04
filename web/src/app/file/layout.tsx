import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "File de candidature · Stage Copilot",
  description: "Offre courante à remplir par Claude in Chrome, compte rendu et suivi de la file.",
};

export default function FileLayout({ children }: { children: React.ReactNode }) {
  return children;
}
