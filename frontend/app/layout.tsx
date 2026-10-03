import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Bibliometrics — Research Intelligence Workspace",
  description:
    "Evidence-grounded research intelligence over Scopus publications. Ask in natural language, trace every number to database-backed evidence.",
  icons: {
    icon: [{ url: "/favicon.ico" }, { url: "/logo.png", type: "image/png" }],
    apple: "/apple-touch-icon.png",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#workspace-main">
          Skip to research workspace
        </a>
        {children}
      </body>
    </html>
  );
}
