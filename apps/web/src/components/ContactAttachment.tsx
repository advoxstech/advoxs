"use client";

import { useState } from "react";
import { backendFetch } from "@/lib/client-api";
import type { Message } from "@/lib/types";

export function ContactAttachment({
  conversationId,
  message,
}: {
  conversationId: string;
  message: Message;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const download = async () => {
    setBusy(true);
    setError("");
    try {
      const response = await backendFetch(
        `conversations/${conversationId}/messages/${message.id}/attachment`,
      );
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(
          typeof body.detail === "string"
            ? body.detail
            : "Não foi possível baixar o anexo.",
        );
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download =
        response.headers
          .get("content-disposition")
          ?.match(/filename="([^"]+)"/)?.[1] ?? "anexo";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <button
        type="button"
        disabled={busy}
        onClick={() => void download()}
        className="break-words text-left underline decoration-dotted underline-offset-2 hover:text-accent disabled:opacity-50"
      >
        {busy ? "Baixando anexo…" : message.content || "Baixar anexo"}
      </button>
      {error && (
        <p role="alert" className="mt-1 text-xs text-danger">
          {error}
        </p>
      )}
    </>
  );
}
