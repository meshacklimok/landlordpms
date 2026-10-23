import type { Metadata } from "next";
import localFont from "next/font/local";
import "bootstrap-icons/font/bootstrap-icons.css";
import { themeBootScript } from "@/lib/theme";
import "./globals.css";

const inter = localFont({
  src: "./fonts/inter-latin-wght-normal.woff2",
  variable: "--font-inter",
  display: "swap",
  weight: "100 900",
});

export const metadata: Metadata = {
  title: { default: "Home", template: "%s · NyumbaPMS" },
  description: "Rent, tenants and repairs for landlords and agents in Kenya.",
  manifest: "/manifest.webmanifest", // Django's, so the browser can offer to install from these screens too (D-080)
  robots: { index: false, follow: false }, // signed-in screens; the public pages stay on Django (D-158)
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={inter.variable} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootScript }} />
      </head>
      <body>
        <a className="skip-link" href="#main">Skip to content</a>
        {children}
      </body>
    </html>
  );
}
