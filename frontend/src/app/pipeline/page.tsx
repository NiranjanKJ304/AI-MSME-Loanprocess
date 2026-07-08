"use client";

import { useEffect, useState } from "next";
import { useRouter } from "next/navigation";
import ReactFlow, { Background, Controls, Edge, Node } from "reactflow";
import "reactflow/dist/style.css";
import { GitBranch, ArrowRight } from "lucide-react";

const initialNodes: Node[] = [
  { id: "1", position: { x: 50, y: 150 }, data: { label: "Financial Data" }, type: "input" },
  { id: "2", position: { x: 250, y: 150 }, data: { label: "Feature Engineering" } },
  { id: "3", position: { x: 500, y: 50 }, data: { label: "Health Agent" } },
  { id: "4", position: { x: 500, y: 150 }, data: { label: "Fraud Agent" } },
  { id: "5", position: { x: 500, y: 250 }, data: { label: "Forecast Agent" } },
  { id: "6", position: { x: 750, y: 150 }, data: { label: "Decision Coordinator" } },
];

const initialEdges: Edge[] = [
  { id: "e1-2", source: "1", target: "2", animated: true },
  { id: "e2-3", source: "2", target: "3", animated: true },
  { id: "e2-4", source: "2", target: "4", animated: true },
  { id: "e2-5", source: "2", target: "5", animated: true },
  { id: "e3-6", source: "3", target: "6", animated: true },
  { id: "e4-6", source: "4", target: "6", animated: true },
  { id: "e5-6", source: "5", target: "6", animated: true },
];

export default function PipelinePage() {
  const router = useRouter();
  const [pipelineFinished, setPipelineFinished] = useState(false);

  useEffect(() => {
    // Simulate pipeline run time
    const timer = setTimeout(() => {
      setPipelineFinished(true);
    }, 4000);
    return () => clearTimeout(timer);
  }, []);

  return (
    <div className="space-y-6 animate-in fade-in h-[85vh] flex flex-col">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className="h-12 w-12 rounded-xl bg-purple-500/20 flex items-center justify-center text-purple-400">
            <GitBranch size={24} />
          </div>
          <div>
            <h1 className="text-3xl font-bold">AI Decision Pipeline</h1>
            <p className="text-muted-foreground mt-1">Executing multi-agent framework</p>
          </div>
        </div>

        {pipelineFinished && (
          <button 
            onClick={() => router.push("/decision-center")}
            className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors bg-primary text-primary-foreground hover:bg-primary/90 h-10 py-2 px-4 animate-in slide-in-from-right-4"
          >
            View Decision Results
            <ArrowRight className="ml-2 h-4 w-4" />
          </button>
        )}
      </div>

      <div className="flex-1 glass-panel rounded-xl overflow-hidden relative">
        <ReactFlow 
          nodes={initialNodes.map(n => ({...n, style: { background: '#1e1e2d', color: '#fff', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', padding: '10px' }}))} 
          edges={initialEdges.map(e => ({...e, style: { stroke: pipelineFinished ? '#4ade80' : '#8b5cf6', strokeWidth: 2 }}))} 
          fitView
        >
          <Background color="#444" gap={16} />
          <Controls />
        </ReactFlow>
      </div>
    </div>
  );
}
