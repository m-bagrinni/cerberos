import os
import subprocess
import shutil
from django.conf import settings

PDF_CACHE_DIR = '/app/tmp_pdf'
LO_PROFILE_DIR = '/app/tmp_pdf/lo_profile'

def convert_to_pdf(file_path):
    """Конвертирует файл в PDF через LibreOffice. Возвращает путь к PDF или None."""
    if not file_path or not os.path.exists(file_path):
        return None
        
    ext = os.path.splitext(file_path)[1].lower()
    
    # PDF и картинки не конвертируем
    if ext in ['.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp']:
        return file_path

    # Для офисных документов используем LibreOffice
    if ext in ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods', '.rtf']:
        os.makedirs(PDF_CACHE_DIR, exist_ok=True)
        
        cmd = [
            'libreoffice', '--headless',
            f'-env:UserInstallation=file://{LO_PROFILE_DIR}',
            '--convert-to', 'pdf',
            '--outdir', PDF_CACHE_DIR,
            file_path
        ]
        
        try:
            subprocess.run(cmd, check=True, timeout=60, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as e:
            print(f"Ошибка конвертации {file_path}: {e}")
            return None
            
        base = os.path.splitext(os.path.basename(file_path))[0] + '.pdf'
        converted_path = os.path.join(PDF_CACHE_DIR, base)
        
        if os.path.exists(converted_path):
            return converted_path
            
    return None
