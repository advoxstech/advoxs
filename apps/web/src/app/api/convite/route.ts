import { NextResponse } from "next/server";
import { API_URL } from "@/lib/backend";

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const response = await fetch(`${API_URL}/api/v1/team-invitations/accept`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: body.token, password: body.password }),
      cache: "no-store",
    });
    return NextResponse.json(await response.json(), {
      status: response.status,
      headers: { "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json(
      { detail: "Não foi possível conectar. Tente novamente." },
      { status: 502 },
    );
  }
}
