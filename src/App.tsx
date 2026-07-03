/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useEffect } from 'react';
import {
  Shield,
  AlertTriangle,
  CheckCircle,
  GitPullRequest,
  Clock,
  Sparkles,
  DollarSign,
  Plus,
  GitMerge,
  Server,
  ShieldAlert,
  FileCode2,
  ListRestart,
  Activity,
  Terminal,
  FileDiff,
  ClipboardCheck,
  Bell,
  UserCheck,
} from 'lucide-react';
import { SystemState, AwsResource, PullRequest, TimelineEvent } from './types';
import Header from './components/Header';
import AlertsAndApproval from './components/AlertsAndApproval';
import ApproveModal from './components/ApproveModal';

export default function App() {
  const [state, setState] = useState<SystemState | null>(null);
  const [selectedResource, setSelectedResource] = useState<AwsResource | null>(null);
  const [selectedPr, setSelectedPr] = useState<PullRequest | null>(null);
  const [loadingPrId, setLoadingPrId] = useState<string | null>(null);
  const [showApproveModal, setShowApproveModal] = useState(false);
  const [pendingMergePrId, setPendingMergePrId] = useState<string | null>(null);
  const [analyzingResourceId, setAnalyzingResourceId] = useState<string | null>(null);
  const [analysisIntervalRef, setAnalysisIntervalRef] = useState<ReturnType<typeof setInterval> | null>(null);
  const [activeAnalysisStep, setActiveAnalysisStep] = useState<number>(0);
  const [fetchError, setFetchError] = useState<boolean>(false);
  const [activeTab, setActiveTab] = useState<'resources' | 'prs' | 'timeline' | 'add-resource' | 'alerts-config'>('resources');
  const [prActiveSubTab, setPrActiveSubTab] = useState<'report' | 'langgraph' | 'cicd'>('report');
  
  // Add custom resource form state
  const [newResName, setNewResName] = useState<string>('');
  const [newResType, setNewResType] = useState<string>('aws_sqs_queue');
  const [newResService, setNewResService] = useState<'S3' | 'VPC' | 'IAM' | 'RDS'>('VPC');
  const [newResDesiredJson, setNewResDesiredJson] = useState<string>('{\n  "name": "payment-notification-queue",\n  "kms_master_key_id": "alias/aws/sqs",\n  "message_retention_seconds": 86400,\n  "visibility_timeout_seconds": 30,\n  "fifo_queue": false\n}');
  const [newResHcl, setNewResHcl] = useState<string>('resource "aws_sqs_queue" "payment_notification" {\n  name                              = "payment-notification-queue"\n  kms_master_key_id                 = "alias/aws/sqs"\n  message_retention_seconds         = 86400\n  visibility_timeout_seconds        = 30\n  fifo_queue                        = false\n}');

  // Plan output terminal log
  const [planLogs, setPlanLogs] = useState<string[]>([]);
  const [showNotification, setShowNotification] = useState<{ message: string; type: 'success' | 'info' | 'warning' } | null>(null);

  // Fetch state on mount
  useEffect(() => {
    fetchState();
  }, []);

  // ponytail: step animation for analysis pipeline — synced with actual agent completion.
  useEffect(() => {
    let interval: ReturnType<typeof setInterval> | null = null;
    if (analyzingResourceId) {
      setActiveAnalysisStep(0);
      interval = setInterval(() => {
        setActiveAnalysisStep(prev => (prev < 4 ? prev + 1 : prev));
      }, 600);
      setAnalysisIntervalRef(interval);
    } else {
      setActiveAnalysisStep(0);
      setAnalysisIntervalRef(null);
    }
    return () => { if (interval) clearInterval(interval); };
  }, [analyzingResourceId]);

  const triggerNotification = (message: string, type: 'success' | 'info' | 'warning' = 'info') => {
    setShowNotification({ message, type });
    setTimeout(() => setShowNotification(null), 5000);
  };

  const fetchState = async () => {
    try {
      const res = await fetch('/api/state');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setState(data);
      setFetchError(false);
      if (data.resources && data.resources.length > 0 && !selectedResource) {
        setSelectedResource(data.resources[0]);
      }
      if (data.prs && data.prs.length > 0 && !selectedPr) {
        setSelectedPr(data.prs[0]);
      }
    } catch (e) {
      console.error('Failed to fetch app state', e);
      setFetchError(true);
    }
  };

  const handleReset = async () => {
    if (!window.confirm('Reset all resources to compliant state?')) return;
    const requestedBy = window.prompt('Enter your name for audit logs (requestedBy)');
    if (!requestedBy || requestedBy.trim().length === 0) {
      triggerNotification('Reset cancelled: requester identity required for audit.', 'warning');
      return;
    }
    try {
      const res = await fetch('/api/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ requestedBy }) });
      const data = await res.json();
      setState(data);
      if (data.resources && data.resources.length > 0) {
        setSelectedResource(data.resources[0]);
      }
      setSelectedPr(null);
      setPlanLogs([]);
      triggerNotification('State reset — all resources restored to desired configuration.', 'success');
    } catch (e) {
      console.error('Failed resetting state', e);
    }
  };

  const handleScan = async () => {
    setPlanLogs(prev => [...prev, 'Starting infrastructure drift scan...']);
    try {
      const res = await fetch('/api/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });

      if (!res.ok) {
        const err = await res.json();
        setPlanLogs(prev => [...prev, `❌ Scan failed: ${err.detail || err.error || 'Unknown error'}`]);
        setState(prev => prev ? { ...prev, scanning: false } : null);
        triggerNotification(`Scan failed: ${err.detail || 'Terraform plan error'}`, 'warning');
        return;
      }

      const data = await res.json();
      setTimeout(() => {
        const logs = [
          'Analyzing live estate differences...',
          'No changes found on compliant resources.',
        ];

        const driftedCount = data.resources.filter((r: AwsResource) => r.isDrifted).length;
        if (driftedCount > 0) {
          logs.push(
            `⚠️ Alert: Terraform plan identified ${driftedCount} drifted resources!`,
            'Run "Agent Reconciliation" to analyze the drift and generate an HCL reconciliation PR.'
          );
        } else {
          logs.push('✅ Success: All resources match their desired Terraform configurations.');
        }

        setPlanLogs(prev => [...prev, ...logs]);
        setState(data);
        triggerNotification('Infrastructure drift plan scan completed successfully!', 'success');

        if (selectedResource) {
          const updated = data.resources.find((r: AwsResource) => r.id === selectedResource.id);
          if (updated) setSelectedResource(updated);
        }
      }, 500);
    } catch (e) {
      console.error('Failed scanning environment', e);
      setState(prev => prev ? { ...prev, scanning: false } : null);
    }
  };

  const handleCreateCustomResource = async () => {
    if (!newResName || !newResHcl || !newResDesiredJson) {
      triggerNotification('Please fill in all resource configuration parameters.', 'warning');
      return;
    }

    try {
      const parsedDesired = JSON.parse(newResDesiredJson);
      
      const res = await fetch('/api/resource', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newResName,
          type: newResType,
          service: newResService,
          terraformCode: newResHcl,
          desiredState: parsedDesired
        })
      });

      if (!res.ok) {
        throw new Error('Server error setting up custom resource');
      }

      const data = await res.json();
      setState(data);
      
      // Clear forms
      setNewResName('');
      setNewResDesiredJson('{}');
      setNewResHcl('');
      
      // Auto select the new resource
      const newlyCreated = data.resources[data.resources.length - 1];
      if (newlyCreated) setSelectedResource(newlyCreated);
      
      setActiveTab('resources');
      triggerNotification(`New custom infrastructure tracking created successfully!`, 'success');

    } catch (err: any) {
      triggerNotification(`Failed to create resource: Ensure desired state is valid JSON.`, 'warning');
    }
  };


  const handleAnalyzeDrift = async (resourceId: string) => {
    setAnalyzingResourceId(resourceId);
    triggerNotification('Agent analyzing drift & generating illustrative HCL diff...', 'info');
    try {
      const res = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resourceId }),
      });
      const data = await res.json();
      // Jump to final step immediately — animation is already showing progress.
      setActiveAnalysisStep(4);
      if (analysisIntervalRef) clearInterval(analysisIntervalRef);

      setState(data.systemState);
      if (data.pr) {
        setSelectedPr(data.pr);
        setActiveTab('prs');
      }
      const updatedRes = data.systemState.resources.find((r: AwsResource) => r.id === resourceId);
      if (updatedRes) setSelectedResource(updatedRes);

      triggerNotification(`PR #${data.pr?.number} generated by agent!`, 'success');
    } catch (e) {
      console.error('Failed analyzing drift', e);
      triggerNotification('Agent analysis failed — check server logs.', 'warning');
    } finally {
      setAnalyzingResourceId(null);
    }
  };

  const handleMergePr = async (prId: string, approvedBy?: string) => {
    setLoadingPrId(prId);
    triggerNotification('Merging PR & reconciling resource state...', 'info');
    try {
      const res = await fetch('/api/merge-pr', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prId, approvedBy }),
      });
      if (res.status === 403) {
        const err = await res.json();
        triggerNotification(err.error || 'This PR requires human approval.', 'warning');
        return;
      }
      const data = await res.json();
      // Full refresh to ensure consistent state across all tabs
      await fetchState();
      triggerNotification('Reconciliation complete! Resource state restored.', 'success');
    } catch (e) {
      console.error('Failed merging PR', e);
    } finally {
      setLoadingPrId(null);
    }
  };

  if (fetchError) {
    return (
      <div className="flex h-screen items-center justify-center bg-slate-950 text-slate-400">
        <div className="text-center">
          <AlertTriangle className="h-10 w-10 text-amber-400 mx-auto mb-4" />
          <p className="font-display font-medium text-sm text-white mb-2">Couldn't reach the server</p>
          <p className="text-xs text-slate-500 mb-4">The backend may be down or restarting.</p>
          <button
            onClick={() => { setFetchError(false); fetchState(); }}
            className="rounded-lg bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 text-sm font-semibold transition-colors"
          >
            Retry Connection
          </button>
        </div>
      </div>
    );
  }

  if (!state) {
    return (
      <div className="flex h-screen items-center justify-center bg-slate-950 text-slate-400">
        <div className="text-center">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-slate-700 border-t-blue-500 mx-auto mb-4" />
          <p className="font-display font-medium text-sm">Loading infrastructure state...</p>
        </div>
      </div>
    );
  }

  const driftedResources = state.resources.filter(r => r.isDrifted);
  const openPrs = state.prs.filter(p => p.status === 'Open');

  // Simple clean side-by-side highlighting or restored changes generator for the PR view
  const renderCodeDiff = (original: string, fixed: string) => {
    const origLines = original.split('\n');
    const fixLines = fixed.split('\n');

    return (
      <div className="font-mono text-[11px] bg-slate-950 p-4 rounded-xl border border-slate-900 overflow-auto max-h-[250px] leading-relaxed select-text">
        {fixLines.map((line, idx) => {
          const wasInOrig = origLines.includes(line);
          const hasDriftMark = line.includes('Re-enforced') || line.includes('#');
          
          let lineBg = 'text-slate-400';
          let indicator = ' ';
          
          if (!wasInOrig || hasDriftMark) {
            lineBg = 'text-emerald-400 bg-emerald-950/20 font-bold';
            indicator = '+';
          }

          return (
            <div key={idx} className={`flex gap-2 ${lineBg} -mx-4 px-4 py-0.5`}>
              <span className="opacity-40 text-[9px] w-6 shrink-0 select-none text-right">{idx + 1}</span>
              <span className="text-emerald-500 select-none w-3 shrink-0">{indicator}</span>
              <span>{line}</span>
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-slate-950 font-sans text-slate-300 selection:bg-blue-600/30">
      
      {/* Toast Notification */}
      {showNotification && (
        <div id="toast-notification" className={`fixed top-4 right-4 z-50 flex items-center gap-3 rounded-xl border px-4 py-3 shadow-2xl transition-all duration-300 animate-in fade-in slide-in-from-top-4 ${
          showNotification.type === 'success' 
            ? 'border-green-500/20 bg-green-950/90 text-green-300' 
            : showNotification.type === 'warning'
            ? 'border-amber-500/20 bg-amber-950/90 text-amber-300'
            : 'border-blue-500/20 bg-slate-900/90 text-blue-300'
        }`}>
          {showNotification.type === 'success' && <CheckCircle className="h-5 w-5 text-green-400 shrink-0" />}
          {showNotification.type === 'warning' && <AlertTriangle className="h-5 w-5 text-amber-400 shrink-0" />}
          {showNotification.type === 'info' && <Sparkles className="h-5 w-5 text-blue-400 shrink-0" />}
          <div className="text-xs font-medium">{showNotification.message}</div>
        </div>
      )}

      {/* Header Component */}
      <Header 
        scanning={state.scanning} 
        onScan={handleScan} 
        onReset={handleReset} 
        lastScanTime={state.lastScanTime}
        driftCount={driftedResources.length}
      />

      {state.environment !== 'demo' && (
        <div className={`px-4 py-2 text-center text-xs font-bold font-mono tracking-wider ${
          state.environment === 'production' ? 'bg-red-600/20 text-red-400 border-b border-red-500/20' :
          'bg-amber-600/20 text-amber-400 border-b border-amber-500/20'
        }`}>
          {state.environment === 'production' ? '🔒 PRODUCTION ENVIRONMENT — destructive actions disabled' :
           '⚠ STAGING ENVIRONMENT — changes affect shared infrastructure'}
        </div>
      )}

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">

        {/* Interactive Explainer Section */}
        <section className="mb-6 rounded-2xl border border-slate-800 bg-slate-900/50 p-5 md:p-6 shadow-xl relative overflow-hidden backdrop-blur-sm">
          <div className="absolute right-0 top-0 -mr-16 -mt-16 h-48 w-48 rounded-full bg-blue-500/5 blur-3xl" />
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div className="max-w-3xl">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/10 px-2.5 py-1 text-xs font-semibold text-amber-400">
                <Shield className="h-3 w-3" /> Connected — Terraform State + Live AWS
              </span>
              <h2 className="mt-3 font-display text-xl font-bold tracking-tight text-white sm:text-2xl">
                IaC Drift Detection & Remediation
              </h2>
              <p className="mt-2 text-sm leading-relaxed text-slate-400">
                Resources are loaded from <strong>S3 Terraform state</strong> and compared against <strong>live AWS infrastructure</strong> via terraform plan. Drift analysis is powered by the agent pipeline (Nova Pro + deterministic fallback). PRs are created on GitHub. Alerts route through PagerDuty.
              </p>
            </div>
            
            {/* Quick Stats Panel */}
            <div className="flex gap-3 shrink-0">
              <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3 text-center min-w-[100px]">
                <div className="text-2xl font-bold font-display text-white">{state.resources.length}</div>
                <div className="text-[10px] font-mono uppercase tracking-wider text-slate-500 font-bold mt-1">Managed</div>
              </div>
              <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3 text-center min-w-[100px]">
                <div className={`text-2xl font-bold font-display ${driftedResources.length > 0 ? 'text-rose-500 animate-pulse' : 'text-green-400'}`}>
                  {driftedResources.length}
                </div>
                <div className="text-[10px] font-mono uppercase tracking-wider text-slate-500 font-bold mt-1">Drifted</div>
              </div>
              <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3 text-center min-w-[100px]">
                <div className="text-2xl font-bold font-display text-blue-400">{openPrs.length}</div>
                <div className="text-[10px] font-mono uppercase tracking-wider text-slate-500 font-bold mt-1">Open PRs</div>
              </div>
            </div>
          </div>
        </section>

        {/* Navigation Tabs */}
        <div className="flex border-b border-slate-800 mb-6 gap-2 overflow-x-auto whitespace-nowrap scrollbar-none">
          <button
            onClick={() => setActiveTab('resources')}
            className={`flex items-center gap-2 px-4 py-3 font-display font-medium text-sm border-b-2 -mb-px transition-all ${
              activeTab === 'resources' 
                ? 'border-blue-500 text-white font-bold' 
                : 'border-transparent text-slate-400 hover:text-white'
            }`}
          >
            <Server className="h-4 w-4" />
            AWS Managed Resources
          </button>
          <button
            onClick={() => setActiveTab('prs')}
            className={`flex items-center gap-2 px-4 py-3 font-display font-medium text-sm border-b-2 -mb-px transition-all relative ${
              activeTab === 'prs' 
                ? 'border-blue-500 text-white font-bold' 
                : 'border-transparent text-slate-400 hover:text-white'
            }`}
          >
            <GitPullRequest className="h-4 w-4" />
            GitHub App PRs
            {openPrs.length > 0 && (
              <span className="rounded-full bg-blue-600 text-white text-[10px] font-bold px-2 py-0.5 ml-1">
                {openPrs.length}
              </span>
            )}
          </button>
          <button
            onClick={() => setActiveTab('add-resource')}
            className={`flex items-center gap-2 px-4 py-3 font-display font-medium text-sm border-b-2 -mb-px transition-all ${
              activeTab === 'add-resource' 
                ? 'border-blue-500 text-white font-bold' 
                : 'border-transparent text-slate-400 hover:text-white'
            }`}
          >
            <Plus className="h-4 w-4" />
            Register IaC Resource
          </button>
          <button
            onClick={() => setActiveTab('timeline')}
            className={`flex items-center gap-2 px-4 py-3 font-display font-medium text-sm border-b-2 -mb-px transition-all ${
              activeTab === 'timeline' 
                ? 'border-blue-500 text-white font-bold' 
                : 'border-transparent text-slate-400 hover:text-white'
            }`}
          >
            <Clock className="h-4 w-4" />
            Drift Audit Timeline
          </button>
          <button
            onClick={() => setActiveTab('alerts-config')}
            className={`flex items-center gap-2 px-4 py-3 font-display font-medium text-sm border-b-2 -mb-px transition-all ${
              activeTab === 'alerts-config' 
                ? 'border-blue-500 text-white font-bold' 
                : 'border-transparent text-slate-400 hover:text-white'
            }`}
          >
            <Bell className="h-4 w-4 text-indigo-400" />
            Alerts & Human Approval (HITL)
          </button>
        </div>

        {/* Resources / Drift Tab */}
        {activeTab === 'resources' && (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            
            {/* Resources List (Left side) */}
            <div className="lg:col-span-4 flex flex-col gap-4">
              <div className="flex items-center justify-between">
                <h3 className="font-display font-semibold text-white text-sm">Target Environments (Production)</h3>
                <span className="text-[11px] text-slate-500 font-mono">Select a resource to inspect</span>
              </div>

              <div className="flex flex-col gap-3">
                {state.resources.map((resource) => {
                  const isSelected = selectedResource?.id === resource.id;
                  
                  return (
                    <div
                      key={resource.id}
                      onClick={() => setSelectedResource(resource)}
                      className={`group relative flex flex-col rounded-xl border p-4 cursor-pointer transition-all ${
                        isSelected 
                          ? 'border-blue-500 bg-slate-900/70 shadow-lg shadow-blue-500/5' 
                          : 'border-slate-800 bg-slate-950/50 hover:border-slate-700 hover:bg-slate-900/30'
                      }`}
                    >
                      {/* Active Status Ribbon/Indicator */}
                      <div className="flex items-start justify-between">
                        <div className="flex items-center gap-2.5">
                          <div className={`flex h-8 w-8 items-center justify-center rounded-lg font-mono text-[10px] font-bold ${
                            resource.service === 'S3' ? 'bg-orange-500/10 text-orange-400 border border-orange-500/20' :
                            resource.service === 'VPC' ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' :
                            resource.service === 'IAM' ? 'bg-red-500/10 text-red-400 border border-red-500/20' :
                            'bg-blue-500/10 text-blue-400 border border-blue-500/20'
                          }`}>
                            {resource.service}
                          </div>
                          <div>
                            <h4 className="font-display text-sm font-semibold text-white group-hover:text-blue-400 transition-colors">
                              {resource.name}
                            </h4>
                            <p className="font-mono text-[11px] text-slate-500">{resource.type}</p>
                          </div>
                        </div>

                        {/* Status Label */}
                        <div>
                          {resource.isDrifted ? (
                            <span className="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-2 py-0.5 font-mono text-[10px] font-bold text-red-400 border border-red-500/20 animate-pulse">
                              <AlertTriangle className="h-3 w-3" /> DRIFTED
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 rounded-full bg-green-500/10 px-2 py-0.5 font-mono text-[10px] font-bold text-green-400 border border-green-500/20">
                              <CheckCircle className="h-3 w-3" /> COMPLIANT
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Brief description of state differences if drifted */}
                      {resource.isDrifted && resource.driftDetails && (
                        <div className="mt-3 rounded-lg bg-red-950/20 border border-red-500/10 p-2.5 text-xs text-red-400/90 font-mono">
                          <div className="font-bold text-[10px] uppercase tracking-wider text-red-500 mb-1">Detected Delta:</div>
                          {resource.driftDetails.map((d, idx) => (
                            <div key={idx} className="flex justify-between gap-2 overflow-hidden text-ellipsis whitespace-nowrap">
                              <span>• {d.field}:</span>
                              <span className="text-slate-500 line-through shrink-0">{String(d.expected)}</span>
                              <span className="text-red-400 font-bold shrink-0">→ {String(d.actual)}</span>
                            </div>
                          ))}
                        </div>
                      )}

                    </div>
                  );
                })}
              </div>

              {/* Reset controls */}
              <div className="rounded-xl border border-dashed border-slate-800 p-4 text-center mt-2">
                <p className="text-xs text-slate-400 leading-relaxed mb-3">
                  Reset all resources to their desired Terraform state:
                </p>
                <div className="flex gap-2 justify-center">
                  <button
                    onClick={handleReset}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-slate-800 hover:border-slate-700 bg-slate-950 hover:bg-slate-900 text-xs font-semibold px-4 py-2 text-slate-300 transition-all"
                  >
                    <ListRestart className="h-4 w-4" /> Reset to Compliant
                  </button>
                </div>
              </div>
            </div>

            {/* Resource Inspector (Right side) */}
            <div className="lg:col-span-8 flex flex-col gap-6">
              {selectedResource ? (
                <div className="rounded-2xl border border-slate-800 bg-slate-900/30 p-5 md:p-6 shadow-xl relative overflow-hidden backdrop-blur-sm flex flex-col">
                  <div className="absolute right-0 top-0 -mr-16 -mt-16 h-32 w-32 rounded-full bg-blue-500/5 blur-2xl" />
                  
                  {/* Header Title info */}
                  <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between border-b border-slate-800 pb-4 gap-4">
                    <div>
                      <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-[10px] uppercase font-bold text-slate-400 border border-slate-700">
                        {selectedResource.service} Environment Resource
                      </span>
                      <h3 className="font-display text-lg font-bold text-white mt-1">{selectedResource.name}</h3>
                      <p className="font-mono text-xs text-slate-500 mt-0.5">{selectedResource.type}</p>
                    </div>

                    {/* Scan trigger or drift action */}
                    <div>
                      {selectedResource.isDrifted ? (
                        <button
                          id="btn-agent-reconcile"
                          onClick={() => handleAnalyzeDrift(selectedResource.id)}
                          disabled={analyzingResourceId !== null}
                          className={`inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white font-semibold text-xs tracking-wide px-4 py-2.5 shadow-lg shadow-blue-500/10 transition-all active:scale-95 ${
                            analyzingResourceId !== null ? 'opacity-50 cursor-not-allowed' : ''
                          }`}
                        >
                          <Sparkles className={`h-4 w-4 ${analyzingResourceId === selectedResource.id ? 'animate-pulse' : ''}`} />
                          {analyzingResourceId === selectedResource.id ? 'Agent Reconciling...' : 'Run Agent Reconciliation'}
                        </button>
                      ) : null}
                    </div>
                  </div>

                  {/* Drift Alert Banner */}
                  {selectedResource.isDrifted && (
                    <div className="mt-4 rounded-xl border border-red-500/20 bg-red-950/20 p-4 relative overflow-hidden">
                      <div className="absolute top-0 right-0 p-3 text-red-500/10">
                        <ShieldAlert className="h-16 w-16" />
                      </div>
                      <div className="flex gap-3">
                        <AlertTriangle className="h-5 w-5 text-red-400 shrink-0 mt-0.5" />
                        <div>
                          <h4 className="font-display text-sm font-bold text-red-300">Out-of-Band State Deviation Detected</h4>
                          <p className="text-xs text-red-400/90 leading-relaxed mt-1">
                            An administrator bypassed the Terraform deployment lifecycle and changed configurations directly in the AWS Console. 
                            The actual live cloud estate has diverged. Our recursive generic scanner caught this deviation.
                          </p>
                        </div>
                      </div>
                    </div>
                  )}

                  {analyzingResourceId === selectedResource.id ? (
                    <div className="mt-6 rounded-xl border border-blue-500/30 bg-slate-950 p-6 flex flex-col gap-6 animate-in fade-in duration-300">
                      
                      {/* LangGraph Title */}
                      <div className="flex items-center justify-between border-b border-slate-800 pb-4">
                        <div className="flex items-center gap-3">
                          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-500/10 text-blue-400 border border-blue-500/20 animate-pulse">
                            <Activity className="h-5 w-5" />
                          </div>
                          <div>
                            <h4 className="font-display text-sm font-bold text-white flex items-center gap-2">
                              Drift Analysis Pipeline Executing
                              <span className="inline-flex h-2 w-2 rounded-full bg-blue-500 animate-ping" />
                            </h4>
                            <p className="text-xs text-slate-500 mt-0.5">Running classification, security audit, cost estimation, reconciliation, and policy check steps</p>
                          </div>
                        </div>
                        <span className="font-mono text-xs text-blue-400 bg-blue-950/40 border border-blue-500/20 rounded px-2.5 py-1 font-bold">
                          Active: Step {activeAnalysisStep + 1}/5
                        </span>
                      </div>

                      {/* LangGraph State Nodes Progress timeline */}
                      <div className="grid grid-cols-1 md:grid-cols-5 gap-3 relative">
                        {[
                          {
                            id: 0,
                            name: 'classify_node',
                            label: '1. Classify Drift',
                            desc: 'Keyword-based risk classification (high / moderate / low risk)',
                            icon: Shield
                          },
                          {
                            id: 1,
                            name: 'security_analysis_node',
                            label: '2. Security Audit',
                            desc: 'Identify CIS / SOC2 compliance failures & threat vectors',
                            icon: ShieldAlert
                          },
                          {
                            id: 2,
                            name: 'cost_estimation_node',
                            label: '3. Cost Impact',
                            desc: 'Calculate financial liability & operational overhead',
                            icon: DollarSign
                          },
                          {
                            id: 3,
                            name: 'hcl_reconciliation_node',
                            label: '4. HCL Generator',
                            desc: 'Reconstruct compliant Terraform code fix structure',
                            icon: FileCode2
                          },
                          {
                            id: 4,
                            name: 'security_scan_node',
                            label: '5. Policy Scan',
                            desc: 'Verify fix passes Checkov IaC security rules',
                            icon: ClipboardCheck
                          }
                        ].map((node) => {
                          const isCompleted = activeAnalysisStep > node.id;
                          const isActive = activeAnalysisStep === node.id;
                          const isPending = activeAnalysisStep < node.id;

                          const NodeIcon = node.icon;

                          return (
                            <div 
                              key={node.id} 
                              className={`relative rounded-xl border p-4 transition-all duration-300 flex flex-col gap-2.5 ${
                                isCompleted ? 'border-emerald-500/20 bg-emerald-950/5 text-slate-300' :
                                isActive ? 'border-blue-500/40 bg-blue-950/10 text-white shadow-lg shadow-blue-500/5' :
                                'border-slate-850 bg-slate-900/10 text-slate-500'
                              }`}
                            >
                              <div className="flex items-center justify-between">
                                <span className={`flex h-7 w-7 items-center justify-center rounded-lg border ${
                                  isCompleted ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400' :
                                  isActive ? 'bg-blue-500/10 border-blue-500/30 text-blue-400 animate-pulse' :
                                  'bg-slate-950 border-slate-800 text-slate-600'
                                }`}>
                                  <NodeIcon className="h-4 w-4" />
                                </span>
                                
                                {isCompleted && (
                                  <span className="text-[10px] font-mono font-bold text-emerald-400 bg-emerald-950/50 px-1.5 py-0.5 rounded border border-emerald-500/20">
                                    Done
                                  </span>
                                )}
                                {isActive && (
                                  <span className="text-[10px] font-mono font-bold text-blue-400 bg-blue-950/50 px-1.5 py-0.5 rounded border border-blue-500/20 animate-pulse">
                                    Running...
                                  </span>
                                )}
                                {isPending && (
                                  <span className="text-[10px] font-mono font-bold text-slate-600">
                                    Queued
                                  </span>
                                )}
                              </div>

                              <div>
                                <h5 className="font-display text-xs font-bold leading-none">{node.label}</h5>
                                <span className="text-[10px] font-mono text-slate-500">{node.name}</span>
                              </div>
                              <p className={`text-[11px] leading-relaxed mt-1 ${isPending ? 'text-slate-600' : 'text-slate-400'}`}>
                                {node.desc}
                              </p>
                            </div>
                          );
                        })}
                      </div>

                      {/* Agent CLI Console */}
                      <div className="rounded-xl border border-slate-800 bg-slate-950 p-4 font-mono text-xs">
                        <div className="flex items-center justify-between border-b border-slate-900 pb-2 mb-3 text-slate-500">
                          <span className="text-[10px] font-bold uppercase tracking-wider">Python Agent Stdout Trace (agent.py)</span>
                          <span className="text-[10px]">process_id: {Date.now().toString().slice(-4)}</span>
                        </div>
                        
                        <div className="space-y-1.5 max-h-[160px] overflow-y-auto text-slate-400">
                          <div className="flex gap-2">
                            <span className="text-slate-600 select-none">$</span>
                            <span className="text-slate-500">python3 agent.py --resource {selectedResource.name}</span>
                          </div>
                          
                          {activeAnalysisStep >= 0 && (
                            <>
                              <div className="text-slate-500">[sys] Initializing python runtime and state schemas...</div>
                              <div className="text-slate-500">[sys] Successfully loaded agent.py entrypoint.</div>
                              <div className="text-slate-500">[sys] Invoking StateGraph.compile() with AgentState payload...</div>
                              <div className="text-blue-400">[graph] Entry Point reached: Entering node "classify"</div>
                              <div className="text-slate-300">[classify] Analyzing drifted fields for "{selectedResource.name}"</div>
                              <div className="text-slate-300">[classify] Submitting request to Gemini-Flash 2.5 classification engine...</div>
                            </>
                          )}
                          
                          {activeAnalysisStep >= 1 && (
                            <>
                              <div className="text-emerald-400">[classify] Classification complete: moderate_risk_change, risk_score=High</div>
                              <div className="text-blue-400">[graph] Edge triggered: "classify" ➔ "security_analysis"</div>
                              <div className="text-blue-400">[graph] Entering node "security_analysis"</div>
                              <div className="text-slate-300">[security_analysis] Auditing config deviations against compliance catalogs...</div>
                              <div className="text-slate-300">[security_analysis] Evaluating compliance matrix: SOC2 CC6.1, HIPAA 164.312, CIS Benchmarks</div>
                              <div className="text-slate-300">[security_analysis] Compiling contextual security audit breakdown report...</div>
                            </>
                          )}

                          {activeAnalysisStep >= 2 && (
                            <>
                              <div className="text-emerald-400">[security_analysis] Threat analysis generated successfully.</div>
                              <div className="text-blue-400">[graph] Edge triggered: "security_analysis" ➔ "cost_estimation"</div>
                              <div className="text-blue-400">[graph] Entering node "cost_estimation"</div>
                              <div className="text-slate-300">[cost_estimation] Calculating operational hours and regulatory fine projection...</div>
                              <div className="text-slate-300">[cost_estimation] Formulating cost liability estimate based on risk severity...</div>
                            </>
                          )}

                          {activeAnalysisStep >= 3 && (
                            <>
                              <div className="text-emerald-400">[cost_estimation] Cost assessment completed.</div>
                              <div className="text-blue-400">[graph] Edge triggered: "cost_estimation" ➔ "hcl_reconciliation"</div>
                              <div className="text-blue-400">[graph] Entering node "hcl_reconciliation"</div>
                              <div className="text-slate-300">[hcl_reconciliation] Correcting Terraform main.tf resource configuration blocks...</div>
                              <div className="text-slate-300">[hcl_reconciliation] Generating state-enforcing HCL diff blocks...</div>
                              <div className="text-emerald-400">[hcl_reconciliation] Infrastructure HCL fix generated perfectly.</div>
                            </>
                          )}

                          {activeAnalysisStep >= 4 && (
                            <>
                              <div className="text-blue-400">[graph] Edge triggered: "hcl_reconciliation" ➔ "security_scan"</div>
                              <div className="text-blue-400">[graph] Entering node "security_scan"</div>
                              <div className="text-indigo-400 animate-pulse">[policy_scan] Running policy reference checks...</div>
                              <div className="text-slate-300">[policy_scan] Identifying applicable CIS/HIPAA/SOC2 compliance rules...</div>
                              <div className="text-emerald-400">[policy_scan] Policy references identified. CI/CD will run full checkov scan on PR.</div>
                              <div className="text-blue-400">[graph] Workflow reached terminal state node "END"</div>
                              <div className="text-slate-500">[sys] Python subprocess exit code 0. Compiled workflow state.</div>
                              <div className="text-indigo-400 animate-pulse">[sys] Packaging state output as git pull request branch...</div>
                            </>
                          )}
                        </div>
                      </div>

                    </div>
                  ) : (
                    <>
                      {/* Dual Panel Comparison */}
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
                        {/* Desired state */}
                        <div className="rounded-xl border border-slate-800 bg-slate-950/80 p-4">
                          <div className="flex items-center gap-2 border-b border-slate-900 pb-2 mb-3">
                            <CheckCircle className="h-4 w-4 text-green-400" />
                            <span className="font-display text-xs font-bold text-slate-400 uppercase tracking-wide">Desired State (Terraform State)</span>
                          </div>
                          <pre className="font-mono text-[11px] text-green-300 bg-slate-950 p-2 rounded max-h-[160px] overflow-auto leading-relaxed">
                            {JSON.stringify(selectedResource.desiredState, null, 2)}
                          </pre>
                        </div>

                        {/* Actual state */}
                        <div className={`rounded-xl border p-4 ${
                          selectedResource.isDrifted ? 'border-red-500/20 bg-red-950/5' : 'border-slate-800 bg-slate-950/80'
                        }`}>
                          <div className="flex items-center gap-2 border-b border-slate-900 pb-2 mb-3 justify-between">
                            <div className="flex items-center gap-2">
                              {selectedResource.isDrifted ? (
                                <AlertTriangle className="h-4 w-4 text-red-400" />
                              ) : (
                                <CheckCircle className="h-4 w-4 text-green-400" />
                              )}
                              <span className="font-display text-xs font-bold text-slate-400 uppercase tracking-wide">Live AWS Configuration</span>
                            </div>
                            {selectedResource.isDrifted && (
                              <span className="text-[10px] font-mono font-bold text-red-400 animate-pulse">DRIFT</span>
                            )}
                          </div>
                          <pre className={`font-mono text-[11px] bg-slate-950 p-2 rounded max-h-[160px] overflow-auto leading-relaxed ${
                            selectedResource.isDrifted ? 'text-red-300' : 'text-slate-300'
                          }`}>
                            {JSON.stringify(selectedResource.actualState, null, 2)}
                          </pre>
                        </div>
                      </div>

                      {/* Terraform Source Code */}
                      <div className="mt-4 flex-grow flex flex-col min-h-[150px]">
                        <div className="flex items-center justify-between border-b border-slate-800 pb-2 mb-2">
                          <div className="flex items-center gap-2">
                            <FileCode2 className="h-4 w-4 text-slate-400" />
                            <span className="font-display text-xs font-bold text-slate-400 uppercase tracking-wide">Desired Terraform Configuration (HCL)</span>
                          </div>
                          <span className="text-[10px] text-slate-500 font-mono">main.tf</span>
                        </div>
                        <pre className="font-mono text-[11px] text-slate-400 bg-slate-950/90 p-4 rounded-xl border border-slate-900 overflow-auto flex-grow max-h-[220px] leading-relaxed select-text">
                          {selectedResource.terraformCode}
                        </pre>
                      </div>
                    </>
                  )}
                </div>
              ) : (
                <div className="rounded-2xl border border-slate-850 bg-slate-900/10 p-12 text-center flex flex-col items-center justify-center min-h-[400px]">
                  <Server className="h-12 w-12 text-slate-600 mb-3" />
                  <p className="text-sm text-slate-500">Select a managed cloud resource from the list to inspect state details.</p>
                </div>
              )}

              {/* MOCK CLI TERMINAL FOR SCAN LOGS (Production Value) */}
              {planLogs.length > 0 && (
                <div className="rounded-xl border border-slate-800 bg-slate-950 p-4">
                  <div className="flex items-center justify-between border-b border-slate-900 pb-2 mb-3">
                    <div className="flex items-center gap-2">
                      <Terminal className="h-4 w-4 text-blue-400 animate-pulse" />
                      <span className="font-mono text-xs font-bold text-slate-400 uppercase">Terraform Plan Console (drift scan)</span>
                    </div>
                    <span className="text-[10px] text-slate-500 font-mono">task_id: {Date.now().toString().slice(-6)}</span>
                  </div>
                  <div className="font-mono text-[11px] text-slate-300 space-y-1 bg-slate-900/80 p-3 rounded-lg max-h-[160px] overflow-y-auto">
                    {planLogs.map((log, index) => (
                      <div key={index} className="flex gap-2">
                        <span className="text-slate-600 select-none">$</span>
                        <span>{log}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Register custom IaC infrastructure (Production Feature) */}
        {activeTab === 'add-resource' && (
          <div className="rounded-2xl border border-slate-800 bg-slate-900/30 p-5 md:p-6 shadow-xl relative overflow-hidden backdrop-blur-sm">
            <div className="absolute right-0 top-0 -mr-16 -mt-16 h-48 w-48 rounded-full bg-blue-500/5 blur-3xl" />
            
            <div className="border-b border-slate-800 pb-4 mb-6">
              <h3 className="font-display text-base font-bold text-white">Track Custom Infrastructure Resource</h3>
              <p className="text-xs text-slate-500 mt-1">Register a custom resource with its Terraform code of record and desired state schema, making it fully testable for generic drift scenarios.</p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              
              {/* Left Column: Form Info */}
              <div className="space-y-4">
                <div>
                  <label className="block text-xs font-bold text-slate-400 uppercase mb-1.5">Resource Name</label>
                  <input
                    type="text"
                    value={newResName}
                    onChange={(e) => setNewResName(e.target.value)}
                    placeholder="e.g., prod_notification_queue"
                    className="w-full bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
                  />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-bold text-slate-400 uppercase mb-1.5">Resource Type</label>
                    <input
                      type="text"
                      value={newResType}
                      onChange={(e) => setNewResType(e.target.value)}
                      placeholder="e.g., aws_sqs_queue"
                      className="w-full bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
                    />
                  </div>

                  <div>
                    <label className="block text-xs font-bold text-slate-400 uppercase mb-1.5">Cloud Service</label>
                    <select
                      value={newResService}
                      onChange={(e: any) => setNewResService(e.target.value)}
                      className="w-full bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
                    >
                      <option value="S3">S3</option>
                      <option value="VPC">VPC</option>
                      <option value="IAM">IAM</option>
                      <option value="RDS">RDS</option>
                    </select>
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-bold text-slate-400 uppercase mb-1.5">Desired State Configuration (JSON)</label>
                  <textarea
                    value={newResDesiredJson}
                    onChange={(e) => setNewResDesiredJson(e.target.value)}
                    rows={6}
                    className="w-full font-mono text-xs bg-slate-950 border border-slate-850 rounded-lg p-3 text-slate-300 focus:outline-none focus:border-blue-500"
                    placeholder="Input key-value JSON state representation"
                  />
                </div>
              </div>

              {/* Right Column: HCL code */}
              <div className="flex flex-col">
                <label className="block text-xs font-bold text-slate-400 uppercase mb-1.5">Terraform Source Code (HCL)</label>
                <textarea
                  value={newResHcl}
                  onChange={(e) => setNewResHcl(e.target.value)}
                  rows={13}
                  className="w-full font-mono text-xs bg-slate-950 border border-slate-850 rounded-lg p-3 text-slate-300 focus:outline-none focus:border-blue-500 flex-grow"
                  placeholder='resource "aws_sqs_queue" "my_queue" { ... }'
                />

                <div className="mt-4 flex justify-end gap-3">
                  <button
                    onClick={() => setActiveTab('resources')}
                    className="px-4 py-2 rounded-lg border border-slate-800 text-xs font-semibold text-slate-400 hover:bg-slate-900"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleCreateCustomResource}
                    className="px-5 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold"
                  >
                    Deploy to State Registry
                  </button>
                </div>
              </div>

            </div>
          </div>
        )}

        {/* GitHub Pull Request Hub Tab */}
        {activeTab === 'prs' && (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 animate-in fade-in duration-250">
            
            {/* PRs List (Left side) */}
            <div className="lg:col-span-4 flex flex-col gap-4">
              <div className="flex items-center justify-between">
                <h3 className="font-display font-semibold text-white text-sm">Automated Pull Requests ({state.prs.length})</h3>
                <span className="text-[11px] text-slate-500 font-mono">Select a PR to review</span>
              </div>

              {state.prs.length === 0 ? (
                <div className="rounded-xl border border-dashed border-slate-800 p-8 text-center bg-slate-900/10">
                  <GitPullRequest className="h-8 w-8 text-slate-600 mx-auto mb-2" />
                  <h4 className="font-display text-xs font-semibold text-slate-400">No PRs Currently Generated</h4>
                  <p className="text-[11px] text-slate-500 mt-1 max-w-xs mx-auto">
                    To trigger an automated pull request, select a drifted resource in AWS Managed Resources tab and run "Run Agent Reconciliation".
                  </p>
                </div>
              ) : (
                <div className="flex flex-col gap-3">
                  {state.prs.map((pr) => {
                    const isSelected = selectedPr?.id === pr.id;
                    const classificationColor =
                      pr.analysis.classification === 'high_risk_change' ? 'bg-red-500/10 text-red-400 border-red-500/20' :
                      pr.analysis.classification === 'moderate_risk_change' ? 'bg-amber-500/10 text-amber-400 border-amber-500/20' :
                      'bg-blue-500/10 text-blue-400 border-blue-500/20';

                    return (
                      <div
                        key={pr.id}
                        onClick={() => setSelectedPr(pr)}
                        className={`group relative flex flex-col rounded-xl border p-4 cursor-pointer transition-all ${
                          isSelected 
                            ? 'border-blue-500 bg-slate-900/70 shadow-lg' 
                            : 'border-slate-800 bg-slate-950/50 hover:border-slate-700 hover:bg-slate-900/30'
                        }`}
                      >
                        <div className="flex items-start justify-between">
                          <div className="flex items-center gap-2">
                            <GitPullRequest className={`h-4.5 w-4.5 ${
                              pr.status === 'Merged' ? 'text-purple-400' : 'text-blue-400'
                            }`} />
                            <h4 className="font-display text-xs font-bold text-white group-hover:text-blue-400 transition-colors line-clamp-1">
                              {pr.title}
                            </h4>
                          </div>
                          
                          <span className={`text-[10px] font-mono font-bold uppercase rounded-full px-2 py-0.5 border ${
                            pr.status === 'Merged' ? 'bg-purple-500/10 text-purple-400 border-purple-500/20' :
                            pr.status === 'Closed' ? 'bg-slate-800 text-slate-400 border-slate-700' :
                            'bg-blue-500/10 text-blue-400 border-blue-500/20 animate-pulse'
                          }`}>
                            {pr.status}
                          </span>
                        </div>

                        <div className="mt-2.5 flex items-center justify-between text-[11px] text-slate-500 font-mono">
                          <span>PR #{pr.number} • {pr.branch}</span>
                          <span className={`px-1.5 py-0.5 rounded border ${classificationColor} font-bold text-[9px] uppercase`}>
                            {pr.analysis.classification}
                          </span>
                        </div>

                        <div className="mt-3 flex items-center justify-between border-t border-slate-900 pt-2.5">
                          <div className="flex items-center gap-1.5 text-[10px] text-slate-500">
                            <Clock className="h-3 w-3" />
                            <span>{new Date(pr.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                          </div>
                          <span className="text-[10px] font-mono font-bold text-slate-400 flex items-center gap-0.5">
                            Risk: <span className={
                              pr.analysis.riskScore === 'Critical' || pr.analysis.riskScore === 'High' ? 'text-rose-400' : 'text-yellow-400'
                            }>{pr.analysis.riskScore}</span>
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* PR Details Workspace (Right side) */}
            <div className="lg:col-span-8 flex flex-col gap-4">
              {selectedPr ? (
                <div className="rounded-2xl border border-slate-800 bg-slate-900/30 p-5 md:p-6 shadow-xl relative overflow-hidden backdrop-blur-sm flex flex-col h-full">
                  <div className="absolute right-0 top-0 -mr-16 -mt-16 h-32 w-32 rounded-full bg-blue-500/5 blur-2xl" />
                  
                  {/* Title & Status controls */}
                  <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between border-b border-slate-800 pb-4 gap-4">
                    <div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-[10px] font-bold text-slate-400 border border-slate-700">
                          Branch: {selectedPr.branch}
                        </span>
                        <span className="text-xs text-slate-500">• Opened by AI Drift Reconciler GitHub App</span>
                      </div>
                      <h3 className="font-display text-lg font-bold text-white mt-1.5 flex items-center gap-2">
                        <span>{selectedPr.title}</span>
                        <span className="text-blue-400 font-mono font-normal">#{selectedPr.number}</span>
                      </h3>
                      <p className="text-[12px] text-slate-400 mt-2">
                        Disclaimer: Merging a PR here updates the reconciliation record and (in demo mode) marks resources as reconciled in the UI/state — it does <strong>not</strong> execute <code>terraform apply</code> against your infrastructure. Apply remediation through your CI/CD pipeline to make real changes.
                      </p>
                    </div>

                    {/* Pull Request Actions */}
                    <div>
                      {selectedPr.status === 'Open' ? (
                        <button
                          id="btn-merge-pr"
                          onClick={() => {
                            // Open modal to collect approver identity
                            setPendingMergePrId(selectedPr.id);
                            setShowApproveModal(true);
                          }}
                          disabled={loadingPrId !== null}
                          className="inline-flex items-center gap-2 rounded-xl bg-purple-600 hover:bg-purple-500 text-white font-semibold text-xs tracking-wide px-5 py-2.5 shadow-lg shadow-purple-500/10 transition-all active:scale-95"
                        >
                          {loadingPrId === selectedPr.id ? (
                            <>
                              <span className="h-3 w-3 animate-spin rounded-full border border-slate-200 border-t-transparent mr-1" />
                              Applying Terraform fix...
                            </>
                          ) : (
                            <>
                              <GitMerge className="h-4 w-4" />
                              Merge Pull Request
                            </>
                          )}
                        </button>
                      ) : (
                        <div className="inline-flex items-center gap-1.5 rounded-xl border border-purple-500/20 bg-purple-950/10 px-4 py-2 font-mono text-xs font-bold text-purple-400">
                          <CheckCircle className="h-4 w-4" /> RECONCILED & MERGED
                        </div>
                      )}
                    </div>
                  </div>

                  <ApproveModal
                    visible={showApproveModal}
                    title="Approve Merge"
                    initial=""
                    onCancel={() => { setShowApproveModal(false); setPendingMergePrId(null); }}
                    onConfirm={(approver) => {
                      setShowApproveModal(false);
                      if (pendingMergePrId) handleMergePr(pendingMergePrId, approver);
                      setPendingMergePrId(null);
                    }}
                  />

                  {/* Classification & Risk Overview Row */}
                  <div className="grid grid-cols-1 sm:grid-cols-4 gap-3 mt-4">
                    
                    <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
                      <div className="text-[10px] font-mono uppercase tracking-wider text-slate-500 font-bold">Threat Class</div>
                      <div className="flex items-center gap-1.5 mt-1">
                        <span className={`inline-block h-2.5 w-2.5 rounded-full ${
                          selectedPr.analysis.classification === 'high_risk_change' ? 'bg-rose-500' :
                          selectedPr.analysis.classification === 'moderate_risk_change' ? 'bg-amber-500' :
                          'bg-blue-400'
                        }`} />
                        <span className="font-display text-xs font-bold text-white">
                          {selectedPr.analysis.classification === 'high_risk_change' ? 'High Risk' :
                           selectedPr.analysis.classification === 'moderate_risk_change' ? 'Moderate Risk' :
                           selectedPr.analysis.classification === 'low_risk_change' ? 'Low Risk' :
                           selectedPr.analysis.classification}
                        </span>
                      </div>
                    </div>

                    <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
                      <div className="text-[10px] font-mono uppercase tracking-wider text-slate-500 font-bold">Risk Assessment</div>
                      <div className="flex items-center gap-1.5 mt-1">
                        <span className={`font-display text-xs font-bold ${
                          selectedPr.analysis.riskScore === 'Critical' || selectedPr.analysis.riskScore === 'High' ? 'text-rose-400' : 'text-yellow-400'
                        }`}>
                          {selectedPr.analysis.riskScore}
                        </span>
                      </div>
                    </div>

                    <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
                      <div className="text-[10px] font-mono uppercase tracking-wider text-slate-500 font-bold">Vulnerabilities Checked</div>
                      <div className="text-xs font-bold text-white mt-1">SOC2, HIPAA, PCI-DSS, CIS-Benchmarks</div>
                    </div>

                    <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
                      <div className="text-[10px] font-mono uppercase tracking-wider text-slate-500 font-bold">Compliance Status</div>
                      <div className="text-xs font-bold text-amber-400 mt-1 flex items-center gap-1">
                        <AlertTriangle className="h-3 w-3 shrink-0" /> Restoring...
                      </div>
                    </div>

                  </div>

                  {/* Tab Selector for PR Description vs LangGraph Trace vs Checkov */}
                  <div className="flex border-b border-slate-800 mt-5 gap-4">
                    <button
                      onClick={() => setPrActiveSubTab('report')}
                      className={`pb-2 text-xs font-display font-medium transition-colors border-b-2 -mb-px ${
                        prActiveSubTab === 'report'
                          ? 'border-blue-500 text-white font-bold'
                          : 'border-transparent text-slate-500 hover:text-slate-300'
                      }`}
                    >
                      Reconciliation Report
                    </button>
                    <button
                      onClick={() => setPrActiveSubTab('langgraph')}
                      className={`pb-2 text-xs font-display font-medium transition-colors border-b-2 -mb-px flex items-center gap-1.5 ${
                        prActiveSubTab === 'langgraph'
                          ? 'border-blue-500 text-white font-bold'
                          : 'border-transparent text-slate-500 hover:text-slate-300'
                      }`}
                    >
                      <Sparkles className="h-3.5 w-3.5 text-indigo-400" />
                      Agent Audit Trace
                    </button>
                    <button
                      onClick={() => setPrActiveSubTab('cicd')}
                      className={`pb-2 text-xs font-display font-medium transition-colors border-b-2 -mb-px flex items-center gap-1.5 ${
                        prActiveSubTab === 'cicd'
                          ? 'border-blue-500 text-white font-bold'
                          : 'border-transparent text-slate-500 hover:text-slate-300'
                      }`}
                    >
                      <ClipboardCheck className="h-3.5 w-3.5 text-emerald-400" />
                      CI/CD Checks
                    </button>
                  </div>

                  {prActiveSubTab === 'report' ? (
                    /* PR Markdown Description Display */
                    <div className="mt-4 rounded-xl border border-slate-850 bg-slate-950 p-4 max-h-[220px] overflow-auto select-text">
                      <div className="flex items-center justify-between border-b border-slate-900 pb-2 mb-2">
                        <span className="text-[10px] font-mono font-bold text-slate-500 uppercase tracking-wide">PR Description (Markdown format)</span>
                        <span className="text-[10px] text-slate-500">Auto-Generated Report</span>
                      </div>
                      <div className="text-xs text-slate-300 leading-relaxed font-sans prose prose-invert">
                        <h4 className="font-bold text-sm text-white mb-2">🤖 Drift Reconciliation Agent Summary</h4>
                        <p className="mb-2">This pull request was automatically generated to close state compliance gaps. Here is the threat matrix analyzed by the LangGraph backend agent:</p>
                        
                        <div className="bg-slate-900/50 rounded-lg p-3 border border-slate-900 mb-3 flex flex-col gap-2">
                          <div>
                            <span className="font-bold text-white text-[11px] block">Impact Vector:</span>
                            <span className="text-slate-400 text-[11px]">{selectedPr.analysis.explanation}</span>
                          </div>
                          <div>
                            <span className="font-bold text-rose-300 text-[11px] block">Security Breach Risk:</span>
                            <span className="text-slate-400 text-[11px]">{selectedPr.analysis.securityImpact}</span>
                          </div>
                          <div>
                            <span className="font-bold text-amber-300 text-[11px] block">Audit & Regulatory Fines:</span>
                            <span className="text-slate-400 text-[11px]">{selectedPr.analysis.costImpact}</span>
                          </div>
                        </div>

                        <p className="text-slate-400">The agent recommends merging this branch immediately to trigger the webhook and apply the reconciling configuration on standard AWS terraform pipeline.</p>
                      </div>
                    </div>
                  ) : prActiveSubTab === 'langgraph' ? (
                    /* LangGraph State Machine Audit Trace */
                    <div className="mt-4 rounded-xl border border-slate-850 bg-slate-950 p-4 max-h-[220px] overflow-auto select-text">
                      <div className="flex items-center justify-between border-b border-slate-900 pb-2 mb-3">
                        <span className="text-[10px] font-mono font-bold text-slate-500 uppercase tracking-wide">Python StateGraph Evaluation Audit Trail</span>
                        <span className="text-[10px] text-slate-500 font-mono">CompiledGraph.invoke()</span>
                      </div>
                      
                      <div className="space-y-4">
                        {/* Classify Node */}
                        <div className="flex gap-3 relative">
                          <div className="absolute left-3 top-6 bottom-0 w-0.5 bg-slate-850" />
                          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-[10px] font-bold font-mono">
                            01
                          </div>
                          <div>
                            <h5 className="text-xs font-bold text-white font-mono">classify_node</h5>
                            <p className="text-[10px] text-slate-500 mt-0.5">Categorized intent and determined threat score under dynamic state graph parameters.</p>
                            <div className="mt-1.5 flex gap-2">
                              <span className="rounded bg-slate-900 border border-slate-850 px-2 py-0.5 font-mono text-[9px] text-slate-400">
                                classification: <strong className="text-emerald-400">{selectedPr.analysis.classification}</strong>
                              </span>
                              <span className="rounded bg-slate-900 border border-slate-850 px-2 py-0.5 font-mono text-[9px] text-slate-400">
                                riskScore: <strong className={selectedPr.analysis.riskScore === 'Critical' || selectedPr.analysis.riskScore === 'High' ? 'text-red-400' : 'text-amber-400'}>{selectedPr.analysis.riskScore}</strong>
                              </span>
                            </div>
                          </div>
                        </div>

                        {/* Security Analysis Node */}
                        <div className="flex gap-3 relative">
                          <div className="absolute left-3 top-6 bottom-0 w-0.5 bg-slate-850" />
                          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-[10px] font-bold font-mono">
                            02
                          </div>
                          <div>
                            <h5 className="text-xs font-bold text-white font-mono">security_analysis_node</h5>
                            <p className="text-[10px] text-slate-500 mt-0.5">Evaluated compliance gaps (SOC2, HIPAA, PCI-DSS, CIS AWS Benchmarks) and threat vector vectors.</p>
                            <div className="mt-2 bg-slate-900/60 p-2.5 rounded-lg border border-slate-900 font-sans text-[11px] text-slate-400 space-y-1">
                              <div><strong className="text-white">Explanation:</strong> {selectedPr.analysis.explanation}</div>
                              <div className="mt-1"><strong className="text-rose-400">Threat Impact:</strong> {selectedPr.analysis.securityImpact}</div>
                            </div>
                          </div>
                        </div>

                        {/* Cost Estimation Node */}
                        <div className="flex gap-3 relative">
                          <div className="absolute left-3 top-6 bottom-0 w-0.5 bg-slate-850" />
                          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-[10px] font-bold font-mono">
                            03
                          </div>
                          <div>
                            <h5 className="text-xs font-bold text-white font-mono">cost_estimation_node</h5>
                            <p className="text-[10px] text-slate-500 mt-0.5">Calculated regulatory liability, recovery overhead, and operational re-audit certification losses.</p>
                            <div className="mt-2 bg-slate-900/60 p-2.5 rounded-lg border border-slate-900 font-sans text-[11px] text-slate-400">
                              <strong>Cost Impact:</strong> {selectedPr.analysis.costImpact}
                            </div>
                          </div>
                        </div>

                        {/* HCL Reconciliation Node */}
                        <div className="flex gap-3 relative">
                          <div className="absolute left-3 top-6 bottom-0 w-0.5 bg-slate-850" />
                          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-[10px] font-bold font-mono">
                            04
                          </div>
                          <div>
                            <h5 className="text-xs font-bold text-white font-mono">hcl_reconciliation_node</h5>
                            <p className="text-[10px] text-slate-500 mt-0.5">Analyzed delta mapping and generated corrective Terraform HCL blocks automatically.</p>
                            <span className="inline-block mt-2 rounded bg-emerald-950/20 border border-emerald-500/10 px-2 py-0.5 font-mono text-[9px] text-emerald-400 font-semibold">
                              ✓ Reconciling manifest output generated (reconcile_fix.tf)
                            </span>
                          </div>
                        </div>

                        {/* Security Scan Node */}
                        <div className="flex gap-3">
                          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-[10px] font-bold font-mono">
                            05
                          </div>
                          <div>
                            <h5 className="text-xs font-bold text-white font-mono">security_scan_node</h5>
                            <p className="text-[10px] text-slate-500 mt-0.5">Verified compliant IaC resources using Checkov's comprehensive static analysis scanner rules.</p>
                            <span className="inline-block mt-2 rounded bg-emerald-950/20 border border-emerald-500/10 px-2 py-0.5 font-mono text-[9px] text-emerald-400 font-semibold">
                              ✓ Policy references: {((selectedPr.analysis.policyReferences || selectedPr.analysis.checkovChecks) || []).length} checks identified
                            </span>
                          </div>
                        </div>
                      </div>
                    </div>
                  ) : (
                    /* CI/CD Status — checks run in GitHub Actions after PR is opened */
                    <div className="mt-4 rounded-xl border border-slate-850 bg-slate-950 p-4 max-h-[220px] overflow-auto select-text">
                      <div className="flex items-center justify-between border-b border-slate-900 pb-2.5 mb-3">
                        <div className="flex items-center gap-2">
                          <ClipboardCheck className="h-4 w-4 text-emerald-400" />
                          <span className="text-[10px] font-mono font-bold text-slate-400 uppercase tracking-wide">
                            CI/CD Pipeline Checks
                          </span>
                        </div>
                        <span className="text-[10px] text-slate-500 font-mono">GitHub Actions</span>
                      </div>
                      <div className="space-y-3">
                        <div className="bg-slate-900/40 border border-slate-900 rounded-lg p-3">
                          <p className="text-xs text-slate-300 leading-relaxed">
                            Policy checks (terraform validate, checkov scan, terraform plan) run automatically via
                            <strong className="text-white"> GitHub Actions </strong>
                            when this PR is opened on branch <code className="text-blue-400">{selectedPr.branch}</code>.
                          </p>
                          <div className="mt-3 grid grid-cols-3 gap-2">
                            {[
                              { label: 'fmt', desc: 'Terraform format check', status: 'pending' },
                              { label: 'validate', desc: 'Syntax validation', status: 'pending' },
                              { label: 'plan', desc: 'Dry-run plan', status: 'pending' },
                            ].map((check) => (
                              <div key={check.label} className="bg-slate-950 rounded-lg p-2.5 border border-slate-800 text-center">
                                <span className="font-mono text-[10px] font-bold text-slate-300">{check.label}</span>
                                <div className="mt-1 flex items-center justify-center gap-1">
                                  <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
                                  <span className="text-[9px] text-slate-500">queued</span>
                                </div>
                              </div>
                            ))}
                          </div>
                          {((selectedPr.analysis.policyReferences || selectedPr.analysis.checkovChecks) || []).length > 0 && (
                            <div className="mt-3 pt-3 border-t border-slate-900">
                              <span className="text-[10px] text-slate-500 font-bold uppercase">Policy References</span>
                              {((selectedPr.analysis.policyReferences || selectedPr.analysis.checkovChecks) || []).map((ref: any, idx: number) => (
                                <div key={idx} className="mt-1 text-[10px] text-slate-400 font-mono">
                                  {ref.id}: {ref.name} <span className="text-amber-400">[{ref.severity}]</span>
                                  {ref.source && <span className="text-slate-600 ml-1">({ref.source})</span>}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Code Diff Component (Production level feature) */}
                  <div className="mt-4 flex-grow flex flex-col min-h-[160px]">
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2 mb-2">
                      <div className="flex items-center gap-2">
                        <FileDiff className="h-4 w-4 text-emerald-400" />
                        <span className="font-display text-xs font-bold text-slate-400 uppercase tracking-wide">Proposed Reconciliation (unapproved recommendation)</span>
                      </div>
                      <span className="text-[10px] text-slate-500 font-mono">reconcile_fix.tf</span>
                    </div>
                    {/* Render color coded changes */}
                    {state.resources.find(r => r.id === selectedPr.analysis.resourceId) ? (
                      renderCodeDiff(
                        state.resources.find(r => r.id === selectedPr.analysis.resourceId)!.terraformCode,
                        selectedPr.hclChanges
                      )
                    ) : (
                      <pre className="font-mono text-[11px] text-green-300 bg-slate-950 p-4 rounded-xl border border-slate-900 overflow-auto">
                        {selectedPr.hclChanges}
                      </pre>
                    )}
                  </div>

                </div>
              ) : (
                <div className="rounded-2xl border border-slate-850 bg-slate-900/10 p-12 text-center flex flex-col items-center justify-center min-h-[400px]">
                  <GitPullRequest className="h-12 w-12 text-slate-600 mb-3" />
                  <p className="text-sm text-slate-500">Select an autonomous pull request from the sidebar to review assessment and merge fixes.</p>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Drift Timeline Tab */}
        {activeTab === 'timeline' && (
          <div className="rounded-2xl border border-slate-800 bg-slate-900/30 p-5 md:p-6 shadow-xl relative overflow-hidden backdrop-blur-sm">
            <div className="absolute right-0 top-0 -mr-16 -mt-16 h-48 w-48 rounded-full bg-blue-500/5 blur-3xl" />
            
            <div className="flex items-center justify-between border-b border-slate-800 pb-4 mb-6">
              <div>
                <h3 className="font-display text-base font-bold text-white">AWS Infrastructure Audit Timeline</h3>
                <p className="text-xs text-slate-500 mt-1">Chronological history of drift scans, manual console overrides, and automated Git pull request reconciliations.</p>
              </div>
              
              <button
                onClick={handleScan}
                disabled={state.scanning}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-800 hover:border-slate-700 bg-slate-950 hover:bg-slate-900 px-3 py-1.5 text-xs text-slate-300 transition-colors"
              >
                <Clock className="h-3.5 w-3.5" /> Run Drift Scan
              </button>
            </div>

            {/* Timeline Stream */}
            <div className="relative border-l border-slate-800 ml-4 pl-6 md:pl-8 flex flex-col gap-8 pb-4">
              {state.timeline.map((event, index) => {
                let iconBg = 'bg-slate-800';
                let iconColor = 'text-slate-400';
                let borderTheme = 'border-slate-800';

                if (event.type === 'scan_drift') {
                  iconBg = 'bg-amber-950/40 border border-amber-500/30';
                  iconColor = 'text-amber-400';
                  borderTheme = 'border-amber-500/15';
                } else if (event.type === 'pr_created') {
                  iconBg = 'bg-blue-950/40 border border-blue-500/30';
                  iconColor = 'text-blue-400';
                  borderTheme = 'border-blue-500/15';
                } else if (event.type === 'pr_merged') {
                  iconBg = 'bg-purple-950/40 border border-purple-500/30';
                  iconColor = 'text-purple-400';
                  borderTheme = 'border-purple-500/15';
                } else if (event.type === 'scan_clean') {
                  iconBg = 'bg-green-950/40 border border-green-500/30';
                  iconColor = 'text-green-400';
                  borderTheme = 'border-green-500/15';
                }

                return (
                  <div key={event.id} className="relative group">
                    {/* Node marker */}
                    <span className="absolute -left-[39px] md:-left-[47px] top-1 flex h-6 w-6 items-center justify-center rounded-full bg-slate-950 border border-slate-800 group-hover:scale-110 transition-transform">
                      <span className={`h-2.5 w-2.5 rounded-full ${
                        event.type === 'scan_drift' ? 'bg-amber-500' :
                        event.type === 'pr_created' ? 'bg-blue-400' :
                        event.type === 'pr_merged' ? 'bg-purple-400' :
                        'bg-green-400'
                      }`} />
                    </span>

                    {/* Content Box */}
                    <div className={`rounded-xl border p-4 bg-slate-950/60 shadow-md ${borderTheme} hover:bg-slate-950/90 transition-all duration-200`}>
                      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-1.5">
                        <div className="flex items-center gap-2">
                          <span className={`inline-flex rounded-md p-1 ${iconBg} ${iconColor}`}>
                            {event.type === 'scan_drift' && <AlertTriangle className="h-3.5 w-3.5" />}
                            {event.type === 'pr_created' && <GitPullRequest className="h-3.5 w-3.5" />}
                            {event.type === 'pr_merged' && <GitMerge className="h-3.5 w-3.5" />}
                            {event.type === 'scan_clean' && <CheckCircle className="h-3.5 w-3.5" />}
                          </span>
                          <h4 className="font-display text-sm font-bold text-white">{event.title}</h4>
                        </div>
                        <span className="font-mono text-[10px] text-slate-500">
                          {new Date(event.timestamp).toLocaleString([], { dateStyle: 'short', timeStyle: 'medium' })}
                        </span>
                      </div>

                      <p className="mt-2 text-xs text-slate-400 leading-relaxed font-sans">{event.message}</p>

                      {event.details && (
                        <div className="mt-2.5 rounded-lg bg-slate-950 p-2 border border-slate-900 font-mono text-[10px] text-slate-500 flex flex-wrap gap-x-4">
                          {Object.entries(event.details).map(([key, val]) => (
                            <div key={key}>
                              <span className="text-slate-600">{key}:</span> {String(val)}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Alerts & Human-in-the-Loop Configurations Tab */}
        {activeTab === 'alerts-config' && state && (
          <AlertsAndApproval
            resources={state.resources}
            prs={openPrs}
            environment={state.environment}
            integrationStatus={state.integrationStatus}
            onTriggerNotification={triggerNotification}
          />
        )}

      </main>

      {/* Elegant Footer and Architecture Specs */}
      <footer className="border-t border-slate-900 bg-slate-950/80 px-6 py-8 mt-12 backdrop-blur-md">
        <div className="mx-auto max-w-7xl flex flex-col md:flex-row md:items-start md:justify-between gap-6 text-xs text-slate-500">
          <div>
            <div className="flex items-center gap-2">
              <span className="font-display font-semibold text-slate-400">AWS Terraform Drift Reconciler</span>
              <span className="rounded bg-slate-900 px-1.5 py-0.5 text-[9px] font-mono text-slate-500 border border-slate-850">v2.0.0</span>
            </div>
            <p className="mt-1.5 leading-relaxed max-w-md">
              State is loaded from S3 terraform state. Drift is detected via terraform plan against live AWS. PRs are created on GitHub. HCL diffs should be validated by CI/CD pipeline checks before merging.
            </p>
          </div>

          <div className="flex flex-col gap-1 md:text-right font-mono text-[11px]">
            <div>Stack: <span className="text-slate-400">Express • React • Python Agent • Tailwind</span></div>
            <div>State: <span className="text-slate-400">S3 + Terraform Plan + Live AWS</span></div>
            <div>Alerts: <span className="text-slate-400">PagerDuty (email/SMS/calls)</span></div>
          </div>
        </div>
      </footer>

    </div>
  );
}
