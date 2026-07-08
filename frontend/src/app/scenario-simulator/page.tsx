"use client";

import { useState } from "react";
import { SlidersHorizontal, Play } from "lucide-react";

export default function ScenarioSimulatorPage() {
  const [loading, setLoading] = useState(false);
  const [params, setParams] = useState({
    loanAmount: 50000,
    interestRate: 8.5,
    revenueGrowth: 15,
    employeeReduction: 0,
    cashFlowChange: 0,
  });

  const runSimulation = () => {
    setLoading(true);
    setTimeout(() => {
      setLoading(false);
    }, 1500);
  };

  return (
    <div className="space-y-8 animate-in fade-in duration-700">
      <div className="flex items-center gap-4">
        <div className="h-12 w-12 rounded-xl bg-pink-500/20 flex items-center justify-center text-pink-400">
          <SlidersHorizontal size={24} />
        </div>
        <div>
          <h1 className="text-3xl font-bold">Scenario Simulator</h1>
          <p className="text-muted-foreground mt-1">Stress-test MSME profile against various economic conditions</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
        <div className="lg:col-span-4 space-y-6 glass-panel p-6 rounded-xl">
          <h2 className="text-xl font-semibold mb-4 border-b border-white/10 pb-2">Parameters</h2>
          
          <div className="space-y-4">
            <div>
              <div className="flex justify-between mb-1">
                <label className="text-sm">Loan Amount</label>
                <span className="text-sm font-bold">${params.loanAmount}</span>
              </div>
              <input 
                type="range" min="10000" max="250000" step="5000"
                value={params.loanAmount}
                onChange={(e) => setParams({...params, loanAmount: Number(e.target.value)})}
                className="w-full accent-primary" 
              />
            </div>
            
            <div>
              <div className="flex justify-between mb-1">
                <label className="text-sm">Interest Rate</label>
                <span className="text-sm font-bold">{params.interestRate}%</span>
              </div>
              <input 
                type="range" min="4" max="24" step="0.5"
                value={params.interestRate}
                onChange={(e) => setParams({...params, interestRate: Number(e.target.value)})}
                className="w-full accent-primary" 
              />
            </div>

            <div>
              <div className="flex justify-between mb-1">
                <label className="text-sm">Revenue Growth (YoY)</label>
                <span className="text-sm font-bold">{params.revenueGrowth}%</span>
              </div>
              <input 
                type="range" min="-50" max="100" step="1"
                value={params.revenueGrowth}
                onChange={(e) => setParams({...params, revenueGrowth: Number(e.target.value)})}
                className="w-full accent-primary" 
              />
            </div>

            <div>
              <div className="flex justify-between mb-1">
                <label className="text-sm">Cash Flow Change</label>
                <span className="text-sm font-bold">{params.cashFlowChange}%</span>
              </div>
              <input 
                type="range" min="-30" max="30" step="1"
                value={params.cashFlowChange}
                onChange={(e) => setParams({...params, cashFlowChange: Number(e.target.value)})}
                className="w-full accent-primary" 
              />
            </div>
          </div>

          <button 
            onClick={runSimulation}
            disabled={loading}
            className="w-full mt-6 inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors bg-primary text-primary-foreground hover:bg-primary/90 h-10 py-2 px-4"
          >
            {loading ? "Simulating..." : "Run Simulation"}
            {!loading && <Play className="ml-2 h-4 w-4" />}
          </button>
        </div>

        <div className="lg:col-span-8 glass-panel p-6 rounded-xl flex flex-col justify-center">
          <h2 className="text-xl font-semibold mb-6">Simulation Result</h2>
          
          <div className={`transition-opacity duration-500 ${loading ? 'opacity-30' : 'opacity-100'}`}>
            <div className="grid grid-cols-2 gap-6 mb-8">
              <div className="p-6 bg-white/5 border border-white/10 rounded-lg text-center">
                <p className="text-sm text-muted-foreground mb-2">Simulated Decision</p>
                <p className={`text-4xl font-bold ${params.interestRate > 15 || params.revenueGrowth < -10 ? 'text-red-400' : 'text-green-400'}`}>
                  {params.interestRate > 15 || params.revenueGrowth < -10 ? 'REJECTED' : 'APPROVED'}
                </p>
              </div>
              <div className="p-6 bg-white/5 border border-white/10 rounded-lg text-center">
                <p className="text-sm text-muted-foreground mb-2">Adjusted Health Score</p>
                <p className="text-4xl font-bold text-blue-400">
                  {Math.max(10, Math.min(99, 85 + params.revenueGrowth/5 - (params.interestRate-8.5)*2)).toFixed(0)}
                </p>
              </div>
            </div>

            <div className="space-y-3">
              <h3 className="font-medium text-muted-foreground">Simulation Insights</h3>
              {(params.interestRate > 15) && (
                <div className="p-3 bg-red-500/10 border border-red-500/30 rounded text-red-200 text-sm">
                  High interest rate severely impacts debt service coverage ratio. Default risk elevated.
                </div>
              )}
              {(params.revenueGrowth < 0) && (
                <div className="p-3 bg-yellow-500/10 border border-yellow-500/30 rounded text-yellow-200 text-sm">
                  Negative revenue growth trajectory triggers early warning system. Re-evaluate loan principal.
                </div>
              )}
              {(params.interestRate <= 15 && params.revenueGrowth >= 0) && (
                <div className="p-3 bg-green-500/10 border border-green-500/30 rounded text-green-200 text-sm">
                  Portfolio parameters remain within acceptable risk thresholds.
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
