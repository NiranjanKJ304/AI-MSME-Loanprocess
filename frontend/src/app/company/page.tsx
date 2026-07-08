"use client";

import { useState } from "next";
import { useRouter } from "next/navigation";
import { Building2, Save } from "lucide-react";

export default function CompanyPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    // Mock save, then navigate
    setTimeout(() => {
      router.push("/financial-data");
    }, 1000);
  };

  return (
    <div className="space-y-8 animate-in fade-in duration-700">
      <div className="flex items-center gap-4">
        <div className="h-12 w-12 rounded-xl bg-blue-500/20 flex items-center justify-center text-blue-400">
          <Building2 size={24} />
        </div>
        <div>
          <h1 className="text-3xl font-bold">Company Profile</h1>
          <p className="text-muted-foreground mt-1">Create an MSME profile to begin the AI decision workflow.</p>
        </div>
      </div>

      <div className="glass-panel rounded-xl p-8 max-w-2xl">
        <form onSubmit={handleSubmit} className="space-y-6">
          <div className="space-y-2">
            <label className="text-sm font-medium">Company Name</label>
            <input 
              required
              className="flex h-10 w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
              placeholder="Acme Manufacturing Ltd." 
            />
          </div>
          
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Industry</label>
              <select className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2">
                <option>Manufacturing</option>
                <option>Retail</option>
                <option>Technology</option>
                <option>Services</option>
              </select>
            </div>
            
            <div className="space-y-2">
              <label className="text-sm font-medium">Annual Revenue (USD)</label>
              <input 
                type="number"
                required
                className="flex h-10 w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                placeholder="500000" 
              />
            </div>
          </div>

          <div className="p-4 bg-white/5 rounded-lg border border-white/10 mt-6 flex items-start gap-3">
            <input type="checkbox" required className="mt-1" />
            <div className="text-sm">
              <p className="font-medium">Consent to Data Access</p>
              <p className="text-muted-foreground">I authorize LendingMind AI to access alternative financial data (GST, UPI, Banking) for credit assessment.</p>
            </div>
          </div>

          <button 
            type="submit"
            disabled={loading}
            className="w-full inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-50 disabled:pointer-events-none bg-primary text-primary-foreground hover:bg-primary/90 h-10 py-2 px-4"
          >
            {loading ? "Saving Profile..." : "Save & Continue to Data Import"}
            {!loading && <Save className="ml-2 h-4 w-4" />}
          </button>
        </form>
      </div>
    </div>
  );
}
