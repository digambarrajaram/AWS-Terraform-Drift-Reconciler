import React, { useState, useEffect } from 'react';

type Props = {
  visible: boolean;
  title?: string;
  initial?: string;
  onConfirm: (approver: string) => void;
  onCancel: () => void;
};

export default function ApproveModal({ visible, title = 'Approve Action', initial = '', onConfirm, onCancel }: Props) {
  const [name, setName] = useState(initial);

  useEffect(() => {
    setName(initial);
  }, [initial, visible]);

  if (!visible) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60" onClick={onCancel} />
      <div className="relative w-full max-w-md rounded-xl border border-slate-800 bg-slate-900/95 p-6 shadow-2xl">
        <h3 className="text-lg font-bold text-white mb-3">{title}</h3>
        <p className="text-sm text-slate-400 mb-4">Provide your name for the audit trail; this will be recorded with the action.</p>
        <input
          className="w-full rounded-md border border-slate-700 bg-slate-950/50 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
          placeholder="Your name (e.g. Jane Doe)"
          value={name}
          onChange={e => setName(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') onConfirm(name.trim()); }}
        />

        <div className="mt-4 flex justify-end gap-2">
          <button className="rounded-md px-3 py-1.5 text-sm bg-slate-700 hover:bg-slate-600 text-white" onClick={onCancel}>Cancel</button>
          <button className="rounded-md px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-60" onClick={() => onConfirm(name.trim())} disabled={name.trim().length === 0}>Confirm</button>
        </div>
      </div>
    </div>
  );
}
