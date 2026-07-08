import Link from "next/link";
import { ArrowRight, BarChart3, ShieldCheck, TrendingUp } from "lucide-react";

export default function Dashboard() {
  return (
    <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-1000">
      <div>
        <h1 className="text-4xl font-extrabold tracking-tight">Dashboard</h1>
        <p className="text-muted-foreground mt-2 text-lg">AI Lending Decision Intelligence Platform</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="glass-panel p-6 rounded-xl flex items-center justify-between">
          <div>
            <p className="text-sm text-muted-foreground">Processed Applications</p>
            <p className="text-3xl font-bold mt-1">1,248</p>
          </div>
          <div className="h-12 w-12 rounded-full bg-blue-500/20 flex items-center justify-center text-blue-400">
            <BarChart3 size={24} />
          </div>
        </div>
        
        <div className="glass-panel p-6 rounded-xl flex items-center justify-between">
          <div>
            <p className="text-sm text-muted-foreground">Avg. Approval Rate</p>
            <p className="text-3xl font-bold mt-1">68.4%</p>
          </div>
          <div className="h-12 w-12 rounded-full bg-green-500/20 flex items-center justify-center text-green-400">
            <TrendingUp size={24} />
          </div>
        </div>
        
        <div className="glass-panel p-6 rounded-xl flex items-center justify-between">
          <div>
            <p className="text-sm text-muted-foreground">Fraud Prevented</p>
            <p className="text-3xl font-bold mt-1">$4.2M</p>
          </div>
          <div className="h-12 w-12 rounded-full bg-purple-500/20 flex items-center justify-center text-purple-400">
            <ShieldCheck size={24} />
          </div>
        </div>
      </div>

      <div className="glass-panel rounded-xl p-8 mt-8">
        <h2 className="text-2xl font-bold mb-4">Start New Analysis</h2>
        <p className="text-muted-foreground mb-6">Initiate the AI decision pipeline by creating a new MSME profile.</p>
        <Link 
          href="/company"
          className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-50 disabled:pointer-events-none ring-offset-background bg-primary text-primary-foreground hover:bg-primary/90 h-10 py-2 px-4"
        >
          Create MSME Company
          <ArrowRight className="ml-2 h-4 w-4" />
        </Link>
      </div>
    </div>
  );
}
