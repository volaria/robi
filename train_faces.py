#!/usr/bin/env python3
"""
train_faces.py — Yüz tanıma modelini yeniden eğitir.
ROBI çalışırken bile çalıştırılabilir; bir sonraki restart'ta yeni model aktif olur.
Kullanım: python train_faces.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from vision.face_recognize import train

print("=" * 50)
print("ROBI Yüz Tanıma — Yeniden Eğitim")
print("=" * 50)

ok = train(force=True)

if ok:
    print("\n✅ Eğitim tamamlandı. ROBI'yi yeniden başlatınca aktif olur.")
else:
    print("\n❌ Eğitim başarısız. Model dosyalarını kontrol et.")
