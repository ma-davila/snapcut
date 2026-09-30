// Snapcut desktop shell: runs the packaged server, shows its page in a
// window, and keeps working from the menu bar (tray on Windows) when the
// window is closed.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, OnceLock};
use std::thread;
use std::time::Duration;

use serde_json::Value;
use tauri::image::Image;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::webview::NewWindowResponse;
use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindowBuilder, WindowEvent, Wry};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt as _};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_opener::OpenerExt;

// Passed by the login item: start in the menu bar, without the window.
const HIDDEN: &str = "--hidden";
const POLL: Duration = Duration::from_secs(15);
const MAX_LOG: u64 = 5_000_000;

#[derive(Default)]
struct Server {
    url: Mutex<Option<String>>,
    // Holding stdin keeps the server alive (--exit-with-stdin).
    child: Mutex<Option<(Child, ChildStdin)>>,
    quitting: AtomicBool,
}

struct Tray {
    auto: CheckMenuItem<Wry>,
    login: CheckMenuItem<Wry>,
}

fn http() -> &'static ureq::Agent {
    static AGENT: OnceLock<ureq::Agent> = OnceLock::new();
    AGENT.get_or_init(|| {
        ureq::Agent::config_builder().timeout_global(Some(Duration::from_secs(10))).build().into()
    })
}

fn get_json(url: &str) -> Option<Value> {
    let body = http().get(url).call().ok()?.body_mut().read_to_string().ok()?;
    serde_json::from_str(&body).ok()
}

fn server_url(app: &AppHandle) -> Option<String> {
    app.state::<Server>().url.lock().unwrap().clone()
}

// Same folder the server's own files use (snapcut/paths.py).
fn log_path(app: &AppHandle) -> PathBuf {
    let dir = if cfg!(target_os = "macos") {
        app.path().home_dir().unwrap_or_default().join("Library/Logs/Snapcut")
    } else {
        app.path().local_data_dir().unwrap_or_default().join("Snapcut").join("logs")
    };
    dir.join("app.log")
}

fn open_log(app: &AppHandle) -> std::io::Result<File> {
    let path = log_path(app);
    fs::create_dir_all(path.parent().unwrap())?;
    if fs::metadata(&path).map(|m| m.len() > MAX_LOG).unwrap_or(false) {
        let _ = fs::rename(&path, path.with_extension("log.1"));
    }
    OpenOptions::new().create(true).append(true).open(path)
}

// ---------- server ----------

fn start_server(app: &AppHandle) -> Result<(), String> {
    let exe = if cfg!(windows) { "snapcut-server.exe" } else { "snapcut-server" };
    let path = app.path().resource_dir().map_err(|e| e.to_string())?.join("server").join(exe);
    let mut log = open_log(app).map_err(|e| e.to_string())?;
    let mut cmd = Command::new(&path);
    cmd.arg("--exit-with-stdin")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(log.try_clone().map_err(|e| e.to_string())?);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let mut child = cmd.spawn().map_err(|e| format!("{}: {e}", path.display()))?;
    let stdout = child.stdout.take().unwrap();
    let stdin = child.stdin.take().unwrap();
    *app.state::<Server>().child.lock().unwrap() = Some((child, stdin));

    let app = app.clone();
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Some(url) = line.strip_prefix("Snapcut: ") {
                server_ready(&app, url.trim().to_string());
            }
            let _ = writeln!(log, "{line}");
        }
        if !app.state::<Server>().quitting.load(Ordering::SeqCst) {
            fail(&app, "El servidor de Snapcut se ha cerrado inesperadamente.".into());
        }
    });
    Ok(())
}

fn server_ready(app: &AppHandle, url: String) {
    *app.state::<Server>().url.lock().unwrap() = Some(url.clone());
    if let (Some(window), Ok(target)) = (app.get_webview_window("main"), url.parse::<Url>()) {
        let _ = window.navigate(target);
    }
    if let Some(config) = get_json(&format!("{url}/api/config")) {
        let _ = app.state::<Tray>().auto.set_checked(config["auto"].as_bool().unwrap_or(true));
    }
    watch_ready(app.clone(), url);
}

fn stop_server(app: &AppHandle) {
    let state = app.state::<Server>();
    state.quitting.store(true, Ordering::SeqCst);
    let Some((mut child, stdin)) = state.child.lock().unwrap().take() else { return };
    drop(stdin);
    for _ in 0..30 {
        if matches!(child.try_wait(), Ok(Some(_))) {
            return;
        }
        thread::sleep(Duration::from_millis(100));
    }
    let _ = child.kill();
}

fn fail(app: &AppHandle, message: String) {
    let text = format!(
        "Snapcut no ha podido arrancar.\n\n{message}\n\nEl registro está en {}",
        log_path(app).display()
    );
    let handle = app.clone();
    app.dialog()
        .message(text)
        .title("Snapcut")
        .kind(MessageDialogKind::Error)
        .show(move |_| handle.exit(1));
}

// ---------- notifications ----------

// A cut finished: say which game, never how it went.
fn watch_ready(app: AppHandle, url: String) {
    thread::spawn(move || {
        let mut seq = None;
        loop {
            if let Some(reply) = get_json(&format!("{url}/api/ready?after={}", seq.unwrap_or(0))) {
                if seq.is_some() {
                    for event in reply["ready"].as_array().into_iter().flatten() {
                        notify(&app, event["teams"].as_str());
                    }
                }
                seq = reply["seq"].as_u64().or(seq);
            }
            thread::sleep(POLL);
        }
    });
}

fn notify(app: &AppHandle, teams: Option<&str>) {
    let watching = app
        .get_webview_window("main")
        .map(|w| w.is_visible().unwrap_or(false) && w.is_focused().unwrap_or(false))
        .unwrap_or(false);
    if watching {
        return;
    }
    let body = match teams {
        Some(t) => format!("{t} ya se puede ver."),
        None => "Tu partido ya se puede ver.".into(),
    };
    let _ = app.notification().builder().title("Partido listo").body(body).show();
}

// ---------- window ----------

fn show_window(app: &AppHandle) {
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        return;
    }
    let target = match server_url(app).and_then(|u| u.parse().ok()) {
        Some(url) => WebviewUrl::External(url),
        None => WebviewUrl::App("index.html".into()),
    };
    let nav = app.clone();
    let popup = app.clone();
    let built = WebviewWindowBuilder::new(app, "main", target)
        .title("Snapcut")
        .inner_size(1280.0, 860.0)
        .min_inner_size(420.0, 520.0)
        // Only the app itself opens in the window; links go to the browser.
        .on_navigation(move |url| {
            let local = url.scheme() == "tauri" || url.host_str() == Some("tauri.localhost");
            let ours = server_url(&nav).is_some_and(|s| url.as_str().starts_with(&s));
            if !local && !ours {
                let _ = nav.opener().open_url(url.as_str(), None::<&str>);
            }
            local || ours
        })
        .on_new_window(move |url, _| {
            let _ = popup.opener().open_url(url.as_str(), None::<&str>);
            NewWindowResponse::Deny
        })
        .build();
    if let Ok(window) = built {
        let _ = window.set_focus();
    }
}

fn hide_window(window: &tauri::Window) {
    // Closing the window shouldn't leave a video playing behind it.
    if let Some(webview) = window.app_handle().get_webview_window(window.label()) {
        let _ = webview.eval("document.querySelectorAll('video').forEach((v) => v.pause())");
    }
    let _ = window.hide();
    #[cfg(target_os = "macos")]
    let _ = window.app_handle().set_activation_policy(tauri::ActivationPolicy::Accessory);
}

// ---------- tray ----------

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Abrir Snapcut", true, None::<&str>)?;
    let auto = CheckMenuItem::with_id(app, "auto", "Descarga automática", true, true, None::<&str>)?;
    let login_on = app.autolaunch().is_enabled().unwrap_or(false);
    let login = CheckMenuItem::with_id(app, "login", "Abrir al iniciar sesión", true, login_on, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Salir", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &open,
            &PredefinedMenuItem::separator(app)?,
            &auto,
            &login,
            &PredefinedMenuItem::separator(app)?,
            &quit,
        ],
    )?;
    // macOS draws a one-colour template icon to match the menu bar; the
    // Windows tray shows the app icon.
    let icon = if cfg!(target_os = "macos") {
        Image::from_bytes(include_bytes!("../icons/tray.png"))?
    } else {
        app.default_window_icon().cloned().expect("app icon")
    };
    TrayIconBuilder::with_id("tray")
        .icon(icon)
        .icon_as_template(cfg!(target_os = "macos"))
        .tooltip("Snapcut")
        .menu(&menu)
        .show_menu_on_left_click(cfg!(target_os = "macos"))
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => show_window(app),
            "auto" => set_auto(app),
            "login" => set_login(app),
            "quit" => {
                stop_server(app);
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                show_window(tray.app_handle());
            }
        })
        .build(app)?;
    app.manage(Tray { auto, login });
    Ok(())
}

// Check items flip themselves when clicked; apply the new state or put it back.
fn set_auto(app: &AppHandle) {
    let item = app.state::<Tray>().auto.clone();
    let on = item.is_checked().unwrap_or(true);
    let Some(url) = server_url(app) else {
        let _ = item.set_checked(!on);
        return;
    };
    thread::spawn(move || {
        let body = serde_json::json!({ "auto": on }).to_string();
        let sent = http()
            .put(&format!("{url}/api/settings"))
            .header("Content-Type", "application/json")
            .send(body.as_str());
        if sent.is_err() {
            let _ = item.set_checked(!on);
        }
    });
}

fn set_login(app: &AppHandle) {
    let item = &app.state::<Tray>().login;
    let on = item.is_checked().unwrap_or(false);
    let launcher = app.autolaunch();
    let done = if on { launcher.enable() } else { launcher.disable() };
    if done.is_err() {
        let _ = item.set_checked(!on);
    }
}

// macOS menu bar, in Spanish: the default one is in English.
fn app_menu(app: &AppHandle) -> tauri::Result<Menu<Wry>> {
    let sep = || PredefinedMenuItem::separator(app);
    Menu::with_items(
        app,
        &[
            &Submenu::with_items(
                app,
                "Snapcut",
                true,
                &[
                    &PredefinedMenuItem::hide(app, Some("Ocultar Snapcut"))?,
                    &PredefinedMenuItem::hide_others(app, Some("Ocultar otros"))?,
                    &sep()?,
                    &PredefinedMenuItem::quit(app, Some("Salir de Snapcut"))?,
                ],
            )?,
            &Submenu::with_items(
                app,
                "Edición",
                true,
                &[
                    &PredefinedMenuItem::undo(app, Some("Deshacer"))?,
                    &PredefinedMenuItem::redo(app, Some("Rehacer"))?,
                    &sep()?,
                    &PredefinedMenuItem::cut(app, Some("Cortar"))?,
                    &PredefinedMenuItem::copy(app, Some("Copiar"))?,
                    &PredefinedMenuItem::paste(app, Some("Pegar"))?,
                    &PredefinedMenuItem::select_all(app, Some("Seleccionar todo"))?,
                ],
            )?,
            &Submenu::with_items(
                app,
                "Ventana",
                true,
                &[
                    &PredefinedMenuItem::minimize(app, Some("Minimizar"))?,
                    &PredefinedMenuItem::fullscreen(app, Some("Pantalla completa"))?,
                    &PredefinedMenuItem::close_window(app, Some("Cerrar ventana"))?,
                ],
            )?,
        ],
    )
}

fn main() {
    let app = tauri::Builder::default()
        .menu(app_menu)
        .plugin(tauri_plugin_single_instance::init(|app, _, _| show_window(app)))
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec![HIDDEN])))
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(Server::default())
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                hide_window(window);
            }
        })
        .setup(|app| {
            let handle = app.handle().clone();
            build_tray(&handle)?;
            if std::env::args().any(|a| a == HIDDEN) {
                #[cfg(target_os = "macos")]
                app.set_activation_policy(tauri::ActivationPolicy::Accessory);
            } else {
                show_window(&handle);
            }
            if let Err(e) = start_server(&handle) {
                fail(&handle, e);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Snapcut");

    app.run(|app, event| match event {
        RunEvent::Exit => stop_server(app),
        #[cfg(target_os = "macos")]
        RunEvent::Reopen { .. } => show_window(app),
        _ => {}
    });
}
