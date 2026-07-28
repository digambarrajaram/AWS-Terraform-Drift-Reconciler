import { create } from 'zustand';

interface AuthState {
  /** True when the user needs to supply (or re-supply) the API access token. */
  needsToken: boolean;
  setNeedsToken: (v: boolean) => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  needsToken: false,
  setNeedsToken: (v) => set({ needsToken: v }),
}));
