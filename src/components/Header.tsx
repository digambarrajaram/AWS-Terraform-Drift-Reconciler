import { Cloud, RefreshCw, Terminal, Activity, Sparkles } from 'lucide-react';

interface HeaderProps {
  scanning: boolean;
  onScan: () => void;
  onReset: () => void;
  lastScanTime: string | null;
  driftCount: number;
}

export default function Header({ scanning, onScan, onReset, lastScanTime, driftCount }: HeaderProps) {
  const formattedTime = lastScanTime
    ? new Date(lastScanTime).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : 'Never';

  return (
    <header className="border-b border-slate-800 bg-slate-950 px-6 py-4">
      <div className="mx-auto flex max-w-7xl flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">

        <div className="flex items-center gap-3">
          <div className="relative flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-tr from-blue-600 to-indigo-600 text-white shadow-lg shadow-blue-500/10">
            <Terminal className="h-5 w-5" />
            <div className="absolute -right-0.5 -top-0.5 h-3 w-3 rounded-full border-2 border-slate-950 bg-green-500 animate-pulse" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="font-display text-lg font-bold tracking-tight text-white">IaC Drift Reconciler</h1>
              <span className="rounded-full bg-green-500/10 px-2 py-0.5 font-mono text-[10px] font-semibold tracking-wider text-green-400 border border-green-500/20 uppercase">
                Live
              </span>
            </div>
            <p className="text-xs text-slate-400">Terraform Drift Detection & Automated Remediation</p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 sm:gap-4">
          <div className="hidden rounded-lg bg-slate-900 px-3 py-1.5 border border-slate-800/80 md:flex items-center gap-2">
            <Cloud className="h-4 w-4 text-blue-400" />
            <div className="text-left">
              <div className="font-mono text-[10px] uppercase tracking-wider text-slate-500 font-bold">AWS</div>
              <div className="font-mono text-xs text-slate-300">us-east-1</div>
            </div>
          </div>

          <div className="rounded-lg bg-slate-900 px-3 py-1.5 border border-slate-800/80 flex items-center gap-2">
            <div className="flex h-2 w-2 items-center justify-center">
              <div className="h-1.5 w-1.5 rounded-full bg-green-400" />
            </div>
            <div className="text-left">
              <div className="font-mono text-[10px] uppercase tracking-wider text-slate-500 font-bold">Pipeline</div>
              <div className="font-mono text-xs text-slate-300 flex items-center gap-1">
                Active <Sparkles className="h-3 w-3 text-indigo-400" />
              </div>
            </div>
          </div>

          <div className="rounded-lg bg-slate-900 px-3 py-1.5 border border-slate-800/80 flex items-center gap-2">
            <Activity className="h-4 w-4 text-indigo-400" />
            <div className="text-left">
              <div className="font-mono text-[10px] uppercase tracking-wider text-slate-500 font-bold">Last Scan</div>
              <div className="font-mono text-xs text-slate-300">{formattedTime}</div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button id="btn-scan" onClick={onScan} disabled={scanning}
              className={`flex items-center gap-2 rounded-lg px-4 py-2 font-medium text-xs tracking-wide transition-all ${
                scanning ? 'bg-slate-800 text-slate-500 cursor-not-allowed'
                         : 'bg-blue-600 text-white hover:bg-blue-500 hover:shadow-lg hover:shadow-blue-500/10 active:scale-95'
              }`}>
              <RefreshCw className={`h-3.5 w-3.5 ${scanning ? 'animate-spin text-slate-500' : ''}`} />
              {scanning ? 'Running plan...' : 'Scan Env'}
            </button>
            <button id="btn-reset" onClick={onReset} title="Reset state"
              className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-800 bg-slate-950 text-slate-400 hover:bg-slate-900 hover:text-white transition-colors">
              <RefreshCw className="h-3.5 w-3.5 text-slate-400 hover:rotate-180 transition-transform duration-500" />
            </button>
          </div>
        </div>
      </div>
    </header>
  );
}
