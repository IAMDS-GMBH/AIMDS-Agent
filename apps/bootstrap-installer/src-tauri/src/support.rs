//! Support ticket & diagnostics upload for the bootstrap installer.
//!
//! Submits logs, system details, and issue descriptions directly to the
//! AIMDS support backend (https://suite-support.iamds.com/api/v1/upload).

use std::collections::HashMap;
use std::fs;
use std::io::{Cursor, Write};
use base64::prelude::*;
use zip::write::SimpleFileOptions;
use zip::ZipWriter;

const DEFAULT_UPLOAD_URL: &str = "https://suite-support.iamds.com/api/v1/upload";

#[derive(Debug, Clone, serde::Deserialize)]
pub struct SupportTicketPayload {
    pub category: String,
    pub severity: String,
    pub summary: String,
    pub user_description: Option<String>,
    #[serde(default = "default_true")]
    pub include_logs: bool,
    pub install_type: Option<String>,
    pub context_type: Option<String>,
    pub error_message: Option<String>,
    pub attachments: Option<Vec<String>>,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct SupportTicketResult {
    pub ok: bool,
    pub reference_id: Option<String>,
    pub support_case_id: Option<String>,
    pub error: Option<String>,
}

fn redact_sensitive(text: &str) -> String {
    // Basic redacting of potential keys, passwords, and authorization tokens
    let mut out = String::with_capacity(text.len());
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("IAMDS_LITELLM_API_KEY=")
            || trimmed.starts_with("OPENAI_API_KEY=")
            || trimmed.starts_with("ANTHROPIC_API_KEY=")
            || trimmed.starts_with("SUPPORT_API_KEY=")
        {
            if let Some((k, _)) = line.split_once('=') {
                out.push_str(k);
                out.push_str("=******\n");
                continue;
            }
        }
        if line.contains("Bearer ") {
            let parts: Vec<&str> = line.split("Bearer ").collect();
            out.push_str(parts[0]);
            out.push_str("Bearer ******\n");
            continue;
        }
        out.push_str(line);
        out.push('\n');
    }
    out
}

fn collect_installer_logs() -> HashMap<String, String> {
    let mut files = HashMap::new();
    let log_dir = crate::paths::log_dir();

    let candidate_names = [
        "bootstrap-installer.log",
        "update.log",
        "desktop.log",
        "errors.log",
        "gateway.log",
    ];

    for name in &candidate_names {
        let p = log_dir.join(name);
        if p.is_file() {
            if let Ok(content) = fs::read_to_string(&p) {
                if !content.trim().is_empty() {
                    // Keep at most last ~2000 lines if huge
                    let lines: Vec<&str> = content.lines().collect();
                    let truncated = if lines.len() > 2000 {
                        lines[lines.len() - 2000..].join("\n")
                    } else {
                        content
                    };
                    files.insert(format!("logs/{name}"), redact_sensitive(&truncated));
                }
            }
        }
    }

    // Also check Downloads directory for bootstrap-installer.log
    if let Some(download_dir) = dirs::download_dir() {
        let p = download_dir.join("bootstrap-installer.log");
        if p.is_file() && !files.contains_key("logs/bootstrap-installer.log") {
            if let Ok(content) = fs::read_to_string(&p) {
                if !content.trim().is_empty() {
                    let lines: Vec<&str> = content.lines().collect();
                    let truncated = if lines.len() > 2000 {
                        lines[lines.len() - 2000..].join("\n")
                    } else {
                        content
                    };
                    files.insert("logs/bootstrap-installer.log".to_string(), redact_sensitive(&truncated));
                }
            }
        }
    }

    files
}

#[tauri::command]
pub async fn submit_support_ticket(
    payload: SupportTicketPayload,
) -> Result<SupportTicketResult, String> {
    let now = time::OffsetDateTime::now_utc();
    let support_case_id = format!(
        "SUP-{:04}{:02}{:02}-{:02}{:02}{:02}",
        now.year(),
        u8::from(now.month()),
        now.day(),
        now.hour(),
        now.minute(),
        now.second()
    );

    let customer_id = std::env::var("IAMDS_CUSTOMER_ID").unwrap_or_else(|_| "cust-iamds".to_string());
    let customer_name = std::env::var("IAMDS_CUSTOMER_NAME").unwrap_or_else(|_| "IAMDS GmbH".to_string());
    let upload_url = std::env::var("SUPPORT_UPLOAD_URL").unwrap_or_else(|_| DEFAULT_UPLOAD_URL.to_string());

    let existing = crate::models::get_existing_config(None).await.unwrap_or(crate::models::ExistingConfig {
        base_url: None,
        api_key: None,
        model: None,
    });
    let litellm_url = existing.base_url.unwrap_or_else(|| "https://suite.iamds.com/litellm/v1".to_string());
    let model_used = existing.model.unwrap_or_else(|| "AIMDS-Suite-Auto".to_string());

    let files = if payload.include_logs {
        collect_installer_logs()
    } else {
        HashMap::new()
    };

    let os_info = format!(
        "{} {} ({})",
        std::env::consts::OS,
        std::env::consts::FAMILY,
        std::env::consts::ARCH
    );

    let user_id = std::env::var("USERNAME")
        .or_else(|_| std::env::var("USER"))
        .unwrap_or_else(|_| "installer-user".to_string());

    let mut binary_attachments: Vec<(String, Vec<u8>)> = Vec::new();
    if let Some(ref atts) = payload.attachments {
        for (idx, att) in atts.iter().enumerate() {
            let att_str = att.trim();
            if att_str.starts_with("data:") && att_str.contains(";base64,") {
                if let Some((header, b64_data)) = att_str.split_once(";base64,") {
                    let ext = if header.contains("jpeg") || header.contains("jpg") {
                        ".jpg"
                    } else if header.contains("webp") {
                        ".webp"
                    } else {
                        ".png"
                    };
                    if let Ok(bytes) = BASE64_STANDARD.decode(b64_data) {
                        binary_attachments.push((format!("attachments/screenshot_{}{}", idx + 1, ext), bytes));
                    }
                }
            } else if let Ok(bytes) = BASE64_STANDARD.decode(att_str) {
                binary_attachments.push((format!("attachments/screenshot_{}.png", idx + 1), bytes));
            }
        }
    }

    let mut files_manifest = Vec::new();
    for (rel_path, content) in &files {
        files_manifest.push(serde_json::json!({
            "path": rel_path,
            "mime_type": "text/plain",
            "size_bytes": content.len(),
            "content_category": "log"
        }));
    }
    for (rel_path, bytes) in &binary_attachments {
        let mime = if rel_path.ends_with(".jpg") {
            "image/jpeg"
        } else if rel_path.ends_with(".webp") {
            "image/webp"
        } else {
            "image/png"
        };
        files_manifest.push(serde_json::json!({
            "path": rel_path,
            "mime_type": mime,
            "size_bytes": bytes.len(),
            "content_category": "screenshot"
        }));
    }

    let metadata = serde_json::json!({
        "schema_version": "1.1.0",
        "support_case_id": support_case_id,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "litellm_url": litellm_url,
        "model_used": model_used,
        "environment": std::env::var("HERMES_ENV").unwrap_or_else(|_| "production".to_string()),
        "timestamp": now.format(&time::format_description::well_known::Rfc3339).unwrap_or_default(),
        "client_info": {
            "client_type": "hermes-bootstrap-installer",
            "client_version": env!("CARGO_PKG_VERSION"),
            "os": os_info,
            "user_id": user_id,
        },
        "issue_details": {
            "category": payload.category,
            "severity": payload.severity,
            "summary": payload.summary,
            "user_description": payload.user_description.clone().unwrap_or_default(),
            "error_message": payload.error_message.clone().unwrap_or_default(),
        },
        "context_type": payload.context_type.clone().unwrap_or_else(|| "install_error".to_string()),
        "install_type": payload.install_type.clone(),
        "lifecycle": {
            "retention_days": 14,
            "max_size_kb": 25600
        },
        "files": files_manifest
    });

    let manifest = serde_json::json!({
        "schema": 1,
        "created_at": now.format(&time::format_description::well_known::Rfc3339).unwrap_or_default(),
        "client": "hermes-bootstrap-installer",
        "support_case_id": support_case_id,
        "metadata": metadata
    });

    let metadata_str = serde_json::to_string_pretty(&metadata).map_err(|e| e.to_string())?;
    let manifest_str = serde_json::to_string_pretty(&manifest).map_err(|e| e.to_string())?;

    // Create zip archive in memory
    let mut buffer = Cursor::new(Vec::new());
    {
        let mut zip = ZipWriter::new(&mut buffer);
        let options = SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);

        zip.start_file("metadata.json", options)
            .map_err(|e| format!("Zip error: {e}"))?;
        zip.write_all(metadata_str.as_bytes())
            .map_err(|e| format!("Zip write error: {e}"))?;

        zip.start_file("manifest.json", options)
            .map_err(|e| format!("Zip error: {e}"))?;
        zip.write_all(manifest_str.as_bytes())
            .map_err(|e| format!("Zip write error: {e}"))?;

        for (rel_path, content) in &files {
            zip.start_file(rel_path, options)
                .map_err(|e| format!("Zip error: {e}"))?;
            zip.write_all(content.as_bytes())
                .map_err(|e| format!("Zip write error: {e}"))?;
        }

        for (rel_path, bytes) in &binary_attachments {
            zip.start_file(rel_path, options)
                .map_err(|e| format!("Zip error: {e}"))?;
            zip.write_all(bytes)
                .map_err(|e| format!("Zip write error: {e}"))?;
        }

        zip.finish().map_err(|e| format!("Zip finish error: {e}"))?;
    }

    let zip_bytes = buffer.into_inner();
    let zip_filename = format!("{}.zip", support_case_id);

    let form = reqwest::multipart::Form::new()
        .text("support_case_id", support_case_id.clone())
        .part(
            "file",
            reqwest::multipart::Part::bytes(zip_bytes)
                .file_name(zip_filename.clone())
                .mime_str("application/zip")
                .map_err(|e| format!("Multipart error: {e}"))?,
        );

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(35))
        .build()
        .map_err(|e| format!("HTTP client error: {e}"))?;

    let resp = client
        .post(&upload_url)
        .header("User-Agent", "hermes-bootstrap-installer/1.0")
        .header("X-Hermes-Reason", &payload.summary)
        .header("X-Hermes-Filename", &zip_filename)
        .multipart(form)
        .send()
        .await
        .map_err(|e| format!("Support-Upload fehlgeschlagen: {e}"))?;

    let status = resp.status();
    let body = resp.text().await.unwrap_or_default();

    if !status.is_success() {
        return Ok(SupportTicketResult {
            ok: false,
            reference_id: None,
            support_case_id: Some(support_case_id),
            error: Some(format!(
                "Server antwortete mit Status {}: {}",
                status,
                if body.trim().is_empty() { "Keine Fehlerdetails" } else { &body }
            )),
        });
    }

    let parsed: serde_json::Value = serde_json::from_str(&body).unwrap_or(serde_json::Value::Null);
    let ref_id = parsed
        .get("reference_id")
        .or_else(|| parsed.get("job_id"))
        .or_else(|| parsed.get("id"))
        .and_then(|v| v.as_str())
        .unwrap_or(&support_case_id)
        .to_string();

    let case_id = parsed
        .get("support_case_id")
        .or_else(|| parsed.get("case_id"))
        .and_then(|v| v.as_str())
        .unwrap_or(&support_case_id)
        .to_string();

    Ok(SupportTicketResult {
        ok: true,
        reference_id: Some(ref_id),
        support_case_id: Some(case_id),
        error: None,
    })
}
