"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { FileSpreadsheet, CheckCircle2, Loader2, Play } from "lucide-react";

export default function FinancialDataPage() {
  const router = useRouter();
  const [status, setStatus] = useState<"idle" | "importing" | "done">("idle");

  const startImport = () => {
    setStatus("importing");
    setTimeout(() => {
      setStatus("done");
    }, 2500);
  };

  const runPipeline = () => {
    router.push("/pipeline");
  };

  return (
    <div className="space-y-8 animate-in fade-in duration-700">
      <div className="flex items-center gap-4">
        <div className="h-12 w-12 rounded-xl bg-green-500/20 flex items-center justify-center text-green-400">
          <FileSpreadsheet size={24} />
        </div>
        <div>
          <h1 className="text-3xl font-bold">Financial Data Aggregation</h1>
          <p className="text-muted-foreground mt-1">Mocking alternative data import (GST, UPI, Payroll)</p>
        </div>
      </div>

      <div className="glass-panel rounded-xl p-8 max-w-3xl">
        <div className="space-y-6">
          
          <div className="flex items-center justify-between p-4 bg-white/5 border border-white/10 rounded-lg">
            <div>
              <p className="font-semibold text-lg">GST Filing Data</p>
              <p className="text-sm text-muted-foreground">Historical GSTR-1 & GSTR-3B records</p>
            </div>
            {status === "idle" && <div className="text-sm text-muted-foreground">Pending</div>}
            {status === "importing" && <Loader2 className="animate-spin text-blue-400" />}
            {status === "done" && <CheckCircle2 className="text-green-400" />}
          </div>

          <div className="flex items-center justify-between p-4 bg-white/5 border border-white/10 rounded-lg">
            <div>
              <p className="font-semibold text-lg">UPI Transaction Streams</p>
              <p className="text-sm text-muted-foreground">Real-time digital payment inflows</p>
            </div>
            {status === "idle" && <div className="text-sm text-muted-foreground">Pending</div>}
            {status === "importing" && <Loader2 className="animate-spin text-blue-400" />}
            {status === "done" && <CheckCircle2 className="text-green-400" />}
          </div>

          <div className="flex items-center justify-between p-4 bg-white/5 border border-white/10 rounded-lg">
            <div>
              <p className="font-semibold text-lg">Banking & Payroll</p>
              <p className="text-sm text-muted-foreground">Cash flow statements and salary disbursals</p>
            </div>
            {status === "idle" && <div className="text-sm text-muted-foreground">Pending</div>}
            {status === "importing" && <Loader2 className="animate-spin text-blue-400" />}
            {status === "done" && <CheckCircle2 className="text-green-400" />}
          </div>
          
        </div>

        <div className="mt-8 flex justify-end">
          {status === "idle" && (
            <button 
              onClick={startImport}
              className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors bg-secondary text-secondary-foreground hover:bg-secondary/80 h-10 py-2 px-4"
            >
              Start Data Import
            </button>
          )}
          
          {status === "done" && (
            <button 
              onClick={runPipeline}
              className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors bg-primary text-primary-foreground hover:bg-primary/90 h-10 py-2 px-4"
            >
              Execute AI Pipeline
              <Play className="ml-2 h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
