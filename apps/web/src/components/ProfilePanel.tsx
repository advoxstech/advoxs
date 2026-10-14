"use client";

import { useEffect, useState } from "react";

import { logout } from "@/app/conversas/actions";
import { backendFetch } from "@/lib/client-api";
import { useTeamSession } from "@/components/TeamSession";
import type { Profile } from "@/lib/types";

export function ProfilePanel() {
  const identity = useTeamSession();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [tenantName, setTenantName] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [nameSaved, setNameSaved] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [logoError, setLogoError] = useState<string | null>(null);
  const [logoVersion, setLogoVersion] = useState(0);
  const [letterheadVersion, setLetterheadVersion] = useState(0);
  const [letterheadError, setLetterheadError] = useState<string | null>(null);
  const [letterheadBusy, setLetterheadBusy] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const response = await backendFetch("profile");
        if (response.ok) {
          const body: Profile = await response.json();
          setProfile(body);
          setTenantName(body.tenant_name);
        }
      } finally {
        setLoaded(true);
      }
    }
    void load();
  }, []);

  async function handleSaveName() {
    setNameError(null);
    setNameSaved(false);
    const response = await backendFetch("profile", {
      method: "PATCH",
      body: JSON.stringify({ tenant_name: tenantName }),
    });
    if (response.ok) {
      const body: Profile = await response.json();
      setProfile(body);
      setNameSaved(true);
    } else {
      setNameError("Não foi possível salvar. Tente novamente.");
    }
  }

  async function handleChangePassword() {
    setPasswordError(null);
    if (newPassword !== confirmPassword) {
      setPasswordError("As senhas não coincidem.");
      return;
    }
    const response = await backendFetch("profile/password", {
      method: "POST",
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    });
    if (response.ok) {
      window.location.assign("/login?password_changed=1");
    } else {
      const body = await response.json().catch(() => null);
      setPasswordError(
        typeof body?.detail === "string" ? body.detail : "Não foi possível trocar a senha.",
      );
    }
  }

  async function handleUploadLogo(file: File) {
    setLogoError(null);
    const formData = new FormData();
    formData.append("file", file);
    const response = await backendFetch("profile/logo", { method: "POST", body: formData });
    if (response.ok) {
      const body: Profile = await response.json();
      setProfile(body);
      setLogoVersion((v) => v + 1);
    } else {
      setLogoError("Não foi possível enviar a logo. Envie um PNG ou JPG de até 2 MB.");
    }
  }

  async function handleUploadLetterhead(file: File) {
    setLetterheadError(null);
    setLetterheadBusy(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await backendFetch("profile/letterhead", {
        method: "POST", body: formData,
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail || "Não foi possível enviar o timbrado.");
      }
      setProfile((current) => current && { ...current, has_letterhead: true });
      setLetterheadVersion((version) => version + 1);
    } catch (error) {
      setLetterheadError(error instanceof Error ? error.message : "Falha no envio.");
    } finally {
      setLetterheadBusy(false);
    }
  }

  async function handleRemoveLetterhead() {
    setLetterheadError(null);
    setLetterheadBusy(true);
    try {
      const response = await backendFetch("profile/letterhead", { method: "DELETE" });
      if (!response.ok) throw new Error("Não foi possível remover o timbrado.");
      setProfile((current) => current && { ...current, has_letterhead: false });
    } catch (error) {
      setLetterheadError(error instanceof Error ? error.message : "Falha na remoção.");
    } finally {
      setLetterheadBusy(false);
    }
  }

  if (!loaded) {
    return <p className="p-8 text-sm text-muted">Carregando...</p>;
  }
  if (!profile) {
    return <p className="p-8 text-sm text-danger">Não foi possível carregar o perfil.</p>;
  }

  return (
    <div className="flex flex-col gap-8 p-8">
      <section className="flex flex-col gap-4 rounded-sm border border-line bg-surface p-6">
        <h2 className="font-display text-xl font-semibold text-ink">Dados do escritório</h2>

        <div className="flex items-center gap-4">
          {profile.has_logo ? (
            <img
              key={logoVersion}
              src="/api/backend/profile/logo"
              alt="Logo do escritório"
              className="h-16 w-16 rounded-sm object-cover"
            />
          ) : (
            <div className="flex h-16 w-16 items-center justify-center rounded-sm bg-ink font-display text-2xl font-semibold text-ground">
              A.
            </div>
          )}
          <label className="cursor-pointer rounded-sm border border-line px-3 py-1.5 text-sm text-ink hover:bg-ground">
            Alterar logo
            <input
              type="file"
              accept="image/png,image/jpeg"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void handleUploadLogo(file);
              }}
            />
          </label>
        </div>
        {logoError && <p className="text-sm text-danger">{logoError}</p>}

        <div className="flex flex-col gap-1.5">
          <label htmlFor="tenant-name" className="text-sm font-medium text-ink">
            Nome do escritório
          </label>
          <input
            id="tenant-name"
            type="text"
            value={tenantName}
            onChange={(event) => setTenantName(event.target.value)}
            className="rounded-sm border border-line bg-ground px-3 py-2.5 text-sm"
          />
        </div>
        {nameError && <p className="text-sm text-danger">{nameError}</p>}
        {nameSaved && <p className="text-sm text-accent">Nome salvo.</p>}
        <button
          type="button"
          onClick={() => void handleSaveName()}
          className="self-start rounded-sm bg-accent px-4 py-2 text-sm font-medium text-surface hover:bg-ink"
        >
          Salvar nome
        </button>

        <p className="text-sm text-muted">
          Usuário: {profile.user_name} (<span>{profile.user_email}</span>)
        </p>
      </section>

      <section className="flex flex-col gap-4 rounded-sm border border-line bg-surface p-6">
        <h2 className="font-display text-xl font-semibold text-ink">Timbrado dos documentos</h2>
        <p className="text-sm text-muted">
          {profile.has_letterhead
            ? "Este PDF será usado nos documentos gerados para este escritório."
            : "Nenhum timbrado cadastrado. Os documentos serão gerados sem timbrado."}
        </p>
        {profile.has_letterhead && (
          <iframe
            key={letterheadVersion}
            title="Prévia do timbrado"
            src={`/api/backend/profile/letterhead?v=${letterheadVersion}`}
            className="h-96 w-full rounded-sm border border-line"
          />
        )}
        {identity?.role === "admin" && (
          <div className="flex flex-wrap items-center gap-3">
            <label className="cursor-pointer rounded-sm bg-accent px-4 py-2 text-sm font-medium text-surface hover:bg-ink">
              {profile.has_letterhead ? "Substituir PDF" : "Enviar timbrado"}
              <input
                type="file"
                accept="application/pdf,.pdf"
                disabled={letterheadBusy}
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void handleUploadLetterhead(file);
                  event.target.value = "";
                }}
              />
            </label>
            {profile.has_letterhead && (
              <button
                type="button"
                disabled={letterheadBusy}
                onClick={() => void handleRemoveLetterhead()}
                className="rounded-sm border border-danger px-4 py-2 text-sm text-danger disabled:opacity-50"
              >
                Remover timbrado
              </button>
            )}
          </div>
        )}
        <p className="text-xs text-muted">Use um PDF A4 vertical de uma página, com até 5 MB.</p>
        {letterheadError && <p className="text-sm text-danger">{letterheadError}</p>}
      </section>

      <section className="flex flex-col gap-4 rounded-sm border border-line bg-surface p-6">
        <h2 className="font-display text-xl font-semibold text-ink">Trocar senha</h2>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="current-password" className="text-sm font-medium text-ink">
            Senha atual
          </label>
          <input
            id="current-password"
            type="password"
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
            className="rounded-sm border border-line bg-ground px-3 py-2.5 text-sm"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="new-password" className="text-sm font-medium text-ink">
            Nova senha
          </label>
          <input
            id="new-password"
            type="password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            className="rounded-sm border border-line bg-ground px-3 py-2.5 text-sm"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="confirm-password" className="text-sm font-medium text-ink">
            Confirmar nova senha
          </label>
          <input
            id="confirm-password"
            type="password"
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
            className="rounded-sm border border-line bg-ground px-3 py-2.5 text-sm"
          />
        </div>
        {passwordError && <p className="text-sm text-danger">{passwordError}</p>}
        <button
          type="button"
          onClick={() => void handleChangePassword()}
          className="self-start rounded-sm bg-accent px-4 py-2 text-sm font-medium text-surface hover:bg-ink"
        >
          Trocar senha
        </button>
      </section>

      <section className="rounded-sm border border-danger/40 bg-surface p-6">
        <form action={logout}>
          <button
            type="submit"
            className="rounded-sm border border-danger px-4 py-2 text-sm font-medium text-danger hover:bg-danger/10"
          >
            Sair da conta
          </button>
        </form>
      </section>
    </div>
  );
}
