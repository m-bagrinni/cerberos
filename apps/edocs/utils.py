import logging
import os
import shutil
import subprocess
import tempfile

logger = logging.getLogger(__name__)

# Рабочий каталог конвертации — во временном каталоге контейнера: /app смонтирован с хоста
# и может быть недоступен на запись пользователю контейнера. Готовые PDF хранятся в MEDIA.
PDF_CACHE_DIR = os.environ.get("EDOCS_CONVERT_DIR") or os.path.join(tempfile.gettempdir(), "cerber_pdf")
LO_PROFILE_DIR = os.path.join(PDF_CACHE_DIR, "lo_profile")
OFFICE_EXT = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".rtf", ".txt", ".csv"}


def convert_to_pdf(file_path):
    """
    Офисный файл → PDF через LibreOffice. Возвращает путь к PDF во временном каталоге
    (вызывающий удаляет каталог целиком) или None.
    Каждая конвертация — в свой каталог: одинаковые имена файлов не перетирают друг друга.
    """
    if not file_path or not os.path.exists(file_path):
        return None
    if os.path.splitext(file_path)[1].lower() not in OFFICE_EXT:
        return None
    try:
        os.makedirs(PDF_CACHE_DIR, exist_ok=True)
        out_dir = tempfile.mkdtemp(prefix="conv-", dir=PDF_CACHE_DIR)
    except OSError:
        logger.exception("edocs: нет каталога для конвертации %s", PDF_CACHE_DIR)
        return None
    cmd = ["libreoffice", "--headless", f"-env:UserInstallation=file://{LO_PROFILE_DIR}",
           "--convert-to", "pdf", "--outdir", out_dir, file_path]
    try:
        subprocess.run(cmd, check=True, timeout=90, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception:  # noqa: BLE001
        logger.exception("edocs: ошибка конвертации %s", file_path)
        shutil.rmtree(out_dir, ignore_errors=True)
        return None
    pdf = os.path.join(out_dir, os.path.splitext(os.path.basename(file_path))[0] + ".pdf")
    if not os.path.exists(pdf):
        shutil.rmtree(out_dir, ignore_errors=True)
        return None
    return pdf
