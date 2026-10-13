import { isSameOrigin } from "@/lib/request-origin";
import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { setAuthCookies } from "@/lib/auth";
import { identityFetch } from "@/lib/server-session";

export async function GET() {
  const response = await identityFetch("offices");
  return NextResponse.json(await response.json(), {
    status: response.status, headers: { "Cache-Control": "no-store" },
  });
}

export async function POST(request: Request) {
  if ( !isSameOrigin(request))
    return NextResponse.json({ detail: "Origem inválida." }, { status: 403 });
  const body = await request.json();
  const response = await identityFetch("offices/select", {
    method: "POST", body: JSON.stringify({ tenant_id: body.tenant_id }),
  });
  const value = await response.json();
  if (!response.ok) return NextResponse.json(value, { status: response.status });
  setAuthCookies(await cookies(), value);
  return NextResponse.json({ status: "selected" }, { headers: { "Cache-Control": "no-store" } });
}
