import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Racooon — Voice, video and links into useful text",
  description:
    "Turn voice messages, interviews, lectures, videos and links into text, notes and translations.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
