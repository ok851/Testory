use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use reqwest::blocking::Client;
use tauri::{AppHandle, Manager};

pub struct FlaskChild {
    pub child: Child,
    pub log_file: PathBuf,
}

const CREATE_NO_WINDOW: u32 = 0x0800_0000;

pub fn resolve_user_data_dir(_app_root: &Path) -> PathBuf {
    if let Ok(raw) = std::env::var("UAT_DATA_DIR") {
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }
    if let Ok(local) = std::env::var("LOCALAPPDATA") {
        let trimmed = local.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed).join("Testory");
        }
    }
    dirs_fallback_home().join("AppData").join("Local").join("Testory")
}

fn dirs_fallback_home() -> PathBuf {
    std::env::var("USERPROFILE")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
}

pub fn resolve_python(app_root: &Path) -> Option<PathBuf> {
    for rel in [".venv/Scripts/python.exe", ".venv/Scripts/pythonw.exe"] {
        let cand = app_root.join(rel);
        if cand.is_file() {
            return Some(cand);
        }
    }
    None
}

fn backend_command(app_root: &Path, python: &Path) -> Command {
    let protected = app_root.join("runtime/testory_app/TestoryBackend.exe");
    if protected.is_file() {
        let mut cmd = Command::new(protected);
        cmd.current_dir(app_root);
        return cmd;
    }
    let mut cmd = Command::new(python);
    cmd.arg(app_root.join("app.py")).current_dir(app_root);
    cmd
}

pub fn spawn_flask(app_root: &Path, user_data: &Path, port_file: &Path) -> Result<FlaskChild, String> {
    fs::create_dir_all(user_data.join("logs")).map_err(|e| e.to_string())?;
    let log_file = user_data.join("logs/backend_startup.log");
    let log_stdout = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_file)
        .map_err(|e| e.to_string())?;
    let log_stderr = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_file)
        .map_err(|e| e.to_string())?;
    {
        let mut marker = log_stdout.try_clone().map_err(|e| e.to_string())?;
        writeln!(marker, "\n--- backend start (tauri) ---").map_err(|e| e.to_string())?;
    }

    let python = resolve_python(app_root).ok_or_else(|| {
        "未找到 .venv\\Scripts\\python.exe，请在项目根目录创建虚拟环境。".to_string()
    })?;

    let root_str = app_root
        .canonicalize()
        .unwrap_or_else(|_| app_root.to_path_buf())
        .to_string_lossy()
        .into_owned();

    let mut cmd = backend_command(app_root, &python);
    cmd.env_remove("PYTHONHOME")
        .env("PYTHONNOUSERSITE", "1")
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONPATH", &root_str)
        .env("TESTORY_INSTALL_ROOT", &root_str)
        .env("DEPLOYMENT_PROFILE", "local")
        .env("DESKTOP_EXECUTION_MODE", "inprocess")
        .env("PLAYWRIGHT_HEADLESS", "0")
        .env("DESKTOP_AUTO_START_GATEWAY", "0")
        // 修复：Tauri 模式下 Flask 改为监听 0.0.0.0，端口固定为 5000，
        // 这样手机端可通过 PC 局域网 IP 访问 PC 端进行移动端同步。
        // 不再使用 127.0.0.1 + 随机端口（之前会导致手机无法连接）。
        .env("FLASK_RUN_HOST", "0.0.0.0")
        .env("FLASK_RUN_PORT", "5000")
        .env("UAT_DESKTOP_MODE", "1")
        .env("DEPLOYMENT_MODE", "client")
        .env("DESKTOP_LAZY_GATEWAY_BOOT", "1")
        .env("TESTORY_FRAMELESS_SHELL", "1")
        .env("ENABLE_MOBILE", "1")
        .env("MOBILE_EMULATOR_MODE", "1")
        .env("MOBILE_AUTO_CONNECT", "1")
        .env("SKIP_ENV_EXAMPLE_SYNC", "1")
        .env("TESTORY_TAURI_MODE", "1")
        .env("TESTORY_FLASK_PORT_FILE", port_file.to_string_lossy().as_ref())
        .env("UAT_DATA_DIR", user_data.to_string_lossy().as_ref())
        .env("DATABASE_PATH", user_data.join("test_cases.db").to_string_lossy().as_ref())
        .env(
            "EMBEDDED_BROWSER_GATEWAY_URL",
            "http://127.0.0.1:8765",
        )
        .env("EMBEDDED_BROWSER_GATEWAY_SECRET", "hufirst-desktop-local")
        .env("MOBILE_AGENT_GATEWAY_SECRET", "hufirst-desktop-local")
        .env("EMBEDDED_BROWSER_PUBLIC_WS_BASE", "ws://127.0.0.1:8765")
        .env("EMBEDDED_BROWSER_AUTO_START_GATEWAY", "1")
        .env("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434")
        .env(
            "TESTORY_ENV_FILE",
            user_data.join(".env").to_string_lossy().as_ref(),
        )
        .stdout(Stdio::from(log_stdout))
        .stderr(Stdio::from(log_stderr));

    let browsers = app_root.join("playwright-browsers");
    if browsers.is_dir() {
        cmd.env(
            "PLAYWRIGHT_BROWSERS_PATH",
            browsers.to_string_lossy().as_ref(),
        )
        .env("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "1");
    }

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let child = cmd.spawn().map_err(|e| format!("启动 Flask 失败: {e}"))?;
    Ok(FlaskChild { child, log_file })
}

fn read_port_file(port_file: &Path) -> Option<u16> {
    let text = fs::read_to_string(port_file).ok()?;
    text.trim().parse().ok()
}

fn flask_still_running(app: &AppHandle) -> bool {
    let Some(state) = app.try_state::<crate::AppState>() else {
        return false;
    };
    let Ok(mut guard) = state.flask.lock() else {
        return false;
    };
    let Some(flask) = guard.as_mut() else {
        return false;
    };
    matches!(flask.child.try_wait(), Ok(None))
}

pub fn wait_for_flask_ready(
    port_file: &Path,
    log_file: &Path,
    app: &AppHandle,
    timeout: Duration,
    poll: Duration,
) -> Result<u16, String> {
    let client = Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .map_err(|e| e.to_string())?;
    let deadline = Instant::now() + timeout;

    while Instant::now() < deadline {
        if !flask_still_running(app) {
            return Err(flask_exit_message(log_file));
        }
        if let Some(port) = read_port_file(port_file) {
            let url = format!("http://127.0.0.1:{port}/api/health");
            if let Ok(resp) = client.get(&url).send() {
                if resp.status().is_success() {
                    return Ok(port);
                }
            }
        }
        thread::sleep(poll);
    }
    Err(format!(
        "等待 Flask 健康检查超时（{}s）。{}",
        timeout.as_secs(),
        tail_log_hint(log_file, 8)
    ))
}

fn flask_exit_message(log_file: &Path) -> String {
    let tail = tail_log_hint(log_file, 12);
    if tail.is_empty() {
        format!(
            "Flask 进程已退出，请查看 {}",
            log_file.display()
        )
    } else {
        format!("Flask 进程已退出。最近日志:\n{tail}")
    }
}

pub fn kill_flask_child(flask: Option<&mut FlaskChild>) {
    let Some(flask) = flask else {
        return;
    };
    let pid = flask.child.id();
    let _ = flask.child.kill();
    let _ = flask.child.wait();
    #[cfg(windows)]
    {
        if pid > 0 {
            let mut cmd = Command::new("taskkill");
            cmd.args(["/F", "/T", "/PID", &pid.to_string()]);
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(CREATE_NO_WINDOW);
            let _ = cmd.status();
        }
    }
}

fn tail_log_hint(log_file: &Path, max_lines: usize) -> String {
    let file = match fs::File::open(log_file) {
        Ok(f) => f,
        Err(_) => return String::new(),
    };
    let reader = BufReader::new(file);
    let lines: Vec<String> = reader
        .lines()
        .map_while(Result::ok)
        .collect();
    if lines.is_empty() {
        return String::new();
    }
    let start = lines.len().saturating_sub(max_lines);
    lines[start..].join("\n")
}
