"use client";

import { createContext, useContext } from "react";

export interface TeamIdentity {
  id: string;
  tenant_id: string;
  tenant_name: string;
  name: string;
  email: string;
  role: "admin" | "lawyer";
}
export const TeamSession = createContext<TeamIdentity | null>(null);
export const useTeamSession = () => useContext(TeamSession);
