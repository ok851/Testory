mod flask_process;
mod flask_proxy;
mod native_dialog;

use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

use reqwest::Client;
use tauri::{
    AppHandle, Manager, RunEvent, Url, WindowEvent,
    window::{Effect, EffectsBuilder},
};

use flask_process::{kill_flask_child, spawn_flask, wait_for_flask_ready, FlaskChild};
use flask_proxy::{build_http_client, flask_fetch};
use native_dialog::{pick_native_files, read_native_file_base64};

const HEALTH_TIMEOUT: Duration = Duration::from_secs(30);
const HEALTH_POLL: Duration = Duration::from_millis(500);

pub struct AppState {
    pub flask: Mutex<Option<FlaskChild>>,
    pub flask_port: Mutex<Option<u16>>,
    pub http: Client,
}

pub fn run() {
    tauri::Builder::default()
        .manage(AppState {
            flask: Mutex::new(None),
            flask_port: Mutex::new(None),
            http: build_http_client(),
        })
        .invoke_handler(tauri::generate_handler![
            flask_fetch,
            pick_native_files,
            read_native_file_base64,
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                if let Err(err) = bootstrap_flask(&handle) {
                    eprintln!("Testory bootstrap failed: {err}");
                    if let Some(window) = handle.get_webview_window("main") {
                        if let Ok(url) = local_page_url("tauri_error.html") {
                            let _ = window.navigate(url);
                        }
                    }
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build Testory desktop app")
        .run(|app, event| {
            if let RunEvent::WindowEvent {
                label,
                event: WindowEvent::CloseRequested { api, .. },
                ..
            } = event
            {
                if label == "main" {
                    api.prevent_close();
                    if let Some(state) = app.try_state::<AppState>() {
                        if let Ok(mut guard) = state.flask.lock() {
                            kill_flask_child(guard.as_mut());
                            *guard = None;
                        }
                    }
                    app.exit(0);
                }
            }
        });
}

fn project_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..")
}

fn bootstrap_flask(app: &AppHandle) -> Result<(), String> {
    let root = project_root();
    let user_data = flask_process::resolve_user_data_dir(&root);
    let port_file = user_data.join(".flask_port");
    let _ = std::fs::remove_file(&port_file);

    let child = spawn_flask(&root, &user_data, &port_file)?;
    let log_file = child.log_file.clone();
    {
        let state = app.state::<AppState>();
        let mut guard = state.flask.lock().map_err(|e| e.to_string())?;
        *guard = Some(child);
    }

    let port = wait_for_flask_ready(
        &port_file,
        &log_file,
        app,
        HEALTH_TIMEOUT,
        HEALTH_POLL,
    )?;
    {
        let state = app.state::<AppState>();
        let mut guard = state.flask_port.lock().map_err(|e| e.to_string())?;
        *guard = Some(port);
    }

    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "main window missing".to_string())?;

    let app_url = Url::parse(&format!("http://127.0.0.1:{port}/"))
        .map_err(|e| format!("invalid app url: {e}"))?;
    window.navigate(app_url).map_err(|e| e.to_string())?;

    #[cfg(windows)]
    {
        let _ = window.set_effects(EffectsBuilder::new().effect(Effect::Mica).build());
    }
    #[cfg(target_os = "macos")]
    {
        let _ = window.set_effects(
            EffectsBuilder::new()
                .effect(Effect::HudWindow)
                .build(),
        );
    }

    Ok(())
}

fn local_page_url(path: &str) -> Result<Url, String> {
    Url::parse(&format!("https://tauri.localhost/{path}"))
        .map_err(|e| format!("invalid local page url: {e}"))
}
