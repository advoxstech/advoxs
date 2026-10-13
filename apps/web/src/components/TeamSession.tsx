"use client";

import { createContext, useContext } from "react";

export interface TeamIdentity {
  id: string;
  name: string;
  email: string;
  role: "admin" | "lawyer";
}
export const TeamSession = createContext<TeamIdentity | null>(null);
export const useTeamSession = () => useContext(TeamSession);
