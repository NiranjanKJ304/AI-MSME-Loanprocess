"use client";

import { Lightbulb, ChevronRight } from "lucide-react";
import Link from "next/link";

export default function RecommendationPage() {
  return (
    <div className="space-y-8 animate-in fade-in duration-700">
      <div className="flex items-center gap-4">
        <div className="h-12 w-12 rounded-xl bg-yellow-500/20 flex items-center justify-center text-yellow-400">
          <Lightbulb size={24} />
        </div>
        <div>
          <h1 className="text-3xl font-bold">Loan Recommendation</h1>
          <p className="text-muted-foreground mt-1">AI-generated structuring based on capacity and risk.</p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="glass-panel p-8 rounded-xl bg-gradient-to-br from-white/5 to-purple-500/10">
          <h2 className="text-lg font-semibold text-muted-foreground mb-2">Recommended Principal</h2>
          <p className="text-5xl font-bold mb-6">$50,000</p>
          
          <h2 className="text-lg font-semibold text-muted-foreground mb-2">Suggested Interest Rate</h2>
          <p className="text-4xl font-bold text-green-400 mb-6">8.5% <span className="text-sm font-normal text-muted-foreground">APR</span></p>

          <h2 className="text-lg font-semibold text-muted-foreground mb-2">Confidence Score</h2>
          <div className="flex items-center gap-3">
            <div className="flex-1 h-2 bg-white/10 rounded-full overflow-hidden">
              <div className="h-full bg-blue-500 w-[94%]" />
            </div>
            <span className="font-bold">94%</span>
          </div>
        </div>

        <div className="glass-panel p-8 rounded-xl space-y-6">
          <h2 className="text-xl font-semibold">Business Improvement Suggestions</h2>
          <ul className="space-y-4">
            <li className="flex gap-3">
              <ChevronRight className="text-primary shrink-0" size={20} />
              <span className="text-sm">Diversify client base to reduce dependency risk (Business Stability Agent).</span>
            </li>
            <li className="flex gap-3">
              <ChevronRight className="text-primary shrink-0" size={20} />
              <span className="text-sm">Negotiate better payment terms with suppliers to buffer Q2 cash flow volatility.</span>
            </li>
            <li className="flex gap-3">
              <ChevronRight className="text-primary shrink-0" size={20} />
              <span className="text-sm">Increase marketing spend in Q3 to capture forecasted peak demand.</span>
            </li>
          </ul>

          <div className="pt-6 mt-6 border-t border-white/10">
            <Link 
              href="/scenario-simulator"
              className="w-full inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors bg-secondary text-secondary-foreground hover:bg-secondary/80 h-10 py-2 px-4"
            >
              Simulate Scenarios
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
