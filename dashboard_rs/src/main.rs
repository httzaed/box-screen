use eframe::egui::{self, Color32, RichText, Stroke, Ui, Vec2};
use serde::Deserialize;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

const API_BASE: &str = "http://localhost:7420";

// ── Palette ──────────────────────────────────────────────────────────────────
const BG:     Color32 = Color32::from_rgb(6,   4,  16);
const PANEL:  Color32 = Color32::from_rgb(13,  10,  30);
const ACCENT: Color32 = Color32::from_rgb(200,  64, 255);
const GREEN:  Color32 = Color32::from_rgb(0,  255, 157);
const BLUE:   Color32 = Color32::from_rgb(56, 191, 255);
const ORANGE: Color32 = Color32::from_rgb(255, 106,   0);
const MUTED:  Color32 = Color32::from_rgb(107,  94, 138);
const BORDER: Color32 = Color32::from_rgb(42,   31,  80);

const MODES:       [&str; 3] = ["ascii_vhs", "video", "image"];
const HUD_STYLES:  [&str; 6] = ["full", "terminal", "clock", "split", "tiles", "matrix"];
const CASE_MODES:  [&str; 3] = ["off", "wave", "beat"];
const FAN_MODES:   [&str; 4] = ["off", "spin_bpm", "spin_fixed", "static"];
const LED_COLORS:  [&str; 6] = ["violet", "cyan", "orange", "green", "red", "white"];

// Couleur d'aperçu pour chaque nom
fn led_color_preview(name: &str) -> Color32 {
    match name {
        "violet" => Color32::from_rgb(180,   0, 255),
        "cyan"   => Color32::from_rgb(  0, 200, 255),
        "orange" => Color32::from_rgb(255, 106,   0),
        "green"  => Color32::from_rgb(  0, 255, 100),
        "red"    => Color32::from_rgb(255,  20,  60),
        "white"  => Color32::from_rgb(220, 220, 220),
        _        => Color32::GRAY,
    }
}

// ── State partagé ─────────────────────────────────────────────────────────────
#[derive(Debug, Clone, Default, Deserialize)]
struct LedCfg {
    case_mode:  String,
    fan_mode:   String,
    case_color: String,
    fan_color:  String,
}

#[derive(Debug, Clone, Default, Deserialize)]
struct Status {
    mode:     String,
    hud:      String,
    bpm:      Option<f32>,
    level:    f32,
    beat_seq: i64,
    #[serde(default)]
    led:      LedCfg,
}

#[derive(Default)]
struct SharedState {
    status:    Status,
    connected: bool,
}

// ── App ───────────────────────────────────────────────────────────────────────
struct DashApp {
    shared:          Arc<Mutex<SharedState>>,
    last_beat_seq:   i64,
    beat_flash_until: Option<Instant>,
}

impl DashApp {
    fn new(cc: &eframe::CreationContext<'_>, shared: Arc<Mutex<SharedState>>) -> Self {
        // Style global
        let ctx = &cc.egui_ctx;
        let mut style = (*ctx.style()).clone();
        style.visuals.window_fill        = BG;
        style.visuals.panel_fill         = BG;
        style.visuals.faint_bg_color     = PANEL;
        style.visuals.extreme_bg_color   = PANEL;
        style.visuals.window_stroke      = Stroke::new(1.0, BORDER);
        style.visuals.widgets.noninteractive.bg_fill  = PANEL;
        style.visuals.widgets.inactive.bg_fill        = PANEL;
        style.visuals.widgets.hovered.bg_fill         = Color32::from_rgb(35, 25, 65);
        style.visuals.widgets.active.bg_fill          = ACCENT;
        style.visuals.widgets.noninteractive.fg_stroke = Stroke::new(1.0, MUTED);
        style.visuals.widgets.inactive.fg_stroke       = Stroke::new(1.0, MUTED);
        style.visuals.override_text_color = Some(Color32::from_rgb(212, 200, 240));
        style.spacing.item_spacing   = Vec2::new(8.0, 6.0);
        style.spacing.button_padding = Vec2::new(14.0, 6.0);
        ctx.set_style(style);

        Self {
            shared,
            last_beat_seq:    -1,
            beat_flash_until: None,
        }
    }

    fn post_async(path: String) {
        thread::spawn(move || {
            let _ = reqwest::blocking::Client::new()
                .post(format!("{}{}", API_BASE, path))
                .timeout(Duration::from_millis(400))
                .send();
        });
    }

    fn section_label(ui: &mut Ui, text: &str) {
        ui.label(RichText::new(text).color(MUTED).size(10.0));
        ui.add_space(4.0);
    }

    fn mode_button(ui: &mut Ui, label: &str, active: bool, mode: &str) {
        let (bg, fg, stroke_col) = if active {
            (Color32::from_rgb(124, 0, 224), Color32::WHITE, ACCENT)
        } else {
            (PANEL, MUTED, BORDER)
        };
        let btn = egui::Button::new(RichText::new(label).color(fg).size(11.0))
            .fill(bg)
            .stroke(Stroke::new(1.0, stroke_col))
            .min_size(Vec2::new(120.0, 28.0));
        if ui.add(btn).clicked() {
            Self::post_async(format!("/api/mode/{}", mode));
        }
    }

    fn led_mode_button(ui: &mut Ui, label: &str, active: bool, path: &str) {
        let (bg, fg, sc) = if active {
            (Color32::from_rgb(0, 70, 40), Color32::WHITE, GREEN)
        } else {
            (PANEL, MUTED, BORDER)
        };
        let btn = egui::Button::new(RichText::new(label).color(fg).size(10.0))
            .fill(bg)
            .stroke(Stroke::new(1.0, sc))
            .min_size(Vec2::new(58.0, 26.0));
        if ui.add(btn).clicked() {
            Self::post_async(path.to_string());
        }
    }

    fn color_button(ui: &mut Ui, color_name: &str, active: bool, path: &str) {
        let preview = led_color_preview(color_name);
        let border  = if active { preview } else { BORDER };
        let bg      = if active {
            Color32::from_rgba_premultiplied(
                (preview.r() as f32 * 0.25) as u8,
                (preview.g() as f32 * 0.25) as u8,
                (preview.b() as f32 * 0.25) as u8,
                255,
            )
        } else { PANEL };
        let dot = RichText::new("  ").background_color(preview);
        let btn = egui::Button::new(dot)
            .fill(bg)
            .stroke(Stroke::new(if active { 2.0 } else { 1.0 }, border))
            .min_size(Vec2::new(28.0, 26.0));
        if ui.add(btn).clicked() {
            Self::post_async(path.to_string());
        }
    }

    fn hud_button(ui: &mut Ui, label: &str, active: bool, hud: &str, accent: Color32) {
        let (bg, fg, stroke_col) = if active {
            (Color32::from_rgba_premultiplied(
                (accent.r() as f32 * 0.25) as u8,
                (accent.g() as f32 * 0.25) as u8,
                (accent.b() as f32 * 0.25) as u8,
                255,
            ), Color32::WHITE, accent)
        } else {
            (PANEL, MUTED, BORDER)
        };
        let btn = egui::Button::new(RichText::new(label).color(fg).size(11.0))
            .fill(bg)
            .stroke(Stroke::new(1.0, stroke_col))
            .min_size(Vec2::new(60.0, 28.0));
        if ui.add(btn).clicked() {
            Self::post_async(format!("/api/hud/{}", hud));
        }
    }

    fn progress_bar(ui: &mut Ui, value: f32, color: Color32) {
        let (rect, _) = ui.allocate_exact_size(Vec2::new(ui.available_width(), 7.0),
                                                egui::Sense::hover());
        let painter = ui.painter();
        painter.rect_filled(rect, 3.0, Color32::from_rgb(20, 15, 40));
        if value > 0.0 {
            let mut fill = rect;
            fill.set_right(rect.left() + rect.width() * value.clamp(0.0, 1.0));
            painter.rect_filled(fill, 3.0, color);
            // glow
            painter.rect_stroke(fill, 3.0, Stroke::new(1.0,
                Color32::from_rgba_premultiplied(color.r(), color.g(), color.b(), 80)));
        }
    }
}

impl eframe::App for DashApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // Repaint en continu pour beat flash + refresh
        ctx.request_repaint_after(Duration::from_millis(80));

        let (status, connected) = {
            let s = self.shared.lock().unwrap();
            (s.status.clone(), s.connected)
        };

        // Détection beat
        if status.beat_seq != self.last_beat_seq && self.last_beat_seq >= 0 {
            self.beat_flash_until = Some(Instant::now() + Duration::from_millis(120));
        }
        self.last_beat_seq = status.beat_seq;
        let beat_on = self.beat_flash_until
            .map(|t| Instant::now() < t)
            .unwrap_or(false);

        egui::CentralPanel::default()
            .frame(egui::Frame::none().fill(BG).inner_margin(16.0))
            .show(ctx, |ui| {
                // Titre
                ui.label(RichText::new("BOX SCREEN").color(ACCENT).size(14.0).strong());
                ui.label(RichText::new("Trofeo Vision 9.16 — 1920x462").color(MUTED).size(10.0));
                ui.add_space(8.0);
                ui.separator();
                ui.add_space(10.0);

                // ── Mode ─────────────────────────────────────────────────
                egui::Frame::none()
                    .fill(PANEL)
                    .stroke(Stroke::new(1.0, BORDER))
                    .rounding(6.0)
                    .inner_margin(12.0)
                    .show(ui, |ui| {
                        Self::section_label(ui, "MODE");
                        ui.horizontal(|ui| {
                            for m in MODES {
                                let label = m.replace('_', " ").to_uppercase();
                                Self::mode_button(ui, &label, status.mode == m, m);
                            }
                        });
                    });

                ui.add_space(10.0);

                // ── HUD ──────────────────────────────────────────────────
                egui::Frame::none()
                    .fill(PANEL)
                    .stroke(Stroke::new(1.0, BORDER))
                    .rounding(6.0)
                    .inner_margin(12.0)
                    .show(ui, |ui| {
                        Self::section_label(ui, "HUD STYLE");
                        let led_accent = led_color_preview(&status.led.case_color);
                        ui.horizontal(|ui| {
                            for h in HUD_STYLES {
                                Self::hud_button(ui, &h.to_uppercase(), status.hud == h, h, led_accent);
                            }
                        });
                    });

                ui.add_space(10.0);

                // ── Audio ────────────────────────────────────────────────
                egui::Frame::none()
                    .fill(PANEL)
                    .stroke(Stroke::new(1.0, BORDER))
                    .rounding(6.0)
                    .inner_margin(12.0)
                    .show(ui, |ui| {
                        ui.horizontal(|ui| {
                            ui.label(RichText::new("AUDIO").color(MUTED).size(10.0));
                            ui.add_space(6.0);
                            let beat_color = if beat_on {
                                ORANGE
                            } else {
                                Color32::from_rgba_premultiplied(255, 106, 0, 40)
                            };
                            ui.label(RichText::new("BEAT").color(beat_color).size(10.0).strong());
                        });
                        ui.add_space(8.0);

                        // BPM
                        let bpm_text = status.bpm
                            .map(|b| format!("{} BPM", b.round() as i32))
                            .unwrap_or_else(|| "-- BPM".into());
                        ui.label(RichText::new(&bpm_text).color(GREEN).size(22.0).strong());
                        let bpm_pct = status.bpm
                            .map(|b| ((b - 60.0) / 100.0).clamp(0.0, 1.0))
                            .unwrap_or(0.0);
                        Self::progress_bar(ui, bpm_pct, GREEN);

                        ui.add_space(10.0);

                        // Level
                        ui.label(RichText::new("LEVEL  (BASS)").color(MUTED).size(10.0));
                        ui.label(RichText::new(format!("{}%", (status.level * 100.0) as i32))
                            .color(BLUE).size(22.0).strong());
                        Self::progress_bar(ui, status.level, BLUE);
                    });

                ui.add_space(10.0);

                // ── LEDs ─────────────────────────────────────────────────
                egui::Frame::none()
                    .fill(PANEL)
                    .stroke(Stroke::new(1.0, BORDER))
                    .rounding(6.0)
                    .inner_margin(12.0)
                    .show(ui, |ui| {
                        // ── Case ─────────────────────────────────────────
                        ui.horizontal(|ui| {
                            ui.label(RichText::new("CASE").color(MUTED).size(10.0));
                            ui.add_space(6.0);
                            for m in CASE_MODES {
                                let label = m.to_uppercase();
                                let active = status.led.case_mode == m;
                                Self::led_mode_button(ui, &label, active,
                                    &format!("/api/led/case/{}", m));
                            }
                            ui.add_space(10.0);
                            ui.label(RichText::new("COLOR").color(MUTED).size(10.0));
                            ui.add_space(4.0);
                            for c in LED_COLORS {
                                let active = status.led.case_color == c;
                                Self::color_button(ui, c, active,
                                    &format!("/api/led/case_color/{}", c));
                            }
                        });

                        ui.add_space(6.0);

                        // ── Fans ─────────────────────────────────────────
                        ui.horizontal(|ui| {
                            ui.label(RichText::new("FANS").color(MUTED).size(10.0));
                            ui.add_space(6.0);
                            for m in FAN_MODES {
                                let label = match m {
                                    "spin_bpm"   => "BPM",
                                    "spin_fixed" => "FIXED",
                                    other        => &other.to_uppercase(),
                                };
                                let active = status.led.fan_mode == m;
                                Self::led_mode_button(ui, label, active,
                                    &format!("/api/led/fans/{}", m));
                            }
                            ui.add_space(10.0);
                            ui.label(RichText::new("COLOR").color(MUTED).size(10.0));
                            ui.add_space(4.0);
                            for c in LED_COLORS {
                                let active = status.led.fan_color == c;
                                Self::color_button(ui, c, active,
                                    &format!("/api/led/fans_color/{}", c));
                            }
                        });
                    });

                ui.add_space(10.0);

                // ── Status bar ───────────────────────────────────────────
                ui.horizontal(|ui| {
                    let (dot, col) = if connected {
                        ("* online", GREEN)
                    } else {
                        ("* offline", Color32::from_rgb(255, 68, 68))
                    };
                    ui.label(RichText::new(dot).color(col).size(10.0));
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        ui.label(RichText::new(
                            format!("{}  /  {}", status.mode, status.hud)
                        ).color(MUTED).size(10.0));
                    });
                });
            });
    }
}

// ── Thread polling HTTP ───────────────────────────────────────────────────────
fn spawn_poller(shared: Arc<Mutex<SharedState>>, ctx: egui::Context) {
    thread::spawn(move || {
        let client = reqwest::blocking::Client::builder()
            .timeout(Duration::from_millis(400))
            .build()
            .unwrap();
        loop {
            let result = client
                .get(format!("{}/api/status", API_BASE))
                .send()
                .and_then(|r| r.json::<Status>());
            {
                let mut s = shared.lock().unwrap();
                match result {
                    Ok(status) => { s.status = status; s.connected = true; }
                    Err(_)     => { s.connected = false; }
                }
            }
            ctx.request_repaint();
            thread::sleep(Duration::from_millis(250));
        }
    });
}

// ── main ─────────────────────────────────────────────────────────────────────
fn main() -> eframe::Result<()> {
    let shared = Arc::new(Mutex::new(SharedState::default()));

    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_title("Box Screen")
            .with_inner_size([620.0, 580.0])
            .with_resizable(false)
            .with_decorations(true),
        ..Default::default()
    };

    let shared_clone = Arc::clone(&shared);

    eframe::run_native(
        "Box Screen",
        options,
        Box::new(move |cc| {
            spawn_poller(Arc::clone(&shared_clone), cc.egui_ctx.clone());
            Box::new(DashApp::new(cc, shared_clone)) as Box<dyn eframe::App>
        }),
    )
}
