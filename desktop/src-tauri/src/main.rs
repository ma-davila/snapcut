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
use tauri_plugin_updater::UpdaterExt;

// Passed by the login item: start in the menu bar, without the window.
const HIDDEN: &str = "--hidden";
const POLL: Duration = Duration::from_secs(15);
const MAX_LOG: u64 = 5_000_000;
// The server exits with this when it wants to be started again (a newer yt-dlp).
const RESTART: i32 = 75;
const UPDATE_FIRST: Duration = Duration::from_secs(120);
const UPDATE_EVERY: Duration = Duration::from_secs(6 * 3600);
const IDLE_POLL: Duration = Duration::from_secs(60);

#[derive(Default)]
struct Server {
    url: Mutex<Option<String>>,
    // Holding stdin keeps the server alive (--exit-with-stdin).
    child: Mutex<Option<(Child, ChildStdin)>>,
    quitting: AtomicBool,
    watching: AtomicBool,
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
        app.path().local_data_dir().unwrap_or_default().join("Snapcut").join("data").join("logs")
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

fn log_line(app: &AppHandle, message: &str) {
    if let Ok(mut log) = open_log(app) {
        let _ = writeln!(log, "{message}");
    }
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
        // Keep reading whatever comes: a stalled pipe would block the server.
        let mut reader = BufReader::new(stdout);
        let mut buf = Vec::new();
        while reader.read_until(b'\n', &mut buf).unwrap_or(0) > 0 {
            let line = String::from_utf8_lossy(&buf);
            if let Some(url) = line.trim_end().strip_prefix("Snapcut: ") {
                server_ready(&app, url.to_string());
            }
            let _ = log.write_all(&buf);
            buf.clear();
        }
        let state = app.state::<Server>();
        let code = state.child.lock().unwrap().as_mut().and_then(|(c, _)| c.wait().ok()).and_then(|s| s.code());
        if state.quitting.load(Ordering::SeqCst) {
            return;
        }
        if code == Some(RESTART) {
            let _ = writeln!(log, "starting the server again");
            if let Err(e) = start_server(&app) {
                fail(&app, e);
            }
        } else {
            fail(&app, "El servidor de Snapcut se ha cerrado inesperadamente.".into());
        }
    });
    Ok(())
}

fn server_ready(app: &AppHandle, url: String) {
    let state = app.state::<Server>();
    let previous = state.url.lock().unwrap().replace(url.clone());
    // After a restart on the same address the page just carries on.
    if previous.as_deref() != Some(url.as_str()) {
        if let (Some(window), Ok(target)) = (app.get_webview_window("main"), url.parse::<Url>()) {
            let _ = window.navigate(target);
        }
    }
    if let Some(config) = get_json(&format!("{url}/api/config")) {
        let _ = app.state::<Tray>().auto.set_checked(config["auto"].as_bool().unwrap_or(true));
    }
    if !state.watching.swap(true, Ordering::SeqCst) {
        watch_ready(app.clone());
    }
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
fn watch_ready(app: AppHandle) {
    thread::spawn(move || {
        // (server boot, last event seen); a new boot means a restarted server.
        let mut seen: Option<(u64, u64)> = None;
        loop {
            if let Some(url) = server_url(&app) {
                let after = seen.map_or(0, |s| s.1);
                if let Some(mut reply) = get_json(&format!("{url}/api/ready?after={after}")) {
                    let boot = reply["boot"].as_u64().unwrap_or(0);
                    let restarted = seen.is_some_and(|s| s.0 != boot);
                    if restarted {
                        // Everything since it started is new to us.
                        reply = get_json(&format!("{url}/api/ready?after=0")).unwrap_or(reply);
                    }
                    if seen.is_some() {
                        for event in reply["ready"].as_array().into_iter().flatten() {
                            notify(&app, event["teams"].as_str());
                        }
                    }
                    seen = Some((boot, reply["seq"].as_u64().unwrap_or(0)));
                }
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

// ---------- app updates ----------

// Written just before installing an update: the next start is quiet (menu
// bar only) and says which version it is.
fn update_marker(app: &AppHandle) -> PathBuf {
    app.path().app_local_data_dir().unwrap_or_default().join("updated")
}

// An update may only interrupt nothing: no cut under way, window closed.
fn idle(app: &AppHandle) -> bool {
    let window_open = app.get_webview_window("main").is_some_and(|w| w.is_visible().unwrap_or(false));
    let busy = server_url(app)
        .and_then(|url| get_json(&format!("{url}/api/status")))
        .map_or(true, |s| s["busy"].as_bool().unwrap_or(true));
    !window_open && !busy
}

async fn update_app(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let mut builder = app.updater_builder();
    // For testing against a local server; releases use tauri.conf.json's endpoint.
    if let Ok(url) = std::env::var("SNAPCUT_UPDATE_URL") {
        builder = builder.endpoints(vec![url.parse()?])?;
    }
    let Some(update) = builder.build()?.check().await? else { return Ok(()) };
    log_line(app, &format!("downloading Snapcut {}", update.version));
    let bytes = update.download(|_, _| {}, || {}).await?;
    log_line(app, &format!("Snapcut {} downloaded, waiting until idle", update.version));
    while !idle(app) {
        thread::sleep(IDLE_POLL);
    }
    let marker = update_marker(app);
    fs::create_dir_all(marker.parent().unwrap())?;
    fs::write(&marker, &update.version)?;
    // Its files are about to be replaced. On Windows install() hands over to
    // the installer, which starts the new version.
    stop_server(app);
    let installed = update.install(bytes);
    if installed.is_err() {
        let _ = fs::remove_file(&marker);
    }
    app.request_restart();
    Ok(installed?)
}

fn watch_updates(app: AppHandle) {
    thread::spawn(move || {
        thread::sleep(UPDATE_FIRST);
        loop {
            if let Err(e) = tauri::async_runtime::block_on(update_app(&app)) {
                log_line(&app, &format!("update check failed: {e}"));
            }
            thread::sleep(UPDATE_EVERY);
        }
    });
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
        .plugin(tauri_plugin_updater::Builder::new().build())
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
            let updated = fs::read_to_string(update_marker(&handle)).ok();
            let _ = fs::remove_file(update_marker(&handle));
            let version = handle.package_info().version.to_string();
            if updated.as_deref().map(str::trim) == Some(version.as_str()) {
                let _ = handle
                    .notification()
                    .builder()
                    .title("Snapcut se ha actualizado")
                    .body(format!("Ya tienes la versión {version}."))
                    .show();
            }
            if updated.is_some() || std::env::args().any(|a| a == HIDDEN) {
                #[cfg(target_os = "macos")]
                app.set_activation_policy(tauri::ActivationPolicy::Accessory);
            } else {
                show_window(&handle);
            }
            if let Err(e) = start_server(&handle) {
                fail(&handle, e);
            }
            watch_updates(handle);
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
