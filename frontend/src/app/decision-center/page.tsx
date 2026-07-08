"use client";

import { Target, CheckCircle } from "lucide-react";
import Link from "next/link";
import { ResponsiveContainer, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, Tooltip } from "recharts";

const radarData = [
  { subject: 'Financial Health', A: 85, fullMark: 100 },
  { subject: 'Cash Flow', A: 70, fullMark: 100 },
  { subject: 'Compliance', A: 98, fullMark: 100 },
  { subject: 'Forecast', A: 82, fullMark: 100 },
  { subject: 'Stability', A: 75, fullMark: 100 },
  { subject: 'Fraud Score (Inv)', A: 88, fullMark: 100 },
];

export default function DecisionCenterPage() {
  return (
    <div className="space-y-8 animate-in fade-in duration-700">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className="h-12 w-12 rounded-xl bg-orange-500/20 flex items-center justify-center text-orange-400">
            <Target size={24} />
          </div>
          <div>
            <h1 className="text-3xl font-bold">Decision Center</h1>
            <p className="text-muted-foreground mt-1">Aggregated AI scoring and explanation</p>
          </div>
        </div>
        <div className="glass-panel px-6 py-2 rounded-full border-green-500/50 bg-green-500/10 flex items-center gap-2">
          <CheckCircle className="text-green-400" size={20} />
          <span className="font-bold text-green-400">APPROVED</span>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="glass-panel p-6 rounded-xl space-y-6">
          <h2 className="text-xl font-semibold">Health Score Breakdown</h2>
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart cx="50%" cy="50%" outerRadius="80%" data={radarData}>
                <PolarGrid stroke="rgba(255,255,255,0.1)" />
                <PolarAngleAxis dataKey="subject" tick={{ fill: 'rgba(255,255,255,0.7)', fontSize: 12 }} />
                <PolarRadiusAxis angle={30} domain={[0, 100]} tick={false} axisLine={false} />
                <Radar name="Score" dataKey="A" stroke="#8b5cf6" fill="#8b5cf6" fillOpacity={0.5} />
                <Tooltip contentStyle={{ backgroundColor: '#1e1e2d', borderColor: 'rgba(255,255,255,0.1)' }} />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="glass-panel p-6 rounded-xl space-y-4">
          <h2 className="text-xl font-semibold">AI Reasoning</h2>
          <div className="space-y-3">
            <div className="p-3 bg-white/5 rounded border border-white/10">
              <span className="text-xs text-purple-400 font-bold uppercase tracking-wider block mb-1">Financial Health</span>
              <p className="text-sm">Strong debt-to-equity ratio and consistent revenue growth over the last 3 quarters.</p>
            </div>
            <div className="p-3 bg-white/5 rounded border border-white/10">
              <span className="text-xs text-orange-400 font-bold uppercase tracking-wider block mb-1">Fraud Risk</span>
              <p className="text-sm">No anomalies detected in recent transactions. Identity verified across GST and UPI data.</p>
            </div>
            <div className="p-3 bg-white/5 rounded border border-white/10">
              <span className="text-xs text-blue-400 font-bold uppercase tracking-wider block mb-1">Forecast</span>
              <p className="text-sm">Projected 15% YoY revenue growth based on historical seasonality trends.</p>
            </div>
          </div>
          
          <div className="pt-4 border-t border-white/10 mt-4">
             <Link href="/recommendation" className="text-primary text-sm font-medium hover:underline flex items-center">
                Proceed to Loan Recommendation →
             </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
