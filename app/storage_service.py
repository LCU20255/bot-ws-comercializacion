import os
import uuid
import logging
from pathlib import Path
from typing import Optional

from app.config import BASE_DIR, SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "siscomer")

class StorageService:
    def __init__(self):
        self.client = None
        self.bucket = STORAGE_BUCKET
        self._init_supabase()

    def _init_supabase(self):
        url = (SUPABASE_URL or "").strip()
        key = (SUPABASE_KEY or "").strip()
        if url and key:
            try:
                from supabase import create_client
                self.client = create_client(url, key)
                logger.info(f"Supabase Storage conectado exitosamente (Bucket: {self.bucket})")
            except Exception as e:
                logger.warning(f"No se pudo inicializar cliente de Supabase Storage: {e}")
                self.client = None
        else:
            self.client = None

    def is_configured(self) -> bool:
        return self.client is not None

    def upload_image(
        self,
        file_bytes: bytes,
        filename: str,
        folder: str = "catalog",
        content_type: str = "image/jpeg"
    ) -> str:
        """
        Sube una imagen a Supabase Storage en la carpeta especificada.
        Si Supabase no está configurado o falla, guarda localmente en static/images como fallback seguro.
        """
        safe_name = filename.replace(" ", "_")
        storage_path = f"{folder}/{safe_name}" if folder else safe_name

        # 1. Intentar subir a Supabase Storage
        if self.client:
            try:
                # Intentar crear bucket si no existiera
                try:
                    self.client.storage.create_bucket(self.bucket, options={"public": True})
                except Exception:
                    pass

                self.client.storage.from_(self.bucket).upload(
                    path=storage_path,
                    file=file_bytes,
                    file_options={"content-type": content_type, "upsert": "true"}
                )

                public_url = self.client.storage.from_(self.bucket).get_public_url(storage_path)
                logger.info(f"Imagen subida exitosamente a Supabase Storage: {public_url}")
                return public_url
            except Exception as e:
                logger.error(f"Error subiendo imagen a Supabase Storage ({storage_path}): {e}. Aplicando fallback local.")

        # 2. Fallback local a app/static/images/
        images_dir = BASE_DIR / "app" / "static" / "images"
        if folder:
            target_dir = images_dir / folder
        else:
            target_dir = images_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        local_file_path = target_dir / safe_name
        with open(local_file_path, "wb") as f:
            f.write(file_bytes)

        local_url = f"/static/images/{folder}/{safe_name}" if folder else f"/static/images/{safe_name}"
        return local_url

    def upload_catalog_image(self, file_bytes: bytes, filename: str, content_type: str = "image/jpeg") -> str:
        return self.upload_image(file_bytes, filename, folder="catalog", content_type=content_type)

    def upload_receipt_image(self, file_bytes: bytes, filename: str, content_type: str = "image/jpeg") -> str:
        return self.upload_image(file_bytes, filename, folder="receipts", content_type=content_type)

storage_service = StorageService()
