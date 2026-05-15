"""
WSGI entry point for PythonAnywhere.

วางไฟล์นี้ (หรือคัดลอกเนื้อหา) ลงในไฟล์ WSGI ที่ PythonAnywhere ให้มา
ตำแหน่งปกติ:  /var/www/<username>_pythonanywhere_com_wsgi.py

แก้ค่า PROJECT_DIR ให้ตรงกับ path จริงของโปรเจกต์บน PythonAnywhere
เช่น  /home/<username>/fianl104
"""
import os
import sys

# === ปรับ path นี้ให้ตรงกับโฟลเดอร์โปรเจกต์บน PythonAnywhere ===
PROJECT_DIR = "/home/YOUR_USERNAME/fianl104"

if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

# (แนะนำ) ตั้ง secret key ผ่าน environment variable
os.environ.setdefault("SMARTBUILD_SECRET", "change-me-to-a-long-random-string")

from app import app as application  # noqa: E402
