import { isSameOrigin } from "@/lib/request-origin";
import { NextResponse } from "next/server";
import { identityFetch } from "@/lib/server-session";

export async function POST(request: Request) {
  if ( !isSameOrigin(request))
    return NextResponse.json({ detail: "Origem inválida." }, { status: 403 });
  try {
    const body = await request.json();
    const action = body.action === "inspect" ? "inspect" : "accept";
    const response = await identityFetch(`team-invitations/${action}`, {
      method: "POST", body: JSON.stringify({ token: body.token, password: body.password }),
    });
    return NextResponse.json(await response.json(), {
      status: response.status, headers: { "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json({ detail: "Não foi possível conectar. Tente novamente." }, { status: 502 });
  }
}
