"""Leitura pontual do Drive. Tokens ficam somente na memória desta requisição."""

from dataclasses import dataclass
from pathlib import Path

import httpx

from app.core.config import settings
from app.services.upload_storage import InvalidUploadContentError, display_filename

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
GOOGLE_DOC = "application/vnd.google-apps.document"
MIME_EXTENSIONS = {"application/pdf": ".pdf", DOCX_MIME: ".docx", "text/plain": ".txt"}


class DriveError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.status = status
        super().__init__(message)


@dataclass(frozen=True)
class DriveFile:
    id: str
    filename: str
    mime_type: str
    version: str
    native_document: bool


def check_response(response: httpx.Response) -> None:
    if response.status_code == 401:
        # 401 é reservado à sessão Advoxs (o proxy faria logout).
        raise DriveError("A autorização do Google expirou. Selecione os arquivos novamente.", 403)
    if response.status_code in (403, 404):
        raise DriveError(
            "Arquivo indisponível ou sem permissão para download. Confira o acesso no Drive.", 403
        )
    if response.status_code == 429 or response.status_code >= 500:
        raise DriveError("O Google Drive está indisponível. Tente novamente em instantes.", 503)
    if not response.is_success:
        raise DriveError("Não foi possível baixar ou converter este arquivo do Google Drive.", 400)


class GoogleDrive:
    def __init__(self, http: httpx.AsyncClient, token: str):
        if not token or len(token) > 4096 or any(c.isspace() for c in token):
            raise DriveError("Autorize o acesso ao Google Drive novamente.", 403)
        self.http = http
        self.headers = {"Authorization": f"Bearer {token}"}

    async def metadata(self, file_id: str) -> DriveFile:
        # file_id é validado pelo schema; nenhuma URL fornecida pelo usuário é acessada.
        response = await self.http.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            headers=self.headers,
            params={
                "fields": "id,name,mimeType,size,version,trashed,capabilities(canDownload)",
                "supportsAllDrives": "true",
            },
        )
        check_response(response)
        data = response.json()
        if data.get("trashed") or not data.get("capabilities", {}).get("canDownload", False):
            raise DriveError("Este arquivo foi excluído ou não permite download.", 403)
        native = data.get("mimeType") == GOOGLE_DOC
        mime = DOCX_MIME if native else data.get("mimeType")
        if mime not in MIME_EXTENSIONS:
            raise DriveError(
                "Formato não suportado. Selecione PDF, DOCX, TXT ou Documentos Google."
            )
        try:
            filename = display_filename(data.get("name", ""))
        except InvalidUploadContentError as exc:
            raise DriveError(str(exc)) from None
        if native:
            filename = filename[:250] + ".docx"
        elif Path(filename).suffix.lower() != MIME_EXTENSIONS[mime]:
            raise DriveError("O nome e o formato do arquivo no Drive não correspondem.")
        if int(data.get("size", 0)) > settings.kb_max_file_size_bytes:
            raise DriveError("O arquivo excede o limite de tamanho da base de conhecimento.", 413)
        if not data.get("version"):
            raise DriveError(
                "Não foi possível verificar a versão do arquivo. Tente novamente.", 503
            )
        return DriveFile(file_id, filename, mime, str(data["version"]), native)

    async def download(self, file: DriveFile) -> bytes:
        url = f"https://www.googleapis.com/drive/v3/files/{file.id}"
        params = {"alt": "media", "supportsAllDrives": "true"}
        if file.native_document:
            url += "/export"
            params = {"mimeType": DOCX_MIME}
        async with self.http.stream("GET", url, params=params, headers=self.headers) as response:
            check_response(response)
            data = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65536):
                if len(data) + len(chunk) > settings.kb_max_file_size_bytes:
                    raise DriveError(
                        "O arquivo excede o limite de tamanho da base de conhecimento.", 413
                    )
                data.extend(chunk)
        if not data:
            raise DriveError("O arquivo está vazio.")
        # Evita rotular bytes de uma edição simultânea como a versão aprovada.
        latest = await self.metadata(file.id)
        if latest.version != file.version:
            raise DriveError("O documento mudou durante a importação. Selecione-o novamente.", 409)
        return bytes(data)
