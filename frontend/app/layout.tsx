import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "AIRP Local",
  description: "Local-first AI investment research platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <header className="topbar">
            <div className="brand">
              AIRP <span className="brand-accent">Local</span>
            </div>
            <nav>
              <Link href="/">Home</Link>
              <Link href="/sandbox">Sandbox / Backtest</Link>
            </nav>
          </header>
          <main className="content">{children}</main>
          <footer className="footer">
            Research proposals only. Nothing here executes trades. See{" "}
            <code>docs/ARCHITECTURE.md</code> in the repo for how the guarantees work.
          </footer>
        </div>
      </body>
    </html>
  );
}
