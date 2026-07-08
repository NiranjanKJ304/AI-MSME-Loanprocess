import type { Metadata } from "next";
import { Inter, Geist } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";
import { cn } from "@/lib/utils";

const geist = Geist({subsets:['latin'],variable:'--font-sans'});

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "LendingMind AI",
  description: "AI Lending Decision Intelligence Platform",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={cn("dark", "font-sans", geist.variable)}>
      <body className={`${inter.className} min-h-screen flex`}>
        <Sidebar />
        <main className="flex-1 ml-64 p-8 overflow-y-auto h-screen">
          <div className="max-w-6xl mx-auto">
            {children}
          </div>
        </main>
      </body>
    </html>
  );
}
