// ============================================================
//  ROBI — R2D2 İlhamlı Masaüstü Robot Gövdesi
//  OpenSCAD Parametrik Model  |  v1.0
// ============================================================
//
//  Bileşenler:
//    • Raspberry Pi 4          85.6 × 56.5 × 17 mm
//    • Pi Camera Module 3      25 × 24 mm
//    • SG90 Servo              22.2 × 11.8 × 29.5 mm
//    • MAX7219 8×8 LED Matrix  32 × 32 mm modül
//    • PAM8403 Amplifikatör    33 × 23 mm
//    • INMP441 Mikrofon        18 × 18 mm breakout
//    • Hoparlör                52 mm çap, 4Ω 3W
//    • Güç                     USB-C (tabandan giriş)
//
//  Baskı Notları:
//    • Önerilen katman kalınlığı: 0.2 mm
//    • Dolgu: %20 gyroid
//    • Malzeme: PETG veya ABS (ısı dayanımı için)
//    • Destek: dome_head için gerekli; gövde+taban desteksiz
//
//  Parçalar (ayrı ayrı baskı alınacak):
//    1. base()         — taban
//    2. body()         — ana gövde
//    3. body_lid()     — gövde arka kapak (Pi erişimi)
//    4. neck()         — boyun / servo tutacağı
//    5. dome_head()    — dönen kafa
//    6. speaker_grille() — hoparlör ızgarası (gövde içine press-fit)
//    7. led_bezel()    — LED matrix çerçevesi
//
//  Alt kısımda render seçenekleri var — istediğin parçayı
//  yorum satırından çıkararak render et / STL export et.
// ============================================================

$fn = 72;
eps = 0.01;  // kesişim hataları için küçük tolerans


// ══════════════════════════════════════════════════════════
//  ANA PARAMETRELER  (buradan özelleştir)
// ══════════════════════════════════════════════════════════

// Gövde
body_od      = 122;   // dış çap
wall_t       =   3;   // duvar kalınlığı
body_h       = 100;   // gövde yüksekliği (tabansız)
body_id      = body_od - wall_t * 2;  // iç çap

// Taban
base_od      = 144;   // dış çap (stabilite için geniş)
base_h       =  14;   // yükseklik
base_wall    =   4;

// Boyun (servo bölgesi)
neck_od_bot  = body_od  - 6;  // alt çap (gövdeyle örtüşür)
neck_od_top  =  72;           // üst çap (dome ile örtüşür)
neck_h       =  22;

// Kafa (dome)
dome_od      =  96;   // dış çap
dome_wall    =   3;
dome_h       =  50;   // toplam yükseklik (silindir + küresel üst)
dome_cyl_h   =  18;   // alt silindirik bölüm yüksekliği

// ── Bileşen boyutları ─────────────────────────────────────

// Raspberry Pi 4
pi_w         = 85.6;
pi_d         = 56.5;
pi_hole_sp_x = 58.0;  // vida delikleri x aralığı
pi_hole_sp_y = 49.0;  // vida delikleri y aralığı
pi_hole_d    =  2.7;  // M2.5 vida deliği
pi_standoff  =   5;   // standoff yüksekliği

// SG90 Servo
sg90_w       = 22.2;
sg90_d       = 11.8;
sg90_h       = 29.5;
sg90_tab_sp  = 27.5;  // tab vida delikleri arası mesafe
sg90_tab_t   =  2.5;  // tab kalınlığı
sg90_tab_d   =  2.0;  // tab vida deliği
sg90_shaft_d =  4.8;  // mil çapı (küçük tolerans ekli)
sg90_shaft_h =   8;   // milin yüksekliği

// Pi Camera
cam_w        =  25;
cam_h        =  24;
cam_lens_d   =   8;   // lens deliği çapı
cam_recess   =   2;   // lens için hafif girintü

// MAX7219 LED Matrix
led_size     =  32;   // kare modül
led_depth    =  12;   // modül derinliği (arkadan)
led_tol      =   0.4; // sıkıştırma toleransı

// PAM8403 Amplifikatör
amp_w        =  33;
amp_d        =  23;
amp_h        =  14;

// INMP441 Mikrofon
mic_pcb      =  18;
mic_hole_d   =   3;   // delik — ses geçişi için

// Hoparlör (52mm)
spk_d        =  52;
spk_rim      =   3;   // flanş çapı (spk_d'ye eklenir)
spk_depth    =  18;   // derinlik (frame içi boşluk)

// Pi kamera ribbon kablo kanalı
rib_w        =  17;   // kablo genişliği
rib_t        =   2.5; // kablo kalınlığı

// USB-C delik (tabanda)
usbc_w       =  10;
usbc_h       =   4;

// Montaj vidaları
screw_d      =   2.7; // M3 vida deliği
boss_d       =   7;   // vida kulağı dış çapı


// ══════════════════════════════════════════════════════════
//  YARDIMCI MODÜLLER
// ══════════════════════════════════════════════════════════

// Kenarları yumuşatılmış kutu
module rounded_box(w, d, h, r=2) {
    hull()
        for (x=[-1,1], y=[-1,1])
            translate([x*(w/2-r), y*(d/2-r), 0])
                cylinder(r=r, h=h);
}

// Vida kulağı (standoff)
module boss(h, screw=screw_d) {
    difference() {
        cylinder(d=boss_d, h=h);
        cylinder(d=screw, h=h+eps);
    }
}

// Hoparlör ızgara deseni (konsantrik halkalar + çapraz)
module spk_grille_pattern(d, t) {
    difference() {
        cylinder(d=d, h=t);
        // Konsantrik halka delikleri
        for (r = [6, 11, 16, 19, 22])
            rotate_extrude($fn=72)
                translate([r, 0])
                    square([2.8, t+2*eps], center=true);
        // Merkez delik
        cylinder(d=5, h=t+2*eps);
    }
}


// ══════════════════════════════════════════════════════════
//  1. TABAN
// ══════════════════════════════════════════════════════════
module base() {
    color("SteelBlue")
    difference() {
        union() {
            // Dış form — hafif konik (R2D2 alt detayı)
            cylinder(d1=base_od, d2=base_od - 6, h=base_h);
            // Üst bilezik — gövde ile örtüşme
            translate([0, 0, base_h - 4])
                cylinder(d=body_od + 2, h=4);
        }

        // İç boşluk (kablo yönetimi)
        translate([0, 0, base_wall])
            cylinder(d=base_od - base_wall*2, h=base_h);

        // USB-C giriş deliği (öne doğru, tabana yakın)
        rotate([0, 0, 0])
        translate([base_od/2 - 1, 0, base_h * 0.45])
            rotate([0, 90, 0])
                rounded_box(usbc_w, usbc_h, base_wall * 2 + 2);

        // Kaymaz taban delikleri — silikon pençe için (4 adet)
        for (a = [45, 135, 225, 315])
            rotate([0, 0, a])
                translate([base_od/2 - 14, 0, -eps])
                    cylinder(d=6, h=3);

        // Gövde yerleşim çıkıntıları için iç chamfer
        translate([0, 0, base_h - 4 - eps])
            cylinder(d1=body_od - wall_t*2, d2=body_od + 3, h=4 + eps);
    }
}


// ══════════════════════════════════════════════════════════
//  2. ANA GÖVDE
// ══════════════════════════════════════════════════════════
module body() {
    color("SteelBlue")
    difference() {
        union() {
            cylinder(d=body_od, h=body_h);
            // Alt bilezik (tabana oturma)
            cylinder(d=body_od + 3, h=5);
            // Üst bilezik (boyuna geçiş)
            translate([0, 0, body_h - 5])
                cylinder(d=body_od + 2, h=5);
        }

        // ── İç boşluk ──
        translate([0, 0, wall_t])
            cylinder(d=body_id, h=body_h - wall_t + eps);

        // ── LED Matrix penceresi (ön-üst) ──
        // Eksen: 0° = ön
        rotate([0, 0, 0])
        translate([body_od/2 - wall_t/2, 0, body_h * 0.58])
            rotate([0, 90, 0])
                cube([led_size + led_tol*2, led_size + led_tol*2,
                      wall_t*2 + 2], center=true);

        // ── Hoparlör deliği (ön-alt) ──
        rotate([0, 0, 0])
        translate([body_od/2 - wall_t/2, 0, body_h * 0.27])
            rotate([0, 90, 0])
                cylinder(d=spk_d, h=wall_t*2 + 2, center=true);

        // ── INMP441 mikrofon deliği (sol yan, üst) ──
        rotate([0, 0, 80])
        translate([body_od/2, 0, body_h * 0.78])
            rotate([0, 90, 0])
                cylinder(d=mic_hole_d, h=wall_t*2 + 2, center=true);

        // ── Pi portları erişim açıklığı (arka) ──
        // USB-A, USB-C güç, HDMI × 2, ethernet, 3.5mm jack
        rotate([0, 0, 180])
        translate([body_od/2 - wall_t/2, 0, body_h * 0.22])
            rotate([0, 90, 0]) {
                // Tüm port grubu için tek büyük pencere
                // (kapak/panel ayrıca monte edilir)
                rounded_box(82, 22, wall_t*2 + 2);
            }

        // ── Havalandırma (sol ve sağ yan, 4 yuvarlak yuva grubu) ──
        for (a = [70, 110, 250, 290])
            rotate([0, 0, a])
                translate([body_od/2 - wall_t/2, 0, body_h * 0.38])
                    rotate([0, 90, 0])
                        for (i = [-1.5, -0.5, 0.5, 1.5])
                            translate([0, i * 7, 0])
                                cylinder(d=3.5, h=wall_t*2+2, center=true);

        // ── Servo montaj açıklığı (üst merkez — boyun altı) ──
        translate([0, 0, body_h - wall_t - eps])
            cylinder(d=neck_od_top - 4, h=wall_t + 2*eps);

        // ── Ribbon kablo kanalı (iç-üst, kameradan gövdeye) ──
        translate([-rib_w/2, -rib_t/2, body_h - 20])
            cube([rib_w, rib_t, 22]);
    }

    // ── İç standoff'lar (Pi 4 montajı, alt katman) ──
    // Pi, gövde merkezinin hafif arkasında (port erişimi için)
    color("SteelBlue")
    translate([-pi_hole_sp_x/2 + 5, -pi_hole_sp_y/2, wall_t])
        for (x=[0, pi_hole_sp_x], y=[0, pi_hole_sp_y])
            translate([x, y, 0])
                boss(pi_standoff);

    // ── İç standoff (PAM8403 amp) ──
    color("SteelBlue")
    translate([-amp_w/2, body_id/2 - amp_d - 4, wall_t])
        for (x=[0, amp_w - 8], y=[0, amp_d - 6])
            translate([x, y, 0])
                boss(pi_standoff);
}


// ══════════════════════════════════════════════════════════
//  3. GÖVDE ARKA KAPAĞI (Pi port erişimi için)
// ══════════════════════════════════════════════════════════
module body_lid() {
    color("SteelBlue", 0.8)
    difference() {
        // Kavisli panel — gövde eğrisine uyar
        rotate_extrude(angle=60, $fn=72)
            translate([body_od/2 - wall_t, 0])
                square([wall_t + 0.5, 52]);

        // USB-A portları (2×)
        translate([0, -15, 22])
            rotate([0, 90, 0])
                rounded_box(16, 14, body_od/2);

        // USB-C güç
        translate([0, 8, 10])
            rotate([0, 90, 0])
                rounded_box(usbc_w, usbc_h, body_od/2);

        // HDMI × 2
        translate([0, -14, 10])
            rotate([0, 90, 0])
                rounded_box(26, 8, body_od/2);

        // Audio jack
        translate([0, 12, 22])
            rotate([0, 90, 0])
                cylinder(d=7, h=body_od/2);
    }
}


// ══════════════════════════════════════════════════════════
//  4. BOYUN — SERVO TUTUCU
// ══════════════════════════════════════════════════════════
module neck() {
    color("MidnightBlue")
    difference() {
        union() {
            // Konik geçiş (gövde → dome)
            cylinder(d1=neck_od_bot, d2=neck_od_top, h=neck_h);
            // Alt bilezik (gövde üstüne oturur)
            cylinder(d=body_od - 1, h=4);
        }

        // İç boşluk
        translate([0, 0, -eps])
            cylinder(d1=neck_od_bot - wall_t*2,
                     d2=neck_od_top - wall_t*2, h=neck_h + 2*eps);

        // ── SG90 servo yuvası (merkez) ──
        translate([0, 0, (neck_h - sg90_h) / 2])
            cube([sg90_w + 0.5, sg90_d + 0.5, sg90_h + 1], center=true);

        // Servo tab genişlemesi
        translate([0, 0, (neck_h - sg90_h) / 2 + 2])
            cube([sg90_tab_sp + 4, sg90_d + 8, sg90_tab_t + 1], center=true);

        // Servo tab vida delikleri
        for (sx = [-sg90_tab_sp/2, sg90_tab_sp/2])
            translate([sx, 0, (neck_h - sg90_h) / 2 + 1])
                cylinder(d=sg90_tab_d, h=sg90_tab_t + 2);

        // ── Servo shaft çıkış deliği (üst) ──
        translate([0, 0, neck_h - 6])
            cylinder(d=sg90_shaft_d + 1, h=8);

        // ── Kablo geçiş deliği (servo kablosu, alta) ──
        translate([sg90_w/2 + 2, 0, 2])
            cylinder(d=5, h=neck_h);

        // ── Ribbon kablo kanalı (kameradan gövdeye) ──
        translate([-rib_w/2, -rib_t/2, -eps])
            cube([rib_w, rib_t, neck_h + 2*eps]);
    }
}


// ══════════════════════════════════════════════════════════
//  5. DÖNEN KAFA (DOME)
// ══════════════════════════════════════════════════════════
module dome_head() {
    color("White")
    difference() {
        union() {
            // Alt silindirik bölüm
            cylinder(d=dome_od, h=dome_cyl_h);

            // Üst küresel kubbe
            // (kürenin alt yarısı silindirik bölümle birleşir)
            translate([0, 0, dome_cyl_h]) {
                r = dome_od / 2;
                intersection() {
                    sphere(r=r);
                    translate([0, 0, 0])
                        cylinder(r=r + 1, h=r);
                }
            }
        }

        // ── İç boşluk ──
        translate([0, 0, dome_wall])
            cylinder(d=dome_od - dome_wall*2, h=dome_cyl_h - dome_wall + eps);

        translate([0, 0, dome_cyl_h + dome_wall]) {
            r = dome_od/2 - dome_wall;
            intersection() {
                sphere(r=r);
                translate([0, 0, 0])
                    cylinder(r=r+1, h=r);
            }
        }

        // ── Pi Kamera deliği (ön, göz seviyesi) ──
        // Kamera lens ekseni, dome merkezinin sağında ve yukarısında
        rotate([0, 0, 0])  // öne bakan
        translate([dome_od/2, 0, dome_cyl_h * 0.6])
            rotate([0, 90, 0]) {
                // Lens geçiş deliği
                cylinder(d=cam_lens_d, h=dome_wall*2 + 2, center=true);
                // Kamera PCB yuvası (iç taraf)
                translate([0, 0, -(dome_wall + 1)])
                    cube([cam_w + 0.5, cam_h + 0.5, dome_wall + 1], center=true);
            }

        // ── R2D2 referans — küçük optik detay delikleri (ön sol) ──
        for (a = [25, 50]) {
            rotate([0, 0, a])
            translate([dome_od/2, 0, dome_cyl_h * 0.75])
                rotate([0, 90, 0])
                    cylinder(d=5, h=dome_wall*2 + 2, center=true);
        }

        // ── Servo shaft bağlantı deliği (alt merkez) ──
        translate([0, 0, -eps])
            cylinder(d=sg90_shaft_d, h=dome_wall + 4);

        // Horn vidası için bağlantı boşluğu
        translate([0, 0, -eps])
            cylinder(d=14, h=3);   // horn çapı için girintü

        // ── Ribbon kablo kanalı (alt + iç) ──
        // Kablo, servo ekseni yanından geçerek dome içine girer
        translate([-rib_w/2, dome_od/2 - rib_t - dome_wall - 2, -eps])
            cube([rib_w, rib_t + 2, dome_wall + 6]);
    }

    // ── Kamera montaj platformu (iç, yapıştırma / vida ile) ──
    color("LightGray")
    translate([dome_od/2 - dome_wall - cam_w - 1, -cam_h/2, dome_cyl_h * 0.55 - cam_h/2])
        difference() {
            cube([cam_w + 3, cam_h, 3]);
            // Kamera PCB vida delikleri (21 × 12.5mm pattern)
            for (x=[2, cam_w], y=[1.5, cam_h - 1.5])
                translate([x, y, -eps])
                    cylinder(d=2, h=4);
        }
}


// ══════════════════════════════════════════════════════════
//  6. HOPARLÖR IZGARA PANELİ  (gövde içine press-fit)
// ══════════════════════════════════════════════════════════
module speaker_grille() {
    color("DimGray")
    difference() {
        // Dış çerçeve — gövde deliğine oturur
        cylinder(d=spk_d + spk_rim*2, h=wall_t + 1);
        // Izgara deseni
        spk_grille_pattern(spk_d - 2, wall_t + 1 + eps);
        // Press-fit pimi için kenar girintisi
        translate([0, 0, wall_t - 1])
            cylinder(d1=spk_d + spk_rim*2 - 1,
                     d2=spk_d + spk_rim*2 + 0.5, h=1.5);
    }
}


// ══════════════════════════════════════════════════════════
//  7. LED MATRIX BEZELİ  (gövde ön penceresine press-fit)
// ══════════════════════════════════════════════════════════
module led_bezel() {
    bezel_t = 2;
    color("DimGray")
    difference() {
        // Dış çerçeve
        cube([led_size + 8, led_size + 8, bezel_t + 1], center=true);
        // LED penceresİ (hafif transparan kapak için boşluk)
        cube([led_size, led_size, bezel_t + 2], center=true);
        // Press-fit tırnaklar (4 köşe)
        for (x=[-1,1], y=[-1,1])
            translate([x*(led_size/2 + 2), y*(led_size/2 + 2), 0])
                cylinder(d=3, h=bezel_t + 2, center=true);
    }
}


// ══════════════════════════════════════════════════════════
//  BOYUT ÖZETİ (konsol çıktısı)
// ══════════════════════════════════════════════════════════
echo("══════════════════════════════");
echo("ROBI Gövde — Boyut Özeti");
echo("══════════════════════════════");
echo(str("Taban çapı    : ", base_od, " mm"));
echo(str("Gövde çapı    : ", body_od, " mm"));
echo(str("Kafa çapı     : ", dome_od, " mm"));
echo(str("Toplam yüksek.: ", base_h + body_h + neck_h + dome_cyl_h + dome_od/2, " mm"));
echo(str("Pi uyum çapı  : ", body_id, " mm  (Pi 85.6mm → OK: ", body_id > 86, ")"));
echo(str("Spk uyum      : ", spk_d, "mm hoparlör → ", body_od/2, "mm yarıçap gövde OK"));
echo("══════════════════════════════");


// ══════════════════════════════════════════════════════════
//  RENDER SEÇENEKLERİ
//  İstediğin bölümün önündeki "//" yi kaldır, F6 ile render et
// ══════════════════════════════════════════════════════════

// ── TAM MONTAJ GÖRÜNTÜsü ──────────────────────────────────
module full_assembly() {
    // Taban
    base();

    // Gövde (taban üzerinde)
    translate([0, 0, base_h])
        body();

    // Boyun (gövde üzerinde)
    translate([0, 0, base_h + body_h])
        neck();

    // Kafa (boyun üzerinde)
    translate([0, 0, base_h + body_h + neck_h])
        dome_head();

    // Hoparlör ızgarası (gövde önünde, referans konumu)
    translate([body_od/2, 0, base_h + body_h * 0.27])
        rotate([0, 90, 0])
            speaker_grille();

    // LED bezeli (gövde önünde, referans konumu)
    translate([body_od/2, 0, base_h + body_h * 0.58])
        rotate([0, 90, 0])
            led_bezel();
}


// ▼▼▼  BURADAN RENDER SEÇ  ▼▼▼

full_assembly();          // Tam montaj — genel görünüm için

// base();                // 1. Taban — STL için
// body();                // 2. Gövde — STL için
// body_lid();            // 3. Arka kapak — STL için
// neck();                // 4. Boyun — STL için
// dome_head();           // 5. Kafa — STL için
// speaker_grille();      // 6. Hoparlör ızgarası — STL için
// led_bezel();           // 7. LED bezeli — STL için
