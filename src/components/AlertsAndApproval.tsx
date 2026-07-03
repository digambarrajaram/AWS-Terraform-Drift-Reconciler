import React, { useState } from 'react';
import { Bell, ShieldAlert, GitMerge, UserCheck, CheckCircle, AlertTriangle } from 'lucide-react';
import { AwsResource, PullRequest, IntegrationStatus } from '../types';

interface AlertsAndApprovalProps {
  resources: AwsResource[];
  prs: PullRequest[];
  environment: string;
  integrationStatus: IntegrationStatus;
  onTriggerNotification: (message: string, type: 'success' | 'info' | 'warning') => void;
}

export default function AlertsAndApproval({
  resources, prs, environment, integrationStatus, onTriggerNotification,
}: AlertsAndApprovalProps) {
  const isProd = environment === 'production';

  return (
    <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 animate-in fade-in duration-300">

      {/* Integration Status Panel — read-only, no secrets */}
      <div className="xl:col-span-12 rounded-2xl border border-slate-800 bg-slate-900/40 p-6">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-800 pb-4 mb-6">
          <div>
            <h3 className="font-display font-bold text-white text-base flex items-center gap-2">
              <ShieldAlert className="h-5 w-5 text-blue-400" /> Integration Status
            </h3>
            <p className="text-xs text-slate-400 mt-1">
              Read-only status of connected services. Configure via environment variables at deploy time.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[
            {
              label: 'PagerDuty', key: 'pagerDuty' as const,
              desc: 'Email, SMS, and phone alerts',
            },
            {
              label: 'GitHub', key: 'github' as const,
              desc: 'PR creation and merge automation',
            },
            {
              label: 'AWS', key: 'aws' as const,
              desc: 'S3 state + live resource scanning',
            },
            {
              label: 'Terraform State', key: 'terraformState' as const,
              desc: 'State file sync from S3',
            },
          ].map((svc) => {
            const status = integrationStatus[svc.key];
            const connected = status === 'connected' || status === 'loaded';
            const showError =
              (svc.key === 'pagerDuty' && integrationStatus.lastPagerDutyError) ||
              (svc.key === 'github' && integrationStatus.lastGitHubError);
            return (
              <div key={svc.key}
                className={`rounded-xl border p-4 ${connected ? 'border-emerald-500/30 bg-emerald-950/10' : 'border-amber-500/30 bg-amber-950/10'}`}>
                <div className="flex items-center gap-2 mb-2">
                  <span className={`h-2 w-2 rounded-full ${connected ? 'bg-emerald-400' : 'bg-amber-400'}`} />
                  <span className="font-display text-xs font-bold text-white">{svc.label}</span>
                </div>
                <div className={`text-[10px] font-mono font-bold uppercase ${connected ? 'text-emerald-400' : 'text-amber-400'}`}>
                  {connected ? '● Connected' : '○ Not Configured'}
                </div>
                <p className="text-[10px] text-slate-500 mt-1">{svc.desc}</p>
                {showError && (
                  <p className="text-[9px] text-red-400 mt-2 font-mono truncate" title={showError}>
                    ⚠ {showError.slice(0, 40)}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* HITL Approval Flow */}
      <div className="xl:col-span-12 rounded-2xl border border-slate-800 bg-slate-900/40 p-6">
        <div className="flex items-center justify-between border-b border-slate-800 pb-4 mb-6">
          <div>
            <h3 className="font-display font-bold text-white text-base flex items-center gap-2">
              <UserCheck className="h-5 w-5 text-blue-400" /> Human-in-the-Loop Approval
            </h3>
            <p className="text-xs text-slate-400 mt-1">
              All drift fixes must pass CI/CD checks and require explicit human approval before merge.
            </p>
            <p className="text-[11px] text-slate-500 mt-3 rounded-md border-l-2 border-slate-700 pl-3">
              Note: merging a PR in this UI updates the reconciliation record and (in demo mode) may mark the resource as reconciled locally — it does not execute <strong>terraform apply</strong> against your infrastructure. Remediation must be applied through your normal CI/CD pipeline.
            </p>
          </div>
          <span className={`inline-flex items-center gap-1 text-[11px] font-bold px-2.5 py-1 rounded-lg ${
            isProd ? 'bg-red-950/40 border border-red-500/20 text-red-400' : 'bg-blue-950/40 border border-blue-500/20 text-blue-400'
          }`}>
            {isProd ? '🔒 Production — Approvals Required' : '🔵 ' + environment.charAt(0).toUpperCase() + environment.slice(1) + ' Mode'}
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
          {[
            { step: '01', label: 'Drift Detected', desc: 'Terraform plan against live AWS finds state deviation.' },
            { step: '02', label: 'PR Opened', desc: 'Agent generates HCL fix, opens PR on GitHub with analysis.' },
            { step: '03', label: 'CI/CD Checks', desc: 'GitHub Actions runs terraform validate, checkov, plan.' },
            { step: '04', label: 'Human Review', desc: 'SRE inspects diff, CI results, and security impact.' },
            { step: '05', label: 'Approval & Merge', desc: 'Approved PR merged in GitHub — CI/CD must run to apply the remediation (terraform apply).' },
          ].map((s, i) => (
            <div key={s.step} className="bg-slate-950 rounded-xl p-4 border border-slate-850 flex flex-col justify-between">
              <div className="absolute top-4 right-4 text-[10px] font-mono font-bold text-slate-600 hidden md:block">{s.step}</div>
              <div>
                <span className={`inline-flex h-7 w-7 items-center justify-center rounded-lg mb-3 text-xs font-mono font-bold ${
                  i === 3 ? 'bg-amber-500/15 text-amber-400 border border-amber-500/20' : 'bg-blue-500/10 text-blue-400 border border-blue-500/20'
                }`}>
                  {i === 0 ? 'DET' : i === 1 ? 'PR' : i === 2 ? 'CI' : i === 3 ? 'REV' : 'MRG'}
                </span>
                <h4 className="text-xs font-bold text-white font-display">{s.label}</h4>
                <p className="text-[11px] text-slate-500 mt-1.5 leading-normal">{s.desc}</p>
              </div>
            </div>
          ))}
        </div>

        {/* PR Approval status */}
        {prs.filter(p => p.status === 'Open').length > 0 && (
          <div className="mt-5 pt-4 border-t border-slate-800">
            <h4 className="text-xs font-bold text-white mb-2">Open Pull Requests Awaiting Review</h4>
            {prs.filter(p => p.status === 'Open').map(pr => (
              <div key={pr.id} className="flex items-center justify-between bg-slate-950 rounded-lg p-3 border border-slate-850 mb-2">
                <div className="flex items-center gap-3">
                  <GitMerge className="h-4 w-4 text-blue-400" />
                  <div>
                    <span className="text-xs font-bold text-white">PR #{pr.number}</span>
                    <span className="text-[10px] text-slate-400 ml-2">{pr.title}</span>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded ${
                    pr.analysis.riskScore === 'Critical' || pr.analysis.riskScore === 'High'
                      ? 'bg-red-500/10 text-red-400 border border-red-500/20' : 'bg-amber-500/10 text-amber-400'
                  }`}>
                    {pr.analysis.riskScore}
                  </span>
                  <span className="text-[10px] text-slate-500">
                    {pr.approvedBy ? `Approved by ${pr.approvedBy}` : 'Awaiting approval'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
