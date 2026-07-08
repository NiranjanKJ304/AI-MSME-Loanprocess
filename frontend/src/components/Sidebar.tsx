"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, Building2, FileSpreadsheet, GitBranch, Target, Lightbulb, SlidersHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";

const navItems = [
  { name: "Dashboard", href: "/", icon: LayoutDashboard },
  { name: "Company", href: "/company", icon: Building2 },
  { name: "Financial Data", href: "/financial-data", icon: FileSpreadsheet },
  { name: "Pipeline", href: "/pipeline", icon: GitBranch },
  { name: "Decision Center", href: "/decision-center", icon: Target },
  { name: "Recommendation", href: "/recommendation", icon: Lightbulb },
  { name: "Scenario Simulator", href: "/scenario-simulator", icon: SlidersHorizontal },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-64 h-screen border-r border-white/10 glass-panel flex flex-col fixed left-0 top-0">
      <div className="p-6 border-b border-white/10">
        <h1 className="text-2xl font-bold bg-gradient-to-r from-blue-400 to-purple-500 bg-clip-text text-transparent">
          LendingMind AI
        </h1>
        <p className="text-xs text-muted-foreground mt-1">Decision Intelligence</p>
      </div>
      <nav className="flex-1 p-4 space-y-2 overflow-y-auto">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-3 px-3 py-2 rounded-md transition-all duration-200",
                isActive 
                  ? "bg-primary/20 text-primary border border-primary/30" 
                  : "text-muted-foreground hover:bg-white/5 hover:text-foreground"
              )}
            >
              <Icon size={18} />
              <span className="text-sm font-medium">{item.name}</span>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
