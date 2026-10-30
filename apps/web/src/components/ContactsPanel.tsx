"use client";

import { useCallback, useEffect, useState } from "react";
import { ContactBadges } from "./ContactBadges";
import { backendFetch } from "@/lib/client-api";
import type { ContactTag, Conversation, OfficeContact } from "@/lib/types";

async function request(path: string, init?: RequestInit) {
  const response = await backendFetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : "Confira os dados e tente novamente.",
    );
  }
  return response.status === 204 ? null : response.json();
}

const blank = {
  name: "",
  phone_number: "",
  human_only: false,
  tag_ids: [] as string[],
};
const field = "rounded-sm border border-line bg-ground px-3 py-2 text-sm";

export function ContactsPanel({
  initialConversation,
  onClose,
  onChanged,
}: {
  initialConversation?: Conversation | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [contacts, setContacts] = useState<OfficeContact[]>([]);
  const [tags, setTags] = useState<ContactTag[]>([]);
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<string | null>(
    initialConversation?.contact_id ?? null,
  );
  const [form, setForm] = useState({
    ...blank,
    name: initialConversation?.contact_name ?? "",
    phone_number: initialConversation?.contact_phone_number ?? "",
    human_only: initialConversation?.human_only ?? false,
    tag_ids: initialConversation?.tags?.map((t) => t.id) ?? [],
  });
  const [tagForm, setTagForm] = useState({ name: "", color: "#2563EB" });
  const [tagEditing, setTagEditing] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const load = useCallback(
    async () =>
      Promise.all([
        request(
          `contacts?q=${encodeURIComponent(q)}&limit=50&offset=${offset}`,
        ),
        request("contact-tags"),
      ]),
    [q, offset],
  );
  useEffect(() => {
    let active = true;
    setLoading(true);
    const timer = setTimeout(() => {
      void load()
        .then(([items, labels]) => {
          if (active) {
            setContacts(items);
            setTags(labels);
            setError("");
          }
        })
        .catch((e) => {
          if (active) setError(e.message);
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    }, 200);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [load, revision]);
  const mutate = async (action: () => Promise<unknown>, success: string) => {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      setNotice(success);
      setRevision((v) => v + 1);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section
      aria-label="Contatos e tags"
      className="flex-1 overflow-y-auto p-5"
    >
      <div className="mb-5 flex items-center justify-between gap-3">
        <h2 className="font-display text-2xl">Contatos e tags</h2>
        <button onClick={onClose} className="text-accent underline">
          Voltar às conversas
        </button>
      </div>
      <p className="mb-4 text-sm text-muted">
        Cadastre números antes do primeiro contato. As tags aparecem apenas para
        a equipe.
      </p>
      {error && (
        <p role="alert" className="mb-3 text-danger">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="mb-3 text-accent">
          {notice}
        </p>
      )}
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-5">
          <form
            className="space-y-3 rounded border border-line bg-surface p-4"
            onSubmit={(e) => {
              e.preventDefault();
              void mutate(async () => {
                const saved = await request(
                  editing ? `contacts/${editing}` : "contacts",
                  {
                    method: editing ? "PUT" : "POST",
                    body: JSON.stringify(form),
                  },
                );
                setEditing(saved.id);
              }, "Contato salvo. A regra já vale para novas mensagens e conversas existentes.");
            }}
          >
            <h3 className="font-medium">
              {editing ? "Editar contato" : "Novo contato"}
            </h3>
            <label className="block text-sm">
              Nome ou identificação
              <input
                className={`${field} mt-1 block w-full`}
                maxLength={120}
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </label>
            <label className="block text-sm">
              WhatsApp com código do país
              <input
                required
                type="tel"
                placeholder="+55 79 99999-9999"
                maxLength={40}
                className={`${field} mt-1 block w-full`}
                value={form.phone_number}
                onChange={(e) =>
                  setForm({ ...form, phone_number: e.target.value })
                }
              />
            </label>
            <fieldset>
              <legend className="mb-2 text-sm">Tags</legend>
              <div className="flex flex-wrap gap-3">
                {tags.map((tag) => (
                  <label key={tag.id} className="text-sm">
                    <input
                      type="checkbox"
                      checked={form.tag_ids.includes(tag.id)}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          tag_ids: e.target.checked
                            ? [...form.tag_ids, tag.id]
                            : form.tag_ids.filter((id) => id !== tag.id),
                        })
                      }
                    />{" "}
                    {tag.name}
                  </label>
                ))}
                {!tags.length && (
                  <p className="text-sm text-muted">
                    Crie uma tag abaixo para organizar seus contatos.
                  </p>
                )}
              </div>
            </fieldset>
            <label className="block text-sm">
              <input
                type="checkbox"
                checked={form.human_only}
                onChange={(e) =>
                  setForm({ ...form, human_only: e.target.checked })
                }
              />{" "}
              Atendimento exclusivamente humano
            </label>
            <p className="text-xs text-muted">
              A IA e as mensagens automáticas de cobrança ficam bloqueadas.
              Desativar esta opção não devolve uma conversa à IA
              automaticamente.
            </p>
            <div className="flex flex-wrap gap-3">
              <button
                disabled={busy}
                className="rounded bg-accent px-3 py-2 text-white disabled:opacity-50"
              >
                {busy ? "Salvando…" : "Salvar contato"}
              </button>
              <button
                type="button"
                disabled={busy}
                className="underline"
                onClick={() => {
                  setEditing(null);
                  setForm(blank);
                  setNotice("");
                }}
              >
                Novo contato
              </button>
              {editing && (
                <button
                  type="button"
                  disabled={busy}
                  className="text-danger underline"
                  onClick={() => {
                    if (
                      window.confirm(
                        "Remover este cadastro? O histórico fica preservado. Novas conversas deixarão de receber esta regra.",
                      )
                    )
                      void mutate(async () => {
                        await request(`contacts/${editing}`, {
                          method: "DELETE",
                        });
                        setEditing(null);
                        setForm(blank);
                      }, "Cadastro removido. Conversas em atendimento humano continuam pausadas.");
                  }}
                >
                  Remover cadastro
                </button>
              )}
            </div>
          </form>
          <form
            className="space-y-3 rounded border border-line p-4"
            onSubmit={(e) => {
              e.preventDefault();
              void mutate(async () => {
                await request(
                  tagEditing ? `contact-tags/${tagEditing}` : "contact-tags",
                  {
                    method: tagEditing ? "PUT" : "POST",
                    body: JSON.stringify(tagForm),
                  },
                );
                setTagEditing(null);
                setTagForm({ name: "", color: "#2563EB" });
              }, "Tag salva.");
            }}
          >
            <h3 className="font-medium">
              {tagEditing ? "Editar tag" : "Criar tag"}
            </h3>
            <div className="flex items-end gap-2">
              <label className="flex-1 text-sm">
                Nome da tag
                <input
                  required
                  maxLength={50}
                  value={tagForm.name}
                  className={`${field} mt-1 block w-full`}
                  onChange={(e) =>
                    setTagForm({ ...tagForm, name: e.target.value })
                  }
                />
              </label>
              <label className="text-sm">
                Cor
                <input
                  type="color"
                  aria-label="Cor da tag"
                  value={tagForm.color}
                  className="block h-10 w-12"
                  onChange={(e) =>
                    setTagForm({ ...tagForm, color: e.target.value })
                  }
                />
              </label>
            </div>
            <button disabled={busy} className="text-accent underline">
              Salvar tag
            </button>
            {tagEditing && (
              <button
                type="button"
                className="ml-3 underline"
                onClick={() => {
                  setTagEditing(null);
                  setTagForm({ name: "", color: "#2563EB" });
                }}
              >
                Cancelar edição
              </button>
            )}
            <ul className="space-y-2">
              {tags.map((tag) => (
                <li key={tag.id} className="flex items-center gap-3 text-sm">
                  <ContactBadges tags={[tag]} />
                  <button
                    type="button"
                    onClick={() => {
                      setTagEditing(tag.id);
                      setTagForm({ name: tag.name, color: tag.color });
                    }}
                    aria-label={`Editar tag ${tag.name}`}
                    className="underline"
                  >
                    Editar
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    aria-label={`Excluir tag ${tag.name}`}
                    className="text-danger underline"
                    onClick={() => {
                      if (
                        window.confirm(
                          `Excluir a tag “${tag.name}”? As regras de atendimento continuam iguais.`,
                        )
                      )
                        void mutate(async () => {
                          await request(`contact-tags/${tag.id}`, {
                            method: "DELETE",
                          });
                          setForm((f) => ({
                            ...f,
                            tag_ids: f.tag_ids.filter((id) => id !== tag.id),
                          }));
                        }, "Tag removida.");
                    }}
                  >
                    Excluir
                  </button>
                </li>
              ))}
            </ul>
          </form>
        </div>
        <div>
          <label className="block text-sm">
            Pesquisar contatos
            <input
              className={`${field} my-2 block w-full`}
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setOffset(0);
              }}
              placeholder="Nome ou número"
            />
          </label>
          {loading ? (
            <p role="status">Carregando…</p>
          ) : (
            <ul className="divide-y divide-line">
              {contacts.map((contact) => (
                <li key={contact.id}>
                  <button
                    className="w-full space-y-2 py-3 text-left"
                    disabled={busy}
                    onClick={() => {
                      setEditing(contact.id);
                      setForm({
                        name: contact.name,
                        phone_number: contact.phone_number,
                        human_only: contact.human_only,
                        tag_ids: contact.tags.map((t) => t.id),
                      });
                      setNotice("");
                    }}
                  >
                    <span className="block font-medium">
                      {contact.name || `+${contact.phone_number}`}
                    </span>
                    <span className="block text-sm text-muted">
                      +{contact.phone_number}
                    </span>
                    <ContactBadges
                      tags={contact.tags}
                      humanOnly={contact.human_only}
                    />
                  </button>
                </li>
              ))}
            </ul>
          )}
          {!loading && !contacts.length && (
            <p className="text-muted">Nenhum contato encontrado.</p>
          )}
          <div className="mt-4 flex gap-4">
            <button
              disabled={!offset || loading}
              className="disabled:opacity-40"
              onClick={() => setOffset((n) => Math.max(0, n - 50))}
            >
              Anterior
            </button>
            <button
              disabled={contacts.length < 50 || loading}
              className="disabled:opacity-40"
              onClick={() => setOffset((n) => n + 50)}
            >
              Próxima página
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
