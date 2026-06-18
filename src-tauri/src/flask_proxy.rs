use reqwest::Client;
use serde::Serialize;
use tauri::State;

use crate::AppState;

#[derive(Serialize)]
pub struct FlaskFetchResult {
    pub status: u16,
    pub body: String,
}

#[tauri::command]
pub async fn flask_fetch(
    state: State<'_, AppState>,
    path: String,
    method: Option<String>,
    body: Option<String>,
    cookie: Option<String>,
) -> Result<FlaskFetchResult, String> {
    let port = state
        .flask_port
        .lock()
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "Flask 未就绪".to_string())?;

    let method = method.unwrap_or_else(|| "GET".to_string()).to_uppercase();
    let path = if path.starts_with('/') {
        path
    } else {
        format!("/{path}")
    };
    let url = format!("http://127.0.0.1:{port}{path}");

    let mut req = state
        .http
        .request(
            reqwest::Method::from_bytes(method.as_bytes())
                .map_err(|_| format!("无效 HTTP 方法: {method}"))?,
            &url,
        )
        .header("Accept", "application/json, text/plain, */*");

    if let Some(raw_cookie) = cookie {
        let trimmed = raw_cookie.trim();
        if !trimmed.is_empty() {
            req = req.header("Cookie", trimmed);
        }
    }
    if let Some(payload) = body {
        req = req
            .header("Content-Type", "application/json; charset=utf-8")
            .body(payload);
    }

    let resp = req.send().await.map_err(|e| format!("Flask 请求失败: {e}"))?;
    let status = resp.status().as_u16();
    let body = resp
        .text()
        .await
        .map_err(|e| format!("读取 Flask 响应失败: {e}"))?;
    Ok(FlaskFetchResult { status, body })
}

pub fn build_http_client() -> Client {
    Client::builder()
        .timeout(std::time::Duration::from_secs(15))
        .build()
        .unwrap_or_else(|_| Client::new())
}
