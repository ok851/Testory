use base64::{engine::general_purpose::STANDARD, Engine as _};
use serde::Serialize;

const MAX_FILE_BYTES: u64 = 50 * 1024 * 1024;

#[derive(Serialize)]
pub struct NativeFilePayload {
    pub path: String,
    pub name: String,
    pub base64: String,
}

#[tauri::command]
pub fn pick_native_files(
    title: Option<String>,
    filters: Option<Vec<(String, Vec<String>)>>,
    multiple: Option<bool>,
) -> Option<Vec<String>> {
    let mut dialog = rfd::FileDialog::new();
    if let Some(t) = title.filter(|s| !s.trim().is_empty()) {
        dialog = dialog.set_title(t);
    }
    if let Some(list) = filters {
        for (label, extensions) in list {
            let ext_refs: Vec<&str> = extensions.iter().map(String::as_str).collect();
            if !ext_refs.is_empty() {
                dialog = dialog.add_filter(&label, &ext_refs);
            }
        }
    }

    let picked = if multiple.unwrap_or(false) {
        dialog.pick_files()
    } else {
        dialog.pick_file().map(|p| vec![p])
    }?;

    Some(
        picked
            .iter()
            .map(|p| p.to_string_lossy().into_owned())
            .collect(),
    )
}

#[tauri::command]
pub fn read_native_file_base64(path: String) -> Result<NativeFilePayload, String> {
    let path = path.trim();
    if path.is_empty() {
        return Err("文件路径为空".into());
    }
    let meta = std::fs::metadata(path).map_err(|e| format!("无法读取文件: {e}"))?;
    if !meta.is_file() {
        return Err("不是有效文件".into());
    }
    if meta.len() > MAX_FILE_BYTES {
        return Err("文件过大（上限 50MB）".into());
    }
    let bytes = std::fs::read(path).map_err(|e| format!("读取文件失败: {e}"))?;
    let name = std::path::Path::new(path)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("file")
        .to_string();
    Ok(NativeFilePayload {
        path: path.to_string(),
        name,
        base64: STANDARD.encode(bytes),
    })
}
